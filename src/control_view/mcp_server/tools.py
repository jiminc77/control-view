from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from control_view.contracts.models import LeaseToken
from control_view.mcp_server.tool_schemas import (
    ControlViewResult,
    ExecutionResult,
    ExplainBlockersResult,
    LedgerTailResult,
    RefreshResult,
)
from control_view.service import ControlViewService


def _blocker_messages(blockers: list[Any]) -> list[str]:
    messages: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            continue
        message = blocker.get("message") or blocker.get("kind") or blocker.get("slot_id")
        if message is not None:
            messages.append(str(message))
    return messages


def control_view_summary_text(payload: dict[str, Any]) -> str:
    blocker_count = len(_blocker_messages(payload.get("blockers", [])))
    obligation_count = len(payload.get("open_obligations", []))
    lease_state = "lease" if payload.get("lease_token") is not None else "no_lease"
    return (
        f"family={payload.get('family')} verdict={payload.get('verdict')} "
        f"blockers={blocker_count} obligations={obligation_count} {lease_state}"
    )


def refresh_summary_text(scope: str, payload: dict[str, Any]) -> str:
    unresolved = len(_blocker_messages(payload.get("unresolved_blockers", [])))
    refreshed = len(payload.get("refreshed_slots", []))
    return (
        f"scope={scope} verdict={payload.get('new_verdict')} "
        f"refreshed_slots={refreshed} unresolved={unresolved}"
    )


def execute_summary_text(payload: dict[str, Any]) -> str:
    opened = len(payload.get("opened_obligation_ids", []))
    summary = f"status={payload.get('status')} action_id={payload.get('action_id')} opened={opened}"
    abort_reason = payload.get("abort_reason")
    if abort_reason:
        summary += f" abort={abort_reason}"
    return summary


def explain_blockers_summary_text(family: str, payload: dict[str, Any]) -> str:
    blockers = _blocker_messages(payload.get("blockers", []))
    first_blocker = blockers[0] if blockers else "none"
    return (
        f"family={family} blockers={len(blockers)} first={first_blocker} "
        f"safe={payload.get('suggested_safe_action')}"
    )


def ledger_tail_summary_text(payload: dict[str, Any]) -> str:
    latest_action = "none"
    for action in payload.get("recent_actions", []):
        if isinstance(action, dict):
            latest_action = f"{action.get('family')}:{action.get('state')}"
            break
    return (
        f"latest={latest_action} obligations={len(payload.get('open_obligations', []))} "
        f"artifacts={len(payload.get('artifact_revisions', []))}"
    )


def _summary(text: str, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


def register_tools(server: FastMCP, service: ControlViewService) -> None:
    @server.tool(
        name="control_view.get",
        output_schema=ControlViewResult.model_json_schema(),
    )
    def control_view_get(
        family: str,
        proposed_args: dict[str, Any] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        result = service.get_control_view(family, proposed_args or {})
        payload = result.model_dump(mode="json")
        return _summary(control_view_summary_text(payload), payload)

    @server.tool(
        name="control_view.refresh",
        output_schema=RefreshResult.model_json_schema(),
    )
    def control_view_refresh(
        family: str | None = None,
        slots: list[str] | None = None,
        proposed_args: dict[str, Any] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        result = service.refresh_control_view(
            family=family,
            slots=slots or [],
            proposed_args=proposed_args or {},
        )
        payload = result.model_dump(mode="json")
        scope = family or ",".join(slots or []) or "slots"
        return _summary(refresh_summary_text(scope, payload), payload)

    @server.tool(
        name="action.execute_guarded",
        output_schema=ExecutionResult.model_json_schema(),
    )
    def action_execute_guarded(
        family: str,
        canonical_args: dict[str, Any],
        lease_token: dict[str, Any],
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        result = service.execute_guarded(
            family,
            canonical_args,
            LeaseToken.model_validate(lease_token),
        )
        payload = result.model_dump(mode="json")
        return _summary(execute_summary_text(payload), payload)

    @server.tool(
        name="control.explain_blockers",
        output_schema=ExplainBlockersResult.model_json_schema(),
    )
    def control_explain_blockers(
        family: str,
        proposed_args: dict[str, Any] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = service.explain_blockers(family, proposed_args or {})
        return _summary(explain_blockers_summary_text(family, payload), payload)

    @server.tool(
        name="ledger.tail",
        output_schema=LedgerTailResult.model_json_schema(),
    )
    def ledger_tail(
        last_n: int = 20,
        since_mono_ns: int | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = service.ledger_tail(last_n=last_n, since_mono_ns=since_mono_ns)
        return _summary(ledger_tail_summary_text(payload), payload)
