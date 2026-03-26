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

    fake_clock.set(3_000_000_000)
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    service.get_control_view("HOLD")

    action = service.store.get_action(exec_result.action_id)
    assert action is not None
    assert action.state == ActionState.EXPIRED
