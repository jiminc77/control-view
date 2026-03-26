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
        return _summary(f"{family}: {result.verdict.value}", payload)

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
        return _summary(f"refresh: {scope} -> {result.new_verdict.value}", payload)

    @server.tool(
        name="action.execute_guarded",
        output_schema=ExecutionResult.model_json_schema(),
    )
    def action_execute_guarded(
        family: str,
        canonical_args: dict[str, Any],
        lease_token: dict[str, Any],
    ) -> ToolResult:
        result = service.execute_guarded(
            family,
            canonical_args,
            LeaseToken.model_validate(lease_token),
        )
        payload = result.model_dump(mode="json")
        return _summary(f"{family}: {result.status.value}", payload)

    @server.tool(
        name="control.explain_blockers",
        output_schema=ExplainBlockersResult.model_json_schema(),
    )
    def control_explain_blockers(
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> ToolResult:
        payload = service.explain_blockers(family, proposed_args or {})
        return _summary(
            f"{family}: {len(payload['blockers'])} blockers",
            payload,
        )

    @server.tool(
        name="ledger.tail",
        output_schema=LedgerTailResult.model_json_schema(),
    )
    def ledger_tail(
        last_n: int = 20,
        since_mono_ns: int | None = None,
    ) -> ToolResult:
        payload = service.ledger_tail(last_n=last_n, since_mono_ns=since_mono_ns)
        return _summary(
            "ledger: "
            f"{len(payload['recent_events'])} events, "
            f"{len(payload['recent_actions'])} actions",
            payload,
        )
