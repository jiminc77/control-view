from __future__ import annotations

import json
import time
from pathlib import Path

from control_view.app import main
from control_view.backend.fake_backend import FakeBackend
from control_view.common.time import monotonic_ns
from control_view.common.types import EventType, Verdict
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


def build_context_service() -> ControlViewService:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot("velocity.local", {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}})
    backend.set_slot("estimator.health", {"score": 0.99})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    return ControlViewService(ROOT, backend=backend)


class SlowWarmupBackend(FakeBackend):
    def prepare_control_view(
        self,
        family: str,
        canonical_args: dict[str, object] | None = None,
    ) -> None:
        if family == "GOTO":
            time.sleep(0.35)


def test_app_dry_run_with_fake_backend() -> None:
    assert main(["--root", str(ROOT), "--backend", "fake", "--dry-run"]) == 0


def test_app_dry_run_can_write_replay_jsonl(tmp_path) -> None:
    target = tmp_path / "dry_run.jsonl"

    assert (
        main(
            [
                "--root",
                str(ROOT),
                "--backend",
                "fake",
                "--record-jsonl",
                str(target),
                "--dry-run",
            ]
        )
        == 0
    )
    assert target.exists()
    assert all(json.loads(line) for line in target.read_text().splitlines())


def test_goto_geofence_is_derived_from_artifact() -> None:
    service = build_context_service()

    result = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"}},
    )

    assert result.verdict == Verdict.ACT
    assert result.critical_slots["geofence.status"].value_json["target_inside"] is True
    assert result.critical_slots["geofence.status"].value_json["artifact_revision"] == 1


def test_goto_accepts_legacy_offboard_ok_shape() -> None:
    service = build_context_service()
    service.backend.set_slot(
        "offboard.stream.ok",
        {
            "ok": True,
            "publish_rate_hz": 20.0,
            "last_publish_age_ms": 10.0,
            "warmup_elapsed_ms": 1000.0,
        },
    )

    result = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"}},
    )

    assert result.verdict == Verdict.ACT
    assert result.critical_slots["offboard.stream.ok"].value_json["value"] is True
    assert "ok" not in result.critical_slots["offboard.stream.ok"].value_json


def test_hold_can_materialize_nav_progress_from_pose_dependency() -> None:
    service = build_context_service()
    service.backend.set_slot("vehicle.mode", "AUTO.LOITER")

    service.get_control_view("HOLD")
    nav_progress = service.snapshots.get("nav.progress")

    assert nav_progress is not None
    assert nav_progress.value_json["phase"] == "HOLDING"


def test_hold_treats_auto_takeoff_hover_as_holding_phase() -> None:
    service = build_context_service()
    service.backend.set_slot("vehicle.mode", "AUTO.TAKEOFF")

    service.get_control_view("HOLD")
    nav_progress = service.snapshots.get("nav.progress")

    assert nav_progress is not None
    assert nav_progress.value_json["phase"] == "HOLDING"


def test_goto_treats_near_target_hover_as_arrived_phase() -> None:
    service = build_context_service()
    service.backend.set_slot("vehicle.mode", "AUTO.LOITER")

    service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 0.0, "y": 0.0, "z": 2.0}, "frame_id": "map"}},
    )
    nav_progress = service.snapshots.get("nav.progress")

    assert nav_progress is not None
    assert nav_progress.value_json["phase"] == "ARRIVED"


def test_goto_refreshes_guard_slots_after_warmup_delay() -> None:
    backend = SlowWarmupBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot("velocity.local", {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}})
    backend.set_slot("estimator.health", {"score": 0.99})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    service = ControlViewService(ROOT, backend=backend)

    result = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}, "frame_id": "map"}},
    )

    assert result.verdict == Verdict.ACT


def test_rtl_can_materialize_home_ready_from_home_position_dependency() -> None:
    service = build_context_service()
    service.backend.set_slot(
        "home.position",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "geo": {"latitude": 47.0, "longitude": 8.0, "altitude": 47.0},
            "frame_id": "map",
        },
        frame_id="map",
    )

    result = service.get_control_view("RTL")

    assert result.verdict == Verdict.ACT
    assert result.critical_slots["home.ready"].value_json["ready"] is True


def test_ledger_tail_can_filter_since_mono_ns() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    service = ControlViewService(ROOT, backend=backend)

    marker = monotonic_ns()
    service.get_control_view("ARM")
    recent = service.ledger_tail(since_mono_ns=marker)

    assert recent["recent_events"]


def test_goto_canonical_args_include_dynamic_nav_timeout() -> None:
    service = build_context_service()

    result = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 10.0, "y": 0.0, "z": 2.0}, "frame_id": "map"}},
    )

    assert result.verdict == Verdict.ACT
    assert result.canonical_args["planned_distance_m"] == 10.0
    assert result.canonical_args["nav_timeout_sec"] == 25.0


def test_debug_probe_updates_tool_registry_artifact(monkeypatch) -> None:
    def fake_probe(_self):
        return {
            "role": "read_only_out_of_band_introspection",
            "required_services_ok": True,
            "missing_required_services": [],
            "optional_action_services_present": ["/rosapi/action_servers"],
            "actions_supported": True,
            "available_services": ["/rosapi/services"],
        }

    monkeypatch.setattr(
        "control_view.backend.ros_mcp_debug_adapter.RosMcpDebugAdapter.probe_runtime_capabilities",
        fake_probe,
    )
    service = ControlViewService(ROOT, backend=FakeBackend())

    tool_registry = service.artifacts.get("tool_registry")
    assert tool_registry is not None
    assert tool_registry["payload"]["debug_capabilities"]["required_services_ok"] is True
    assert any(
        event.event_type == EventType.DEBUG_PROBE for event in service.store.tail_events(last_n=10)
    )


def test_land_canonical_args_include_dynamic_timeout() -> None:
    service = build_context_service()
    service.backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 42.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )

    result = service.get_control_view("LAND")

    assert result.verdict == Verdict.ACT
    assert result.canonical_args["land_timeout_sec"] == 47.0
