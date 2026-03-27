from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from control_view.baselines import apply_baseline_policy, baseline_view_payload
from control_view.mcp_server.transcript_schemas import (
    TranscriptDecisionResult,
    TranscriptExecutionResult,
    TranscriptStatusResult,
)
from control_view.service import ControlViewService


def _summary(text: str, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


def transcript_decision_payload(
    service: ControlViewService,
    *,
    family: str,
    proposed_args: dict[str, Any] | None = None,
    baseline_policy: str,
) -> dict[str, Any]:
    view = service.get_control_view(family, proposed_args or {})
    projected = baseline_view_payload(view.model_dump(mode="json"), baseline_policy)
    blockers = [
        str(blocker.get("message", blocker.get("kind", "unknown blocker")))
        for blocker in projected.get("blockers", [])
        if isinstance(blocker, dict)
    ]
    verdict = str(projected.get("verdict", view.verdict.value))
    recommended_next = "execute" if verdict == "ACT" else "status"
    if verdict == "REFRESH":
        recommended_next = "retry_after_refresh"
    if verdict in {"SAFE_HOLD", "REFUSE"}:
        recommended_next = "inspect_blockers"
    payload = TranscriptDecisionResult(
        family=family,
        verdict=verdict,
        canonical_args=dict(projected.get("canonical_args", {})),
        blockers=blockers,
        blocker_count=len(blockers),
        can_execute=verdict == "ACT",
        recommended_next=recommended_next,
    )
    return payload.model_dump(mode="json")


def transcript_execute_payload(
    service: ControlViewService,
    *,
    family: str,
    proposed_args: dict[str, Any] | None = None,
    baseline_policy: str,
) -> dict[str, Any]:
    view = service.get_control_view(family, proposed_args or {})
    projected = baseline_view_payload(view.model_dump(mode="json"), baseline_policy)
    blockers = [
        str(blocker.get("message", blocker.get("kind", "unknown blocker")))
        for blocker in projected.get("blockers", [])
        if isinstance(blocker, dict)
    ]
    if projected.get("verdict") != "ACT" or view.lease_token is None:
        return TranscriptExecutionResult(
            family=family,
            verdict=str(projected.get("verdict", view.verdict.value)),
            status="NOT_EXECUTED",
            canonical_args=view.canonical_args,
            blockers=blockers,
        ).model_dump(mode="json")

    execution = service.execute_guarded(family, view.canonical_args, view.lease_token)
    execution_payload = apply_baseline_policy(
        {
            "family": family,
            **execution.model_dump(mode="json"),
        },
        baseline_policy,
    )
    return TranscriptExecutionResult(
        family=family,
        verdict=str(projected.get("verdict", view.verdict.value)),
        status=str(execution_payload.get("status", execution.status.value)),
        action_id=execution.action_id,
        canonical_args=view.canonical_args,
        blockers=blockers,
        abort_reason=execution_payload.get("abort_reason"),
    ).model_dump(mode="json")


def transcript_status_payload(
    service: ControlViewService,
    *,
    last_n: int = 10,
) -> dict[str, Any]:
    tail = service.ledger_tail(last_n=last_n)
    recent_actions = [
        {
            "action_id": item.get("action_id"),
            "family": item.get("family"),
            "state": item.get("state"),
            "failure_reason_codes": item.get("failure_reason_codes", []),
        }
        for item in tail.get("recent_actions", [])
        if isinstance(item, dict)
    ]
    pending_families = sorted(
        {
            str(item.get("family"))
            for item in tail.get("open_obligations", [])
            if isinstance(item, dict) and item.get("family")
        }
    )
    return TranscriptStatusResult(
        recent_actions=recent_actions,
        pending_families=pending_families,
        open_obligation_count=len(tail.get("open_obligations", [])),
    ).model_dump(mode="json")


def register_transcript_tools(
    server: FastMCP,
    service: ControlViewService,
    *,
    baseline_policy: str,
) -> None:
    @server.tool(
        name="family.decide",
        output_schema=TranscriptDecisionResult.model_json_schema(),
    )
    def family_decide(
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> ToolResult:
        payload = transcript_decision_payload(
            service,
            family=family,
            proposed_args=proposed_args,
            baseline_policy=baseline_policy,
        )
        return _summary(f"{family}: {payload['verdict']}", payload)

    @server.tool(
        name="family.execute",
        output_schema=TranscriptExecutionResult.model_json_schema(),
    )
    def family_execute(
        family: str,
        proposed_args: dict[str, Any] | None = None,
    ) -> ToolResult:
        payload = transcript_execute_payload(
            service,
            family=family,
            proposed_args=proposed_args,
            baseline_policy=baseline_policy,
        )
        return _summary(f"{family}: {payload['status']}", payload)

    @server.tool(
        name="family.status",
        output_schema=TranscriptStatusResult.model_json_schema(),
    )
    def family_status(last_n: int = 10) -> ToolResult:
        payload = transcript_status_payload(service, last_n=last_n)
        return _summary(
            f"status: {payload['open_obligation_count']} open obligations",
            payload,
        )
