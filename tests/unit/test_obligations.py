from __future__ import annotations

from pathlib import Path

import pytest

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ActionState
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, initial_ns: int = 0) -> None:
        self.value = initial_ns

    def now(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = value


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr("control_view.service.monotonic_ns", clock.now)
    monkeypatch.setattr("control_view.runtime.executor.monotonic_ns", clock.now)
    monkeypatch.setattr("control_view.runtime.obligations.monotonic_ns", clock.now)
    monkeypatch.setattr("control_view.runtime.materializer.monotonic_ns", clock.now)
    monkeypatch.setattr("control_view.runtime.governor.monotonic_ns", clock.now)
    return clock


def test_arm_obligation_closes_after_vehicle_is_armed(fake_clock: FakeClock) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_action_result("ARM", state=ActionState.ACKED_STRONG)

    service = ControlViewService(ROOT, backend=backend)
    arm_view = service.get_control_view("ARM")
    exec_result = service.execute_guarded("ARM", arm_view.canonical_args, arm_view.lease_token)

    assert exec_result.status == ActionState.ACKED_STRONG
    assert len(exec_result.opened_obligation_ids) == 1

    backend.set_slot("vehicle.armed", True)
    fake_clock.set(400_000_000)
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    service.get_control_view("HOLD")
    fake_clock.set(800_000_000)
    service.get_control_view("HOLD")

    assert service.store.list_open_obligations() == []
    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.CONFIRMED


def test_arm_obligation_expires_without_confirmation(fake_clock: FakeClock) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_action_result("ARM", state=ActionState.ACKED_STRONG)

    service = ControlViewService(ROOT, backend=backend)
    arm_view = service.get_control_view("ARM")
    exec_result = service.execute_guarded("ARM", arm_view.canonical_args, arm_view.lease_token)

    fake_clock.set(3_500_000_000)
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    service.get_control_view("HOLD")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.EXPIRED


def test_takeoff_obligation_fails_if_vehicle_disarms_before_confirm(
    fake_clock: FakeClock,
) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("pose.local", {"position": {"z": 1.0}, "frame_id": "map"})
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_global_fix({"latitude": 1.0, "longitude": 2.0, "altitude": 3.0})
    backend.set_current_yaw(0.0)
    backend.set_action_result("TAKEOFF", state=ActionState.ACKED_STRONG)

    service = ControlViewService(ROOT, backend=backend)
    takeoff_view = service.get_control_view("TAKEOFF", {"target_altitude": 3.0})
    exec_result = service.execute_guarded(
        "TAKEOFF",
        takeoff_view.canonical_args,
        takeoff_view.lease_token,
    )

    assert exec_result.status == ActionState.ACKED_STRONG

    backend.set_slot("vehicle.armed", False)
    fake_clock.set(200_000_000)
    service.get_control_view("HOLD")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.FAILED
    assert '"vehicle.armed == false"' in action.failure_reason_codes[0]


def test_land_obligation_waits_for_overall_timeout_if_touchdown_does_not_disarm(
    fake_clock: FakeClock,
) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "AUTO.LOITER")
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 0.2},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_action_result("LAND", state=ActionState.ACKED_WEAK)
    backend.set_runtime_context({"signals": {}, "land": {"on_ground": True}})

    service = ControlViewService(ROOT, backend=backend)
    land_view = service.get_control_view("LAND")
    exec_result = service.execute_guarded("LAND", land_view.canonical_args, land_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    fake_clock.set(6_500_000_000)
    service.get_control_view("ARM")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.ACKED_WEAK

    fake_clock.set(11_700_000_000)
    service.get_control_view("ARM")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.ACKED_WEAK

    fake_clock.set(31_100_000_000)
    service.get_control_view("ARM")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.EXPIRED


def test_land_obligation_confirms_after_delayed_disarm_before_timeout(
    fake_clock: FakeClock,
) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "AUTO.LOITER")
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 0.2},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_action_result("LAND", state=ActionState.ACKED_WEAK)
    backend.set_runtime_context({"signals": {}, "land": {"on_ground": True}})

    service = ControlViewService(ROOT, backend=backend)
    land_view = service.get_control_view("LAND")
    exec_result = service.execute_guarded("LAND", land_view.canonical_args, land_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    fake_clock.set(11_700_000_000)
    backend.set_slot("vehicle.armed", False)
    service.get_control_view("ARM")

    fake_clock.set(12_800_000_000)
    service.get_control_view("ARM")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.CONFIRMED


def test_rtl_obligation_allows_delayed_mode_confirmation(fake_clock: FakeClock) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "OFFBOARD")
    backend.set_slot("home.position", {"position": {"x": 0.0, "y": 0.0, "z": 0.0}})
    backend.set_action_result("RTL", state=ActionState.ACKED_WEAK)

    service = ControlViewService(ROOT, backend=backend)
    rtl_view = service.get_control_view("RTL")
    exec_result = service.execute_guarded("RTL", rtl_view.canonical_args, rtl_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    fake_clock.set(3_000_000_000)
    backend.set_slot("vehicle.mode", "AUTO.RTL")
    service.get_control_view("ARM")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.CONFIRMED


def test_ledger_tail_reconciles_takeoff_obligation(fake_clock: FakeClock) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "AUTO.TAKEOFF")
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 1.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_global_fix({"latitude": 1.0, "longitude": 2.0, "altitude": 3.0})
    backend.set_current_yaw(0.0)
    backend.set_action_result("TAKEOFF", state=ActionState.ACKED_STRONG)

    service = ControlViewService(ROOT, backend=backend)
    takeoff_view = service.get_control_view("TAKEOFF", {"target_altitude": 3.0})
    exec_result = service.execute_guarded(
        "TAKEOFF",
        takeoff_view.canonical_args,
        takeoff_view.lease_token,
    )

    assert exec_result.status == ActionState.ACKED_STRONG
    assert service.store.list_open_obligations() != []

    backend.set_runtime_context(
        {
            "signals": {},
            "takeoff": {"airborne": True, "altitude_reached": True},
            "land": {"on_ground": False},
            "goto": {"active_target_pose": None, "distance_m": None, "arrived": False},
        }
    )
    fake_clock.set(600_000_000)

    service.ledger_tail(last_n=5)
    fake_clock.set(1_200_000_000)
    tail = service.ledger_tail(last_n=5)

    assert tail["open_obligations"] == []
    assert tail["recent_actions"][0]["action_id"] == exec_result.action_id
    assert tail["recent_actions"][0]["state"] == ActionState.CONFIRMED.value


