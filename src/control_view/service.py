from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from control_view.backend.base import BackendAdapter
from control_view.backend.fake_backend import FakeBackend
from control_view.backend.global_fix_provider import GlobalFixProvider
from control_view.common.time import monotonic_ns
from control_view.common.types import Verdict
from control_view.common.utils import deep_get
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
        self.materializer = Materializer(
            self.bundle.fields,
            self.backend,
            self.snapshots,
            self.event_bus,
        )
        self.governor = Governor(self.bundle.fields)
        self.obligations = ObligationEngine(self.store)
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

    def _load_artifacts(self) -> None:
        geofence_path = self.root / "artifacts" / "geofence.yaml"
        if geofence_path.exists():
            geofence = yaml.safe_load(geofence_path.read_text())
            revision = int(geofence.get("revision", 1))
            self.artifacts.upsert("geofence", revision, geofence)

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

    def ledger_tail(self, *, last_n: int = 20) -> dict[str, Any]:
        return {
            "recent_events": [
                item.model_dump(mode="json") for item in self.store.tail_events(last_n)
            ],
            "recent_actions": [
                item.model_dump(mode="json") for item in self.store.list_actions(last_n)
            ],
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
    ) -> ControlViewResult:
        contract = self.bundle.families[family]
        compiled = self.compiled[family]
        evidence_map = (
            self.materializer.refresh_slots(compiled.required_slots)
            if refresh
            else self.snapshots.get_many(compiled.required_slots)
        )
        open_obligations = self.obligations.reconcile(evidence_map)
        canonical_args, arg_blockers = self._canonicalize(family, proposed_args, evidence_map)
        evaluation = self.governor.evaluate(
            contract,
            compiled,
            evidence_map,
            canonical_args=canonical_args,
            open_obligations=open_obligations,
            extra_blockers=arg_blockers,
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
