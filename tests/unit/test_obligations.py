from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ActionState
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


def test_arm_obligation_closes_after_vehicle_is_armed() -> None:
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
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    service.get_control_view("HOLD")

    assert service.store.list_open_obligations() == []

