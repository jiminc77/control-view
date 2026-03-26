from __future__ import annotations

from control_view.common.types import ActionState
from control_view.runtime.action_state import ack_state_for_family


def test_ack_state_family_mapping() -> None:
    assert ack_state_for_family("ARM") == ActionState.ACKED_STRONG
    assert ack_state_for_family("GOTO") == ActionState.ACKED_WEAK
    assert ack_state_for_family("UNKNOWN") == ActionState.FAILED

