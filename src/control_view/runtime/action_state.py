from __future__ import annotations

from control_view.common.types import ActionState


def ack_state_for_family(family: str) -> ActionState:
    return {
        "ARM": ActionState.ACKED_STRONG,
        "TAKEOFF": ActionState.ACKED_STRONG,
        "GOTO": ActionState.ACKED_WEAK,
        "HOLD": ActionState.ACKED_WEAK,
        "RTL": ActionState.ACKED_WEAK,
        "LAND": ActionState.ACKED_WEAK,
        "SET_MODE": ActionState.ACKED_WEAK,
    }.get(family, ActionState.FAILED)

