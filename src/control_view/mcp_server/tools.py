from __future__ import annotations

import json
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


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


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
    summary = {
        "family": payload.get("family"),
        "verdict": payload.get("verdict"),
        "canonical_args": payload.get("canonical_args", {}),
        "blockers": _blocker_messages(payload.get("blockers", [])),
        "open_obligation_families": [
            obligation.get("family")
            for obligation in payload.get("open_obligations", [])
            if isinstance(obligation, dict) and obligation.get("family")
        ],
    }
    lease_token = payload.get("lease_token")
    if lease_token is not None:
        summary["lease_token"] = lease_token
        summary["lease_expires_in_ms"] = payload.get("lease_expires_in_ms")
    return _json_text(summary)


def refresh_summary_text(scope: str, payload: dict[str, Any]) -> str:
    return _json_text(
        {
            "scope": scope,
            "new_verdict": payload.get("new_verdict"),
            "refreshed_slots": payload.get("refreshed_slots", []),
            "unresolved_blockers": _blocker_messages(payload.get("unresolved_blockers", [])),
        }
    )


def execute_summary_text(payload: dict[str, Any]) -> str:
    return _json_text(
        {
            "status": payload.get("status"),
            "action_id": payload.get("action_id"),
            "opened_obligation_ids": payload.get("opened_obligation_ids", []),
            "abort_reason": payload.get("abort_reason"),
        }
    )


def explain_blockers_summary_text(family: str, payload: dict[str, Any]) -> str:
    return _json_text(
        {
            "family": family,
            "blockers": _blocker_messages(payload.get("blockers", [])),
            "refresh_hints": payload.get("refresh_hints", []),
            "suggested_safe_action": payload.get("suggested_safe_action"),
        }
    )


def ledger_tail_summary_text(payload: dict[str, Any]) -> str:
    recent_actions = []
    for action in payload.get("recent_actions", [])[-5:]:
        if not isinstance(action, dict):
            continue
        recent_actions.append(
            {
                "family": action.get("family"),
                "state": action.get("state"),
                "failure_reason_codes": action.get("failure_reason_codes", []),
            }
        )
    open_obligations = []
    for obligation in payload.get("open_obligations", []):
        if not isinstance(obligation, dict):
            continue
        open_obligations.append(
            {
                "family": obligation.get("family"),
                "kind": obligation.get("kind"),
                "status": obligation.get("status"),
            }
        )
    artifact_revisions = {
        item.get("artifact_name"): item.get("revision")
        for item in payload.get("artifact_revisions", [])
        if isinstance(item, dict) and item.get("artifact_name") is not None
    }
    return _json_text(
        {
            "recent_actions": recent_actions,
            "open_obligations": open_obligations,
            "artifact_revisions": artifact_revisions,
        }
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
    ) -> ToolResult:
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
    ) -> ToolResult:
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
    ) -> ToolResult:
        payload = service.explain_blockers(family, proposed_args or {})
        return _summary(explain_blockers_summary_text(family, payload), payload)

    @server.tool(
        name="ledger.tail",
        output_schema=LedgerTailResult.model_json_schema(),
    )
    def ledger_tail(
        last_n: int = 20,
        since_mono_ns: int | None = None,
    ) -> ToolResult:
        payload = service.ledger_tail(last_n=last_n, since_mono_ns=since_mono_ns)
        return _summary(ledger_tail_summary_text(payload), payload)
