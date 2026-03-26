from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from control_view.backend.base import BackendAdapter, BackendSlotValue
from control_view.backend.fake_backend import FakeBackend
from control_view.backend.global_fix_provider import GlobalFixProvider
from control_view.backend.ros_mcp_debug_adapter import RosMcpDebugAdapter
from control_view.common.time import monotonic_ns
from control_view.common.types import EventType, Verdict
from control_view.common.utils import deep_get, distance_3d, point_in_polygon, stable_revision
from control_view.contracts.compiler import compile_bundle
from control_view.contracts.loader import load_contract_bundle
from control_view.contracts.models import (
    Blocker,
    ControlViewResult,
    ExecutionResult,
    LeaseToken,
    RefreshResult,
)
from control_view.runtime.blockers import make_blocker
from control_view.runtime.event_bus import EventBus
from control_view.runtime.executor import Executor
from control_view.runtime.governor import Governor
from control_view.runtime.lease import LeaseManager
from control_view.runtime.materializer import Materializer
from control_view.runtime.obligations import ObligationEngine
from control_view.storage.artifacts import ArtifactRepository
from control_view.storage.ledger import LedgerRepository
from control_view.storage.snapshots import SnapshotRepository
from control_view.storage.sqlite_store import SQLiteStore


class ControlViewService:
    def __init__(
        self,
        root: Path,
        *,
        backend: BackendAdapter | None = None,
        sqlite_path: str | Path = ":memory:",
        lease_secret: str | None = None,
    ) -> None:
        self.root = root
        self.bundle = load_contract_bundle(root)
        self.compiled = compile_bundle(self.bundle)
        self.store = SQLiteStore(sqlite_path)
        self.snapshots = SnapshotRepository(self.store)
        self.ledger = LedgerRepository(self.store)
        self.event_bus = EventBus(self.ledger)
        self.artifacts = ArtifactRepository(self.store)
        self.backend = backend or FakeBackend()
        self.debug_adapter = RosMcpDebugAdapter()
        self.materializer = Materializer(
            self.bundle.fields,
            self.backend,
            self.snapshots,
            self.event_bus,
        )
        self.governor = Governor(self.bundle.fields)
        self.obligations = ObligationEngine(self.store, event_bus=self.event_bus)
        self.global_fix_provider = GlobalFixProvider(self.backend)
        secret = lease_secret or os.environ.get(
            "CONTROL_VIEW_LEASE_SECRET",
            "control-view-dev-secret",
        )
        self.lease_manager = LeaseManager(secret)
        self.executor = Executor(
            backend=self.backend,
            event_bus=self.event_bus,
            store=self.store,
            governor=self.governor,
            obligations=self.obligations,
            evaluate_family=self._evaluate_family,
            materializer=self.materializer,
            compiled_specs=self.compiled,
            family_contracts=self.bundle.families,
            lease_manager=self.lease_manager,
        )
        self._load_artifacts()
        self._sync_system_slots()

    def _load_artifacts(self) -> None:
        geofence_path = self.root / "artifacts" / "geofence.yaml"
        if geofence_path.exists():
            geofence = yaml.safe_load(geofence_path.read_text())
            revision = int(geofence.get("revision", 1))
            self._upsert_artifact("geofence", revision, geofence)
        self._upsert_artifact("mission_spec", 0, {"revision": 0})

    def _sync_system_slots(self) -> None:
        tool_registry_revision = self._tool_registry_revision()
        self._upsert_artifact(
            "tool_registry",
            tool_registry_revision,
            {
                "revision": tool_registry_revision,
                "tools": self._tool_names(),
                "debug_capabilities": self.debug_adapter.probe_capabilities(set()),
            },
        )
        self._store_context_slot(
            "tool_registry.rev",
            {"value": tool_registry_revision},
        )
        self._store_context_slot(
            "mission.spec.rev",
            {"value": self._artifact_revision("mission_spec")},
        )

    def get_control_view(
        self,
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> ControlViewResult:
        return self._evaluate_family(family, proposed_args or {}, refresh=True)

    def refresh_control_view(
        self,
        *,
        family: str | None = None,
        slots: list[str] | None = None,
        proposed_args: dict[str, Any] | None = None,
    ) -> RefreshResult:
        if slots:
            self.materializer.refresh_slots(slots)
        if family:
            result = self._evaluate_family(family, proposed_args or {}, refresh=True)
            return RefreshResult(
                refreshed_slots=slots or self.compiled[family].required_slots,
                unresolved_blockers=result.blockers,
                new_verdict=result.verdict,
            )
        return RefreshResult(
            refreshed_slots=slots or [],
            unresolved_blockers=[],
            new_verdict=Verdict.REFRESH,
        )

    def execute_guarded(
        self,
        family: str,
        canonical_args: dict[str, Any],
        lease_token: LeaseToken,
    ) -> ExecutionResult:
        return self.executor.execute_guarded(family, canonical_args, lease_token)

    def explain_blockers(
        self,
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._evaluate_family(family, proposed_args or {}, refresh=True)
        return {
            "blockers": [item.model_dump(mode="json") for item in result.blockers],
            "refresh_hints": [item.refresh_hint for item in result.blockers],
            "suggested_safe_action": self.bundle.families[family].safe_hold_mapping.backend_action,
        }

    def ledger_tail(
        self,
        *,
        last_n: int = 20,
        since_mono_ns: int | None = None,
    ) -> dict[str, Any]:
        recent_events = (
            self.store.tail_events_since(since_mono_ns)
            if since_mono_ns is not None
            else self.store.tail_events(last_n)
        )
        recent_actions = (
            self.store.list_actions_since(since_mono_ns)
            if since_mono_ns is not None
            else self.store.list_actions(last_n)
        )
        return {
            "recent_events": [item.model_dump(mode="json") for item in recent_events],
            "recent_actions": [item.model_dump(mode="json") for item in recent_actions],
            "open_obligations": [
                item.model_dump(mode="json") for item in self.store.list_open_obligations()
            ],
            "artifact_revisions": self.artifacts.list_all(),
        }

    def _evaluate_family(
        self,
        family: str,
        proposed_args: dict[str, Any],
        *,
        refresh: bool,
        canonical_input: bool = False,
    ) -> ControlViewResult:
        contract = self.bundle.families[family]
        compiled = self.compiled[family]
        backend_context = self.backend.get_runtime_context()
        non_contextual_slots = [
            slot_id
            for slot_id in compiled.required_slots
            if slot_id not in {"geofence.status", "nav.progress"}
        ]
        evidence_map = (
            self.materializer.refresh_slots(non_contextual_slots)
            if refresh
            else self.snapshots.get_many(non_contextual_slots)
        )
        if canonical_input:
            canonical_args = proposed_args
            arg_blockers = []
        else:
            canonical_args, arg_blockers = self._canonicalize(family, proposed_args, evidence_map)
        evidence_map.update(
            self._materialize_contextual_slots(
                family,
                compiled.required_slots,
                canonical_args,
                evidence_map,
                backend_context=backend_context,
            )
        )
        if not refresh:
            evidence_map = {
                **self.snapshots.get_many(compiled.required_slots),
                **evidence_map,
            }
        open_obligations = self.obligations.reconcile(
            evidence_map,
            backend_context=backend_context,
        )
        evaluation = self.governor.evaluate(
            contract,
            compiled,
            evidence_map,
            canonical_args=canonical_args,
            open_obligations=open_obligations,
            extra_blockers=arg_blockers,
            backend_context=backend_context,
        )
        lease_token = None
        lease_expires_in_ms = None
        if evaluation.verdict == Verdict.ACT:
            lease_ms = self._lease_duration_ms(compiled.commit_guard_slots)
            now_ns = monotonic_ns()
            lease_token = self.lease_manager.issue(
                family,
                critical_slot_revisions={
                    slot_id: evidence_map[slot_id].revision
                    for slot_id in compiled.commit_guard_slots
                },
                canonical_args=canonical_args,
                issued_mono_ns=now_ns,
                expires_mono_ns=now_ns + (lease_ms * 1_000_000),
            )
            lease_expires_in_ms = lease_ms
        return ControlViewResult(
            family=family,
            verdict=evaluation.verdict,
            canonical_args=canonical_args,
            critical_slots=evaluation.critical_slots,
            support_slots=evaluation.support_slots,
            blockers=evaluation.blockers,
            open_obligations=open_obligations,
            lease_token=lease_token,
            lease_expires_in_ms=lease_expires_in_ms,
        )

    def _materialize_contextual_slots(
        self,
        family: str,
        required_slots: list[str],
        canonical_args: dict[str, Any],
        evidence_map: dict[str, Any],
        *,
        backend_context: dict[str, Any],
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        if "geofence.status" in required_slots and family == "GOTO":
            resolved["geofence.status"] = self._store_context_slot(
                "geofence.status",
                self._compute_geofence_status(canonical_args),
            )
        if "nav.progress" in required_slots:
            resolved["nav.progress"] = self._store_context_slot(
                "nav.progress",
                self._compute_nav_progress(family, canonical_args, evidence_map, backend_context),
            )
        if "tool_registry.rev" in required_slots:
            resolved["tool_registry.rev"] = self._store_context_slot(
                "tool_registry.rev",
                {"value": self._artifact_revision("tool_registry")},
            )
        if "mission.spec.rev" in required_slots:
            resolved["mission.spec.rev"] = self._store_context_slot(
                "mission.spec.rev",
                {"value": self._artifact_revision("mission_spec")},
            )
        return resolved

    def _store_context_slot(self, slot_id: str, value: dict[str, Any] | None) -> Any:
        raw_value = None
        if value is not None:
            raw_value = BackendSlotValue(
                value=value,
                authority_source="sidecar",
                frame_id=value.get("frame_id"),
            )
        return self.materializer.store_slot(slot_id, raw_value)

    def _compute_geofence_status(self, canonical_args: dict[str, Any]) -> dict[str, Any] | None:
        target_position = deep_get(canonical_args, "target_pose.position")
        if not target_position:
            return None
        geofence = self.artifacts.get("geofence")
        if not geofence:
            return {"target_inside": False, "artifact_revision": 0}
        polygons = geofence["payload"].get("polygons", [])
        target_inside = any(
            point_in_polygon(target_position, polygon.get("points", []))
            for polygon in polygons
        )
        return {
            "target_inside": target_inside,
            "artifact_revision": geofence["revision"],
            "frame_id": geofence["payload"].get("frame_id", "map"),
        }

    def _compute_nav_progress(
        self,
        family: str,
        canonical_args: dict[str, Any],
        evidence_map: dict[str, Any],
        backend_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        pose = evidence_map.get("pose.local")
        velocity = evidence_map.get("velocity.local")
        mode = evidence_map.get("vehicle.mode")
        if not pose or not pose.value_json or not mode or mode.value_json is None:
            return None

        target_pose = deep_get(canonical_args, "target_pose")
        if not target_pose:
            target_pose = deep_get(backend_context, "goto.active_target_pose")
        current_position = deep_get(pose.value_json, "position", {})
        speed_mps = self._vector_speed(deep_get(velocity.value_json, "linear", velocity.value_json))
        distance_m = distance_3d(
            current_position,
            deep_get(target_pose, "position"),
        )
        current_mode = str(mode.value_json.get("value", mode.value_json))

        if current_mode == "AUTO.LOITER" and speed_mps <= 0.3:
            phase = "HOLDING"
        elif (
            distance_m is not None
            and current_mode == "OFFBOARD"
            and distance_m <= 0.5
            and speed_mps <= 0.3
        ):
            phase = "ARRIVED"
        elif family == "GOTO" or target_pose:
            phase = "IN_PROGRESS"
        else:
            phase = "IDLE"
        return {
            "phase": phase,
            "distance_m": round(distance_m or 0.0, 3),
            "speed_mps": round(speed_mps, 3),
        }

    def _vector_speed(self, vector: dict[str, Any] | None) -> float:
        if not vector:
            return 0.0
        return (
            float(vector.get("x", 0.0)) ** 2
            + float(vector.get("y", 0.0)) ** 2
            + float(vector.get("z", 0.0)) ** 2
        ) ** 0.5

    def _tool_registry_revision(self) -> int:
        payload = {
            "families": sorted(self.bundle.families),
            "fields": sorted(self.bundle.fields),
            "tools": self._tool_names(),
            "debug_capabilities": self.debug_adapter.probe_capabilities(set()),
        }
        return stable_revision(payload)

    def _tool_names(self) -> list[str]:
        return [
            "control_view.get",
            "control_view.refresh",
            "action.execute_guarded",
            "control.explain_blockers",
            "ledger.tail",
        ]

    def _lease_duration_ms(self, slot_ids: list[str]) -> int:
        lease_values = [
            int(deep_get(self.bundle.fields[slot_id].freshness, "lease_ms", 250))
            for slot_id in slot_ids
        ]
        return min(lease_values) if lease_values else 250

    def _canonicalize(
        self,
        family: str,
        proposed_args: dict[str, Any],
        evidence_map: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Blocker]]:
        if family in {"ARM", "HOLD", "RTL", "LAND"}:
            if proposed_args:
                return {}, [self._arg_conflict_blocker(family, sorted(proposed_args))]
            return {}, []
        if family == "TAKEOFF":
            return self._canonicalize_takeoff(proposed_args, evidence_map)
        if family == "GOTO":
            return self._canonicalize_goto(proposed_args)
        return proposed_args, []

    def _canonicalize_takeoff(
        self,
        proposed_args: dict[str, Any],
        evidence_map: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Blocker]]:
        blockers: list[Blocker] = []
        server_controlled = {
            key for key in proposed_args if key in {"absolute_local_target_z", "current_geo_reference", "current_yaw", "frame_id"}
        }
        if server_controlled:
            return {}, [self._arg_conflict_blocker("TAKEOFF", sorted(server_controlled))]
        if "target_altitude" not in proposed_args:
            blockers.append(
                make_blocker(
                    slot_id="target_altitude",
                    kind="arg_missing",
                    severity="high",
                    message="takeoff requires target_altitude",
                    refreshable=False,
                    refresh_hint="provide target_altitude",
                )
            )
            return {}, blockers
        pose = evidence_map.get("pose.local")
        geo_reference = self.global_fix_provider.current_fix()
        current_yaw = self.global_fix_provider.current_yaw()
        if pose is None or pose.value_json is None:
            blockers.append(
                make_blocker(
                    slot_id="pose.local",
                    kind="arg_fill_missing_pose",
                    severity="high",
                    message="pose.local is required to fill absolute takeoff target",
                    refreshable=True,
                    refresh_hint="refresh pose.local",
                )
            )
        if geo_reference is None or current_yaw is None:
            blockers.append(
                make_blocker(
                    slot_id="takeoff.current_geo_reference",
                    kind="arg_fill_missing_geo",
                    severity="high",
                    message="current global fix and yaw are required for TAKEOFF request fill",
                    refreshable=True,
                    refresh_hint="refresh global fix provider",
                )
            )
        if blockers:
            return {}, blockers

        current_z = float(deep_get(pose.value_json, "position.z", 0.0))
        target_altitude = round(float(proposed_args["target_altitude"]), 3)
        return (
            {
                "target_altitude": target_altitude,
                "absolute_local_target_z": round(current_z + target_altitude, 3),
                "current_geo_reference": geo_reference,
                "current_yaw": round(float(current_yaw), 3),
                "frame_id": "map",
            },
            [],
        )

    def _canonicalize_goto(
        self,
        proposed_args: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Blocker]]:
        blockers: list[Blocker] = []
        server_controlled = {
            key for key in proposed_args if key in {"stream_rate_hz", "safe_hold_mode"}
        }
        if server_controlled:
            return {}, [self._arg_conflict_blocker("GOTO", sorted(server_controlled))]
        target_pose = proposed_args.get("target_pose")
        if not isinstance(target_pose, dict) or "position" not in target_pose:
            blockers.append(
                make_blocker(
                    slot_id="target_pose",
                    kind="arg_missing",
                    severity="high",
                    message="goto requires target_pose.position",
                    refreshable=False,
                    refresh_hint="provide target_pose.position",
                )
            )
            return {}, blockers

        frame_id = target_pose.get("frame_id", "map")
        if frame_id != "map":
            blockers.append(
                make_blocker(
                    slot_id="target_pose.frame_id",
                    kind="missing_frame_transform",
                    severity="high",
                    message="only map frame is accepted in the current implementation",
                    refreshable=False,
                    refresh_hint="provide target pose in map frame",
                )
            )
            return {}, blockers

        position = {
            key: round(float(value), 3)
            for key, value in target_pose["position"].items()
        }
        canonical = {
            "target_pose": {
                "position": position,
                "frame_id": "map",
            },
            "stream_rate_hz": 20.0,
        }
        if "yaw" in target_pose:
            canonical["target_pose"]["yaw"] = round(float(target_pose["yaw"]), 3)
        return canonical, []

    def _upsert_artifact(self, artifact_name: str, revision: int, payload: dict[str, Any]) -> None:
        previous = self.artifacts.get(artifact_name)
        self.artifacts.upsert(artifact_name, revision, payload)
        if previous != {"artifact_name": artifact_name, "revision": revision, "payload": payload}:
            self.event_bus.publish(
                EventType.CONFIG_REVISION,
                source="artifacts",
                payload_json={
                    "artifact_name": artifact_name,
                    "revision": revision,
                },
            )

    def _artifact_revision(self, artifact_name: str) -> int:
        artifact = self.artifacts.get(artifact_name)
        return int(artifact["revision"]) if artifact else 0

    def _arg_conflict_blocker(self, family: str, keys: list[str]) -> Blocker:
        joined = ", ".join(keys)
        return make_blocker(
            slot_id="arguments",
            kind="arg_conflict",
            severity="high",
            message=f"{family} received server-controlled or unsupported arguments: {joined}",
            refreshable=False,
            refresh_hint=f"remove {joined} from proposed_args",
        )
