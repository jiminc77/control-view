from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import Verdict
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


def test_arm_view_can_act_when_required_slots_are_valid() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})

    service = ControlViewService(ROOT, backend=backend)
    result = service.get_control_view("ARM")

    assert result.verdict == Verdict.ACT
    assert result.lease_token is not None


def test_takeoff_refreshes_when_global_fix_is_missing() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("pose.local", {"position": {"z": 1.0}, "frame_id": "map"})
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})

    service = ControlViewService(ROOT, backend=backend)
    result = service.get_control_view("TAKEOFF", {"target_altitude": 5.0})

    assert result.verdict == Verdict.REFRESH
    assert any(blocker.kind == "arg_fill_missing_geo" for blocker in result.blockers)


def test_goto_blocks_server_controlled_arg_override() -> None:
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
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot("vehicle.armed", True)

    service = ControlViewService(ROOT, backend=backend)
    result = service.get_control_view(
        "GOTO",
        {
            "target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"},
            "stream_rate_hz": 5.0,
        },
    )

    assert result.verdict == Verdict.SAFE_HOLD
    assert any(blocker.kind == "arg_conflict" for blocker in result.blockers)
