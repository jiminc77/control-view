from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ActionState, Verdict
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


def build_goto_ready_service() -> ControlViewService:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot("estimator.health", {"score": 0.99})
    backend.set_slot("geofence.status", {"target_inside": True, "artifact_revision": 1})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot("nav.progress", {"phase": "IN_PROGRESS", "distance_m": 5.0, "speed_mps": 0.1})
    return ControlViewService(ROOT, backend=backend)


def test_goto_view_and_execute_guarded() -> None:
    service = build_goto_ready_service()

    view = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"}},
    )

    assert view.verdict == Verdict.ACT
    assert view.lease_token is not None

    exec_result = service.execute_guarded("GOTO", view.canonical_args, view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK
    tail = service.ledger_tail(last_n=10)
    assert tail["recent_events"]
    assert tail["recent_actions"]


def test_goto_blocks_non_map_frame() -> None:
    service = build_goto_ready_service()

    view = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "odom"}},
    )

    assert view.verdict == Verdict.SAFE_HOLD
    assert any(blocker.kind == "missing_frame_transform" for blocker in view.blockers)

