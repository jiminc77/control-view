from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from control_view.backend.base import BackendAdapter, BackendSlotValue
from control_view.backend.fake_backend import FakeBackend
from control_view.backend.global_fix_provider import GlobalFixProvider
from control_view.backend.ros_mcp_debug_adapter import RosMcpDebugAdapter
from control_view.baselines import normalize_baseline_name
from control_view.common.time import monotonic_ns
from control_view.common.types import EventType, ValidState, Verdict
from control_view.common.utils import deep_get, distance_3d, point_in_polygon, stable_revision
from control_view.contracts.compiler import compile_bundle
from control_view.contracts.loader import load_contract_bundle
from control_view.contracts.models import (
    Blocker,
    ControlViewResult,
    EvidenceEntry,
    ExecutionResult,
    LeaseToken,
    ObligationRecord,
    RefreshResult,
)
from control_view.replay.recorder import ReplayRecorder
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
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self.root = root
        self.bundle = load_contract_bundle(root)
        self.compiled = compile_bundle(self.bundle)
        self.store = SQLiteStore(sqlite_path)
        self.snapshots = SnapshotRepository(self.store)
        self.ledger = LedgerRepository(self.store)
        self.event_bus = EventBus(self.ledger, recorder=recorder)
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
        self.obligations = ObligationEngine(
            self.store,
            event_bus=self.event_bus,
            recorder=recorder,
        )
        self.global_fix_provider = GlobalFixProvider(self.backend)
        self.recorder = recorder
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
            recorder=recorder,
        )
        self._load_artifacts()
        self._sync_system_slots()

    def _normalize_family_name(self, family: str) -> str:
        candidate = str(family).strip()
        if candidate in self.bundle.families:
            return candidate
        folded = candidate.replace("-", "_").replace(" ", "_").upper()
        if folded in self.bundle.families:
            return folded
        supported = ", ".join(sorted(self.bundle.families))
        raise ValueError(f"unknown family '{family}'. expected one of: {supported}")

    def _load_artifacts(self) -> None:
        self._sync_artifacts_from_disk()

    def _artifact_root(self) -> Path:
        override = os.environ.get("CONTROL_VIEW_ARTIFACTS_DIR")
        if override:
            return Path(override)
        return self.root / "artifacts"

    def _sync_artifacts_from_disk(self) -> None:
        artifact_root = self._artifact_root()
        artifact_specs = {
            "geofence": {
                "path": artifact_root / "geofence.yaml",
                "default_revision": 1,
                "default_payload": {"revision": 1, "polygons": []},
            },
            "mission_spec": {
                "path": artifact_root / "mission_spec.yaml",
                "default_revision": 0,
                "default_payload": {"revision": 0},
            },
        }
        for artifact_name, config in artifact_specs.items():
            path = config["path"]
            if path.exists():
                payload = yaml.safe_load(path.read_text()) or {}
            else:
                payload = deepcopy(config["default_payload"])
            revision = int(payload.get("revision", config["default_revision"]))
            self._upsert_artifact(artifact_name, revision, payload)

    def _sync_system_slots(self) -> None:
        debug_capabilities = self.debug_adapter.probe_runtime_capabilities()
        self.event_bus.publish(
            EventType.DEBUG_PROBE,
            source="debug_adapter",
            payload_json=debug_capabilities,
        )
        tool_registry_revision = self._tool_registry_revision(debug_capabilities)
        self._upsert_artifact(
            "tool_registry",
            tool_registry_revision,
            {
                "revision": tool_registry_revision,
                "tools": self._tool_names(),
                "debug_capabilities": debug_capabilities,
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
        family = self._normalize_family_name(family)
        self._sync_artifacts_from_disk()
        proposed = proposed_args or {}
        if self.recorder is not None:
            self.recorder.record_view_request(family, proposed)
        result = self._evaluate_family(family, proposed, refresh=True)
        if self.recorder is not None:
            payload = result.model_dump(mode="json")
            payload["artifact_revisions"] = self.artifacts.list_all()
            payload["decision_context"] = deepcopy(result.decision_context)
            payload["legacy_trace"] = False
            scenario_id = self._recorder_metadata_value("scenario_id")
            if scenario_id is not None:
                payload["scenario_id"] = scenario_id
            seed = self._recorder_metadata_value("seed")
            if seed is not None:
                payload["seed"] = seed
            ground_truth_source = self._recorder_metadata_value("ground_truth_source")
            if ground_truth_source is not None:
                payload["ground_truth_source"] = ground_truth_source
            self.recorder.record_view_result(family, payload)
            self.recorder.record_ledger_snapshot(self.ledger_tail(last_n=20))
        return result

    def refresh_control_view(
        self,
        *,
        family: str | None = None,
        slots: list[str] | None = None,
        proposed_args: dict[str, Any] | None = None,
    ) -> RefreshResult:
        self._sync_artifacts_from_disk()
        if slots:
            self.materializer.refresh_slots(slots)
        if family:
            family = self._normalize_family_name(family)
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
        family = self._normalize_family_name(family)
        if self.recorder is not None:
            self.recorder.record_execute_request(
                family,
                canonical_args,
                lease_token.model_dump(mode="json"),
            )
        result = self.executor.execute_guarded(family, canonical_args, lease_token)
        if self.recorder is not None:
            self.recorder.record_execution_result(family, result.model_dump(mode="json"))
            self.recorder.record_ledger_snapshot(self.ledger_tail(last_n=20))
        return result

    def explain_blockers(
        self,
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        family = self._normalize_family_name(family)
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
        self._refresh_open_obligations()
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

    def _refresh_open_obligations(self) -> None:
        open_records = self.store.list_open_obligations()
        for record in open_records:
            canonical_args = record.notes.get("canonical_args", {}) if record.notes else {}
            if not isinstance(canonical_args, dict):
                canonical_args = {}
            self._evaluate_family(
                record.family,
                canonical_args,
                refresh=True,
                canonical_input=True,
            )

    def _evaluate_family(
        self,
        family: str,
        proposed_args: dict[str, Any],
        *,
        refresh: bool,
        canonical_input: bool = False,
    ) -> ControlViewResult:
        family = self._normalize_family_name(family)
        contract = self.bundle.families[family]
        compiled = self.compiled[family]
        canonical_args: dict[str, Any] = {}
        arg_blockers: list[Blocker] = []
        contextual_slot_ids = {
            "geofence.status",
            "nav.progress",
            "tool_registry.rev",
            "mission.spec.rev",
        }
        fill_context = {
            "global_fix": self.global_fix_provider.current_fix(),
            "current_yaw": self.global_fix_provider.current_yaw(),
        }
        if family == "GOTO":
            if canonical_input:
                canonical_args = proposed_args
            else:
                canonical_args, arg_blockers = self._canonicalize_goto(proposed_args)
        non_contextual_slots = [
            slot_id
            for slot_id in compiled.required_slots
            if slot_id not in contextual_slot_ids
        ]
        evidence_map = (
            self.materializer.refresh_slots(non_contextual_slots)
            if refresh
            else self.snapshots.get_many(non_contextual_slots)
        )
        evidence_map.update(
            self._context_dependencies(
                compiled.required_slots,
                evidence_map,
                refresh=refresh,
            )
        )
        evidence_map.update(
            self._refresh_derived_required_slots(
                compiled.required_slots,
                evidence_map,
                refresh=refresh,
            )
        )
        if family == "GOTO" and canonical_args and not arg_blockers:
            canonical_args, fill_blockers = self._fill_goto_args(canonical_args, evidence_map)
            arg_blockers.extend(fill_blockers)
        if family == "GOTO" and canonical_args and not arg_blockers:
            canonical_args = self._enrich_goto_args(canonical_args, evidence_map)
            self.backend.prepare_control_view(family, canonical_args)
            if refresh:
                evidence_map = self.materializer.refresh_slots(non_contextual_slots)
                evidence_map.update(
                    self._context_dependencies(
                        compiled.required_slots,
                        evidence_map,
                        refresh=refresh,
                    )
                )
                evidence_map.update(
                    self._refresh_derived_required_slots(
                        compiled.required_slots,
                        evidence_map,
                        refresh=refresh,
                    )
                )
                canonical_args = self._enrich_goto_args(canonical_args, evidence_map)
        if family != "GOTO":
            if canonical_input:
                canonical_args = proposed_args
            else:
                canonical_args, arg_blockers = self._canonicalize(
                    family,
                    proposed_args,
                    evidence_map,
                    fill_context=fill_context,
                )
            if family == "LAND" and not arg_blockers:
                canonical_args = self._enrich_land_args(canonical_args, evidence_map)
            self.backend.prepare_control_view(family, canonical_args)
        backend_context = deepcopy(self.backend.get_runtime_context())
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
        evaluation_mono_ns = monotonic_ns()
        commit_guard_revisions: dict[str, int] = {}
        if evaluation.verdict == Verdict.ACT:
            lease_ms = self._lease_duration_ms(compiled.commit_guard_slots)
            revision_slots = self._lease_revision_slots(compiled.commit_guard_slots)
            commit_guard_revisions = {
                slot_id: evidence_map[slot_id].revision
                for slot_id in revision_slots
                if slot_id in evidence_map
            }
            lease_token = self.lease_manager.issue(
                family,
                critical_slot_revisions=commit_guard_revisions,
                canonical_args=canonical_args,
                issued_mono_ns=evaluation_mono_ns,
                expires_mono_ns=evaluation_mono_ns + (lease_ms * 1_000_000),
            )
            lease_expires_in_ms = lease_ms
        decision_context = self._build_decision_context(
            family=family,
            proposed_args=proposed_args,
            canonical_args=canonical_args,
            evidence_map=evidence_map,
            open_obligations=open_obligations,
            evaluation_mono_ns=evaluation_mono_ns,
            backend_context=backend_context,
            fill_context=fill_context,
        )
        return ControlViewResult(
            family=family,
            verdict=evaluation.verdict,
            canonical_args=canonical_args,
            critical_slots=evaluation.critical_slots,
            support_slots=evaluation.support_slots,
            blockers=evaluation.blockers,
            open_obligations=open_obligations,
            commit_guard_slots=list(compiled.commit_guard_slots),
            commit_guard_revisions=commit_guard_revisions,
            decision_context=decision_context,
            lease_token=lease_token,
            lease_expires_in_ms=lease_expires_in_ms,
        )

    def replay_view_result(
        self,
        family: str,
        decision_context: dict[str, Any],
        *,
        baseline: str | None = None,
        slot_ablation: list[str] | None = None,
        b2_ttl_sec: float = 5.0,
        cached_slots: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        family = self._normalize_family_name(family)
        normalized_baseline = normalize_baseline_name(baseline)
        compiled = self.compiled[family]
        contract = self.bundle.families[family]
        evaluation_mono_ns = int(decision_context.get("evaluation_mono_ns") or monotonic_ns())
        evidence_source = (
            cached_slots
            if normalized_baseline == "B2" and cached_slots is not None
            else decision_context.get("evidence_map", {})
        )
        evidence_map = self._evidence_entries_from_payload(evidence_source)
        for slot_id in slot_ablation or []:
            evidence_map.pop(slot_id, None)
        canonical_args, arg_blockers = self._canonicalize_from_decision_context(
            family,
            decision_context,
            evidence_map,
        )
        open_obligations = (
            []
            if normalized_baseline in {"B1", "B2"}
            else [
                ObligationRecord.model_validate(item)
                for item in decision_context.get("open_obligations", [])
            ]
        )
        backend_context = deepcopy(decision_context.get("backend_context", {}))
        evaluation = self.governor.evaluate(
            contract,
            compiled,
            evidence_map,
            canonical_args=canonical_args,
            open_obligations=open_obligations,
            extra_blockers=arg_blockers,
            backend_context=backend_context,
            now_mono_ns=evaluation_mono_ns,
            include_pending_transition_blocker=normalized_baseline == "B3",
            validity_resolver=lambda field, entry, risk_class, now_ns: self._baseline_validity(
                baseline=normalized_baseline,
                field=field,
                entry=entry,
                risk_class=risk_class,
                now_mono_ns=now_ns,
                b2_ttl_sec=b2_ttl_sec,
            ),
        )
        lease_token = None
        lease_expires_in_ms = None
        commit_guard_revisions: dict[str, int] = {}
        if evaluation.verdict == Verdict.ACT:
            lease_expires_in_ms = self._lease_duration_ms(compiled.commit_guard_slots)
            revision_slots = self._lease_revision_slots(compiled.commit_guard_slots)
            commit_guard_revisions = {
                slot_id: evidence_map[slot_id].revision
                for slot_id in revision_slots
                if slot_id in evidence_map
            }
            if normalized_baseline == "B3":
                lease_token = self.lease_manager.issue(
                    family,
                    critical_slot_revisions=commit_guard_revisions,
                    canonical_args=canonical_args,
                    issued_mono_ns=evaluation_mono_ns,
                    expires_mono_ns=evaluation_mono_ns + (lease_expires_in_ms * 1_000_000),
                )
        result = ControlViewResult(
            family=family,
            verdict=evaluation.verdict,
            canonical_args=canonical_args,
            critical_slots=evaluation.critical_slots,
            support_slots=evaluation.support_slots,
            blockers=evaluation.blockers,
            open_obligations=open_obligations,
            commit_guard_slots=list(compiled.commit_guard_slots),
            commit_guard_revisions=commit_guard_revisions,
            lease_token=lease_token,
            lease_expires_in_ms=lease_expires_in_ms,
        )
        payload = result.model_dump(mode="json")
        payload["decision_context"] = deepcopy(decision_context)
        payload["ground_truth_source"] = decision_context.get(
            "ground_truth_source",
            "decision_context",
        )
        payload["legacy_trace"] = False
        if slot_ablation:
            payload["ablated_slots"] = sorted(set(slot_ablation))
        payload["policy_swap"] = normalized_baseline
        return payload

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

    def _context_dependencies(
        self,
        required_slots: list[str],
        evidence_map: dict[str, Any],
        *,
        refresh: bool,
    ) -> dict[str, Any]:
        dependency_ids: list[str] = []
        for slot_id in required_slots:
            field = self.bundle.fields[slot_id]
            dependencies = (
                list(field.derivation.get("dependencies", []))
                if field.derivation
                else []
            )
            for dependency in dependencies:
                if dependency not in evidence_map and dependency not in dependency_ids:
                    dependency_ids.append(dependency)
        if not dependency_ids:
            return {}
        if refresh:
            return self.materializer.refresh_slots(dependency_ids)
        return self.snapshots.get_many(dependency_ids)

    def _refresh_derived_required_slots(
        self,
        required_slots: list[str],
        evidence_map: dict[str, Any],
        *,
        refresh: bool,
    ) -> dict[str, Any]:
        if not refresh:
            return {}
        derived_slots = [
            slot_id
            for slot_id in required_slots
            if slot_id
            not in {
                "geofence.status",
                "nav.progress",
                "tool_registry.rev",
                "mission.spec.rev",
            }
            and self.bundle.fields[slot_id].derivation
        ]
        if not derived_slots:
            return {}
        return self.materializer.derive_slots(derived_slots, evidence_map)

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

        if (
            distance_m is not None
            and distance_m <= 0.5
            and speed_mps <= 0.3
        ):
            phase = "ARRIVED"
        elif current_mode in {"AUTO.LOITER", "AUTO.TAKEOFF"} and speed_mps <= 0.3:
            phase = "HOLDING"
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

    def _tool_registry_revision(self, debug_capabilities: dict[str, Any]) -> int:
        payload = {
            "families": sorted(self.bundle.families),
            "fields": sorted(self.bundle.fields),
            "tools": self._tool_names(),
            "debug_capabilities": debug_capabilities,
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

    def _lease_revision_slots(self, slot_ids: list[str]) -> list[str]:
        return [
            slot_id
            for slot_id in slot_ids
            if self.bundle.fields[slot_id].revision_rule != "increment_on_every_accepted_sample"
            and slot_id != "offboard.stream.ok"
        ]

    def _canonicalize(
        self,
        family: str,
        proposed_args: dict[str, Any],
        evidence_map: dict[str, Any],
        *,
        fill_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[Blocker]]:
        if family in {"ARM", "HOLD", "RTL", "LAND"}:
            if proposed_args:
                return {}, [self._arg_conflict_blocker(family, sorted(proposed_args))]
            return {}, []
        if family == "TAKEOFF":
            return self._canonicalize_takeoff(
                proposed_args,
                evidence_map,
                fill_context=fill_context,
            )
        if family == "GOTO":
            return self._canonicalize_goto(proposed_args)
        return proposed_args, []

    def _canonicalize_takeoff(
        self,
        proposed_args: dict[str, Any],
        evidence_map: dict[str, Any],
        *,
        fill_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[Blocker]]:
        blockers: list[Blocker] = []
        server_controlled = {
            key
            for key in proposed_args
            if key
            in {
                "absolute_local_target_z",
                "current_geo_reference",
                "current_yaw",
                "frame_id",
            }
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
        geo_reference = (fill_context or {}).get("global_fix")
        current_yaw = (fill_context or {}).get("current_yaw")
        if geo_reference is None:
            geo_reference = self.global_fix_provider.current_fix()
        if current_yaw is None:
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
            key
            for key in proposed_args
            if key in {"stream_rate_hz", "safe_hold_mode", "planned_distance_m", "nav_timeout_sec"}
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

        if "x" not in target_pose["position"] or "y" not in target_pose["position"]:
            blockers.append(
                make_blocker(
                    slot_id="target_pose.position",
                    kind="arg_missing",
                    severity="high",
                    message="goto requires target_pose.position.x and target_pose.position.y",
                    refreshable=False,
                    refresh_hint="provide target_pose.position.x and target_pose.position.y",
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

    def _fill_goto_args(
        self,
        canonical_args: dict[str, Any],
        evidence_map: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Blocker]]:
        position = dict(deep_get(canonical_args, "target_pose.position", {}) or {})
        if "z" in position:
            return canonical_args, []
        current_z = deep_get(evidence_map.get("pose.local"), "value_json.position.z")
        if current_z is None:
            return (
                canonical_args,
                [
                    make_blocker(
                        slot_id="pose.local",
                        kind="arg_fill_missing_z",
                        severity="high",
                        message="pose.local is required to fill goto target altitude",
                        refreshable=True,
                        refresh_hint="refresh pose.local",
                    )
                ],
            )
        position["z"] = round(float(current_z), 3)
        return (
            {
                **canonical_args,
                "target_pose": {
                    **deep_get(canonical_args, "target_pose", {}),
                    "position": position,
                },
            },
            [],
        )

    def _enrich_goto_args(
        self,
        canonical_args: dict[str, Any],
        evidence_map: dict[str, Any],
    ) -> dict[str, Any]:
        target_position = deep_get(canonical_args, "target_pose.position")
        current_position = deep_get(evidence_map.get("pose.local"), "value_json.position")
        planned_distance_m = distance_3d(current_position, target_position) or 0.0
        enriched = {
            **canonical_args,
            "planned_distance_m": round(planned_distance_m, 3),
            "nav_timeout_sec": round(max(10.0, (2.0 * planned_distance_m) + 5.0), 3),
        }
        return enriched

    def _enrich_land_args(
        self,
        canonical_args: dict[str, Any],
        evidence_map: dict[str, Any],
    ) -> dict[str, Any]:
        current_z = abs(
            float(deep_get(evidence_map.get("pose.local"), "value_json.position.z", 0.0))
        )
        land_timeout_sec = round(max(30.0, (current_z / 1.2) + 12.0), 3)
        return {
            **canonical_args,
            "land_timeout_sec": land_timeout_sec,
        }

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
            if self.recorder is not None:
                self.recorder.record_artifact_revision(artifact_name, revision)

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

    def _recorder_metadata_value(self, key: str) -> Any | None:
        if self.recorder is None:
            return None
        return self.recorder.default_metadata.get(key)

    def _build_decision_context(
        self,
        *,
        family: str,
        proposed_args: dict[str, Any],
        canonical_args: dict[str, Any],
        evidence_map: dict[str, EvidenceEntry],
        open_obligations: list[ObligationRecord],
        evaluation_mono_ns: int,
        backend_context: dict[str, Any],
        fill_context: dict[str, Any],
    ) -> dict[str, Any]:
        compiled = self.compiled[family]
        artifact_revisions = self.artifacts.list_all()
        return {
            "family": family,
            "proposed_args": deepcopy(proposed_args),
            "canonical_args": deepcopy(canonical_args),
            "evaluation_mono_ns": evaluation_mono_ns,
            "required_slots": list(compiled.required_slots),
            "role_partition": deepcopy(compiled.role_partition),
            "commit_guard_slots": list(compiled.commit_guard_slots),
            "commit_guard_revisions": {
                slot_id: evidence_map[slot_id].revision
                for slot_id in self._lease_revision_slots(compiled.commit_guard_slots)
                if slot_id in evidence_map
            },
            "evidence_map": {
                slot_id: entry.model_dump(mode="json")
                for slot_id, entry in evidence_map.items()
            },
            "backend_context": deepcopy(backend_context),
            "open_obligations": [item.model_dump(mode="json") for item in open_obligations],
            "artifact_revisions": deepcopy(artifact_revisions),
            "artifact_revision_map": {
                str(item["artifact_name"]): int(item["revision"])
                for item in artifact_revisions
                if item.get("artifact_name") is not None and item.get("revision") is not None
            },
            "fill_context": deepcopy(fill_context),
            "scenario_id": self._recorder_metadata_value("scenario_id"),
            "seed": self._recorder_metadata_value("seed"),
            "ground_truth_source": self._recorder_metadata_value("ground_truth_source")
            or "decision_context",
        }

    def _evidence_entries_from_payload(
        self,
        payload: dict[str, Any],
    ) -> dict[str, EvidenceEntry]:
        evidence_map: dict[str, EvidenceEntry] = {}
        for slot_id, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            evidence_map[str(slot_id)] = EvidenceEntry.model_validate(
                {"slot_id": str(slot_id), **entry}
            )
        return evidence_map

    def _canonicalize_from_decision_context(
        self,
        family: str,
        decision_context: dict[str, Any],
        evidence_map: dict[str, EvidenceEntry],
    ) -> tuple[dict[str, Any], list[Blocker]]:
        proposed_args = deepcopy(decision_context.get("proposed_args") or {})
        fill_context = deepcopy(decision_context.get("fill_context") or {})
        if family == "GOTO":
            canonical_args, blockers = self._canonicalize_goto(proposed_args)
            if blockers:
                return {}, blockers
            return self._enrich_goto_args(canonical_args, evidence_map), []
        if family == "LAND":
            canonical_args, blockers = self._canonicalize(
                family,
                proposed_args,
                evidence_map,
                fill_context=fill_context,
            )
            if blockers:
                return {}, blockers
            return self._enrich_land_args(canonical_args, evidence_map), []
        return self._canonicalize(
            family,
            proposed_args,
            evidence_map,
            fill_context=fill_context,
        )

    def _baseline_validity(
        self,
        *,
        baseline: str,
        field,
        entry: EvidenceEntry | None,
        risk_class: str,
        now_mono_ns: int,
        b2_ttl_sec: float,
    ) -> ValidState:
        if entry is None:
            return ValidState.MISSING
        if baseline == "B3":
            return self.governor._resolve_valid_state(  # noqa: SLF001
                field,
                entry,
                risk_class,
                now_mono_ns=now_mono_ns,
            )
        if entry.value_json is None:
            return ValidState.MISSING
        if baseline == "B2":
            age_ms = (now_mono_ns - entry.received_mono_ns) / 1_000_000
            if age_ms > float(b2_ttl_sec) * 1000.0:
                return ValidState.STALE
        return ValidState.VALID
