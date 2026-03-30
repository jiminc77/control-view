from __future__ import annotations

import time
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

_STATUS_POLL_INTERVAL_SEC = 0.5
_EMPTY_STATUS_SETTLE_SEC = 1.0
_PENDING_STATUS_WAIT_SEC = {
    "ARM": 3.5,
    "TAKEOFF": 12.0,
    "GOTO": 6.0,
    "HOLD": 5.0,
    "RTL": 5.0,
    "LAND": 15.0,
}
_TERMINAL_ACTION_STATES = {"CONFIRMED", "FAILED", "EXPIRED"}


def _summary(text: str, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )

def transcript_decision_summary_text(payload: dict[str, Any]) -> str:
    blocker_count = len(payload.get("blockers", []))
    return (
        f"family={payload.get('family')} verdict={payload.get('verdict')} "
        f"blockers={blocker_count} next={payload.get('recommended_next')}"
    )


def transcript_execute_summary_text(payload: dict[str, Any]) -> str:
    summary = (
        f"family={payload.get('family')} verdict={payload.get('verdict')} "
        f"status={payload.get('status')} next={payload.get('next_check')}"
    )
    abort_reason = payload.get("abort_reason")
    if abort_reason:
        summary += f" abort={abort_reason}"
    return summary


def transcript_status_summary_text(payload: dict[str, Any]) -> str:
    latest_action = "none"
    recent_actions = payload.get("recent_actions", [])
    if recent_actions:
        action = recent_actions[0]
        latest_action = f"{action.get('family')}:{action.get('state')}"
    pending = ",".join(str(family) for family in payload.get("pending_families", [])) or "none"
    return (
        f"latest={latest_action} pending={pending} "
        f"obligations={payload.get('open_obligation_count', 0)}"
    )


def _transcript_status_snapshot(
    service: ControlViewService,
    *,
    last_n: int,
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


def _transcript_status_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    recent_actions = tuple(
        (
            item.get("action_id"),
            item.get("family"),
            item.get("state"),
            tuple(item.get("failure_reason_codes", [])),
        )
        for item in payload.get("recent_actions", [])
        if isinstance(item, dict)
    )
    return (
        int(payload.get("open_obligation_count", 0)),
        tuple(payload.get("pending_families", [])),
        recent_actions,
    )


def _transcript_status_wait_budget(payload: dict[str, Any]) -> float:
    pending = payload.get("pending_families", [])
    if pending:
        return max(_PENDING_STATUS_WAIT_SEC.get(str(family), 3.0) for family in pending)
    if not payload.get("recent_actions", []):
        return _EMPTY_STATUS_SETTLE_SEC
    return 0.0


def _latest_action_state(payload: dict[str, Any], family: str) -> str | None:
    recent_actions = payload.get("recent_actions", [])
    if not recent_actions:
        return None
    latest = recent_actions[0]
    if latest.get("family") != family:
        return None
    state = latest.get("state")
    return str(state) if state is not None else None


def _settled_action_state(
    service: ControlViewService,
    *,
    family: str,
    fallback_status: str,
) -> str:
    if fallback_status not in {"ACKED_STRONG", "ACKED_WEAK"}:
        return fallback_status
    payload = _transcript_status_snapshot(service, last_n=3)
    latest_state = _latest_action_state(payload, family)
    if latest_state in _TERMINAL_ACTION_STATES:
        return latest_state
    if latest_state is None or fallback_status != "ACKED_STRONG":
        return fallback_status
    wait_budget_sec = _PENDING_STATUS_WAIT_SEC.get(family, 0.0)
    if wait_budget_sec <= 0.0:
        return fallback_status
    deadline = time.monotonic() + wait_budget_sec
    while time.monotonic() < deadline:
        time.sleep(min(_STATUS_POLL_INTERVAL_SEC, max(deadline - time.monotonic(), 0.0)))
        payload = _transcript_status_snapshot(service, last_n=3)
        latest_state = _latest_action_state(payload, family)
        if latest_state in _TERMINAL_ACTION_STATES:
            return latest_state
    return fallback_status


def _execution_next_check(status: str) -> str:
    if status in _TERMINAL_ACTION_STATES:
        return "none"
    if status in {"NOT_EXECUTED", "ABORTED", "FAILED"}:
        return "family.decide"
    return "family.status"


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
    transcript_status_payload(service, last_n=3)
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
            next_check="family.decide",
        ).model_dump(mode="json")

    execution = service.execute_guarded(family, view.canonical_args, view.lease_token)
    execution_payload = apply_baseline_policy(
        {
            "family": family,
            **execution.model_dump(mode="json"),
        },
        baseline_policy,
    )
    settled_status = _settled_action_state(
        service,
        family=family,
        fallback_status=str(execution_payload.get("status", execution.status.value)),
    )
    return TranscriptExecutionResult(
        family=family,
        verdict=str(projected.get("verdict", view.verdict.value)),
        status=settled_status,
        action_id=execution.action_id,
        canonical_args=view.canonical_args,
        blockers=blockers,
        abort_reason=execution_payload.get("abort_reason"),
        next_check=_execution_next_check(settled_status),
    ).model_dump(mode="json")


def transcript_status_payload(
    service: ControlViewService,
    *,
    last_n: int = 3,
) -> dict[str, Any]:
    payload = _transcript_status_snapshot(service, last_n=last_n)
    wait_budget_sec = _transcript_status_wait_budget(payload)
    if wait_budget_sec <= 0.0:
        return payload

    deadline = time.monotonic() + wait_budget_sec
    best = payload
    best_signature = _transcript_status_signature(payload)
    while time.monotonic() < deadline:
        time.sleep(min(_STATUS_POLL_INTERVAL_SEC, max(deadline - time.monotonic(), 0.0)))
        candidate = _transcript_status_snapshot(service, last_n=last_n)
        candidate_signature = _transcript_status_signature(candidate)
        if candidate_signature != best_signature:
            best = candidate
            best_signature = candidate_signature
            if candidate.get("open_obligation_count", 0) == 0:
                return candidate
    return best


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
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = transcript_decision_payload(
            service,
            family=family,
            proposed_args=proposed_args,
            baseline_policy=baseline_policy,
        )
        return _summary(transcript_decision_summary_text(payload), payload)

    @server.tool(
        name="family.execute",
        output_schema=TranscriptExecutionResult.model_json_schema(),
    )
    def family_execute(
        family: str,
        proposed_args: dict[str, Any] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = transcript_execute_payload(
            service,
            family=family,
            proposed_args=proposed_args,
            baseline_policy=baseline_policy,
        )
        return _summary(transcript_execute_summary_text(payload), payload)

    @server.tool(
        name="family.status",
        output_schema=TranscriptStatusResult.model_json_schema(),
    )
    def family_status(last_n: int = 3, wait_for_previous: bool | None = None) -> ToolResult:
        del wait_for_previous
        payload = transcript_status_payload(service, last_n=last_n)
        return _summary(transcript_status_summary_text(payload), payload)