def test_goto_obligation_closes_after_arrival_even_in_loiter(
    fake_clock: FakeClock,
) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "OFFBOARD")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    backend.set_action_result("GOTO", state=ActionState.ACKED_WEAK)
    backend.set_runtime_context(
        {
            "signals": {"OFFBOARD_lost_before_arrival": False},
            "goto": {
                "active_target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}},
                "distance_m": 1.0,
                "arrived": False,
            },
            "land": {"on_ground": False},
        }
    )

    service = ControlViewService(ROOT, backend=backend)
    goto_view = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}, "frame_id": "map"}},
    )
    exec_result = service.execute_guarded("GOTO", goto_view.canonical_args, goto_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    backend.set_slot("vehicle.mode", "AUTO.LOITER")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_runtime_context(
        {
            "signals": {"OFFBOARD_lost_before_arrival": False},
            "goto": {
                "active_target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}},
                "distance_m": 0.0,
                "arrived": True,
            },
            "land": {"on_ground": False},
        }
    )
    fake_clock.set(600_000_000)

    service.ledger_tail(last_n=5)
    fake_clock.set(1_200_000_000)
    tail = service.ledger_tail(last_n=5)

    assert tail["open_obligations"] == []
    assert tail["recent_actions"][0]["action_id"] == exec_result.action_id
    assert tail["recent_actions"][0]["state"] == ActionState.CONFIRMED.value


def test_goto_confirmation_beats_timeout_once_arrival_is_observed(
    fake_clock: FakeClock,
) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "OFFBOARD")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.3, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    backend.set_action_result("GOTO", state=ActionState.ACKED_WEAK)
    backend.set_runtime_context(
        {
            "signals": {"OFFBOARD_lost_before_arrival": False},
            "goto": {
                "active_target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}},
                "distance_m": 1.0,
                "arrived": False,
            },
            "land": {"on_ground": False},
        }
    )

    service = ControlViewService(ROOT, backend=backend)
    goto_view = service.get_control_view(
        "GOTO",
        {"target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}, "frame_id": "map"}},
    )
    exec_result = service.execute_guarded("GOTO", goto_view.canonical_args, goto_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_runtime_context(
        {
            "signals": {"OFFBOARD_lost_before_arrival": False},
            "goto": {
                "active_target_pose": {"position": {"x": 1.0, "y": 0.0, "z": 2.0}},
                "distance_m": 0.0,
                "arrived": True,
            },
            "land": {"on_ground": False},
        }
    )
    fake_clock.set(500_000_000)
    service.ledger_tail(last_n=5)

    fake_clock.set(3_600_000_000)
    tail = service.ledger_tail(last_n=5)

    assert tail["open_obligations"] == []
    assert tail["recent_actions"][0]["action_id"] == exec_result.action_id
    assert tail["recent_actions"][0]["state"] == ActionState.CONFIRMED.value


def test_hold_obligation_allows_delayed_loiter_confirmation(fake_clock: FakeClock) -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot("vehicle.mode", "OFFBOARD")
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 1.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.5, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    backend.set_action_result("HOLD", state=ActionState.ACKED_WEAK)

    service = ControlViewService(ROOT, backend=backend)
    hold_view = service.get_control_view("HOLD")
    exec_result = service.execute_guarded("HOLD", hold_view.canonical_args, hold_view.lease_token)

    assert exec_result.status == ActionState.ACKED_WEAK

    backend.set_slot("vehicle.mode", "AUTO.LOITER")
    backend.set_slot(
        "velocity.local",
        {"linear": {"x": 0.0, "y": 0.0, "z": 0.0}, "frame_id": "map"},
    )
    fake_clock.set(2_500_000_000)
    service.ledger_tail(last_n=5)

    fake_clock.set(3_600_000_000)
    tail = service.ledger_tail(last_n=5)

    assert tail["open_obligations"] == []
    assert tail["recent_actions"][0]["action_id"] == exec_result.action_id
    assert tail["recent_actions"][0]["state"] == ActionState.CONFIRMED.value
