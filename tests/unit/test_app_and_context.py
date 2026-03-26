from __future__ import annotations

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


def test_app_dry_run_with_fake_backend() -> None:
    assert main(["--root", str(ROOT), "--backend", "fake", "--dry-run"]) == 0


def test_goto_geofence_is_derived_from_artifact() -> None:
    service = build_context_service()

    result = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"}},
    )

    assert result.verdict == Verdict.ACT
    assert result.critical_slots["geofence.status"].value_json["target_inside"] is True
    assert result.critical_slots["geofence.status"].value_json["artifact_revision"] == 1


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
