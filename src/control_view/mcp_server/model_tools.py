from __future__ import annotations

import time
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

from control_view.common.types import ActionState, Verdict
from control_view.mcp_server.model_schemas import FamilyStepResult
from control_view.service import ControlViewService

_POLL_INTERVAL_SEC = 0.25
_RETRY_DELAY_MS = 750
_TERMINAL_ACTION_STATES = {
    ActionState.CONFIRMED.value,
    ActionState.FAILED.value,
    ActionState.EXPIRED.value,
    ActionState.ABORTED.value,
}
_TERMINAL_MISSION_FAMILIES = {"LAND", "RTL"}
_FAMILY_TIMEOUT_SEC = {
    "ARM": 4.0,
    "TAKEOFF": 25.0,
    "HOLD": 6.0,
    "RTL": 10.0,
}


def _structured_only(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=[], structured_content=payload)


def _blocker_reason_codes(blockers: list[Any]) -> list[str]:
    reason_codes: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        kind = str(blocker.get("kind") or "unknown")
        slot_id = str(blocker.get("slot_id") or "unknown")
        message = str(blocker.get("message") or "")
        if kind == "predicate_failed" and message:
            predicate_id = message.split(" failed", 1)[0].strip()
            reason_codes.append(f"predicate_failed:{predicate_id}")
            continue
        reason_codes.append(f"{kind}:{slot_id}")
    return reason_codes


def _recovery_family(family: str, reason_codes: list[str]) -> str | None:
    if family != "ARM" and "predicate_failed:armed_ok" in reason_codes:
        return "ARM"
    return None


def _retryable_blockers(blockers: list[Any]) -> bool:
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        kind = str(blocker.get("kind") or "")
        if blocker.get("refreshable"):
            return True
        if kind in {"pending_transition", "predicate_failed"}:
            return True
    return False


def _step_timeout_sec(family: str, canonical_args: dict[str, Any]) -> float:
    if family == "GOTO":
        return max(8.0, float(canonical_args.get("nav_timeout_sec", 20.0)) + 2.0)
    if family == "LAND":
        return max(15.0, float(canonical_args.get("land_timeout_sec", 30.0)) + 2.0)
    return _FAMILY_TIMEOUT_SEC.get(family, 8.0)


def _open_obligation_count(service: ControlViewService) -> int:
    return len(service.store.list_open_obligations())


def _confirmed_next_action(family: str) -> str:
    if family in _TERMINAL_MISSION_FAMILIES:
        return "STOP"
    return "ADVANCE"


def _terminal_completion_payload(
    service: ControlViewService,
    *,
    family: str,
) -> dict[str, Any] | None:
    if family not in _TERMINAL_MISSION_FAMILIES:
        return None
    if _open_obligation_count(service) != 0:
        return None
    recent_actions = service.store.list_actions(limit=1)
    if not recent_actions:
        return None
    latest_action = recent_actions[0]
    if latest_action.family != family or latest_action.state != ActionState.CONFIRMED:
        return None
    return FamilyStepResult(
        family=family,
        state="CONFIRMED",
        next_action="STOP",
        action_id=latest_action.action_id,
        open_obligation_count=0,
    ).model_dump(mode="json")


def _refresh_pending_family(
    service: ControlViewService,
    *,
    family: str,
    canonical_args: dict[str, Any],
) -> None:
    service.refresh_control_view(
        family=family,
        proposed_args=canonical_args,
    )


def _wait_for_terminal_action(
    service: ControlViewService,
    *,
    family: str,
    canonical_args: dict[str, Any],
    action_id: str,
    timeout_sec: float,
):
    deadline = time.monotonic() + timeout_sec
    latest_action = service.store.get_action(action_id)
    while time.monotonic() < deadline:
        _refresh_pending_family(
            service,
            family=family,
            canonical_args=canonical_args,
        )
        latest_action = service.store.get_action(action_id)
        if latest_action is not None and latest_action.state.value in _TERMINAL_ACTION_STATES:
            return latest_action
        time.sleep(_POLL_INTERVAL_SEC)
    return latest_action


def family_step_payload(
    service: ControlViewService,
    *,
    family: str,
    proposed_args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terminal_payload = _terminal_completion_payload(service, family=family)
    if terminal_payload is not None:
        return terminal_payload

    view = service.get_control_view(family, proposed_args or {})
    blockers = [item.model_dump(mode="json") for item in view.blockers]
    if view.verdict != Verdict.ACT or view.lease_token is None:
        reason_codes = _blocker_reason_codes(blockers)
        recovery_family = _recovery_family(family, reason_codes)
        next_action = (
            "RECOVER_PRECONDITION"
            if recovery_family is not None
            else "RETRY_SAME_FAMILY"
            if _retryable_blockers(blockers)
            else "STOP"
        )
        return FamilyStepResult(
            family=family,
            state="BLOCKED",
            next_action=next_action,
            recovery_family=recovery_family,
            reason_codes=reason_codes,
            retry_after_ms=_RETRY_DELAY_MS if next_action == "RETRY_SAME_FAMILY" else 0,
            open_obligation_count=_open_obligation_count(service),
        ).model_dump(mode="json")

    execution = service.execute_guarded(family, view.canonical_args, view.lease_token)
    if execution.status == ActionState.CONFIRMED:
        return FamilyStepResult(
            family=family,
            state="CONFIRMED",
            next_action=_confirmed_next_action(family),
            action_id=execution.action_id,
            open_obligation_count=_open_obligation_count(service),
        ).model_dump(mode="json")

    latest_action = _wait_for_terminal_action(
        service,
        family=family,
        canonical_args=view.canonical_args,
        action_id=execution.action_id,
        timeout_sec=_step_timeout_sec(family, view.canonical_args),
    )
    if latest_action is None:
        return FamilyStepResult(
            family=family,
            state="PENDING",
            next_action="RETRY_SAME_FAMILY",
            retry_after_ms=_RETRY_DELAY_MS,
            action_id=execution.action_id,
            open_obligation_count=_open_obligation_count(service),
        ).model_dump(mode="json")

    latest_state = latest_action.state.value
    if latest_state == ActionState.CONFIRMED.value:
        return FamilyStepResult(
            family=family,
            state="CONFIRMED",
            next_action=_confirmed_next_action(family),
            action_id=execution.action_id,
            open_obligation_count=_open_obligation_count(service),
        ).model_dump(mode="json")

    if latest_state in {
        ActionState.ABORTED.value,
        ActionState.FAILED.value,
        ActionState.EXPIRED.value,
    }:
        return FamilyStepResult(
            family=family,
            state="FAILED",
            next_action="RETRY_SAME_FAMILY",
            reason_codes=list(latest_action.failure_reason_codes),
            retry_after_ms=_RETRY_DELAY_MS,
            action_id=execution.action_id,
            open_obligation_count=_open_obligation_count(service),
        ).model_dump(mode="json")

    return FamilyStepResult(
        family=family,
        state="PENDING",
        next_action="RETRY_SAME_FAMILY",
        retry_after_ms=_RETRY_DELAY_MS,
        action_id=execution.action_id,
        open_obligation_count=_open_obligation_count(service),
    ).model_dump(mode="json")


def register_model_tools(server: FastMCP, service: ControlViewService) -> None:
    @server.tool(
        name="family.step",
        output_schema=FamilyStepResult.model_json_schema(),
    )
    def family_step(
        family: str,
        proposed_args: dict[str, Any] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = family_step_payload(
            service,
            family=family,
            proposed_args=proposed_args,
        )
        return _structured_only(payload)
