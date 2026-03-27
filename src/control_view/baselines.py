from __future__ import annotations

from copy import deepcopy
from typing import Any

from control_view.common.types import Verdict
from control_view.runtime.action_state import ack_state_for_family

BASELINE_NAMES = ("B1", "B2", "B3")
_HIGH_RISK = {"TAKEOFF", "GOTO", "RTL", "LAND"}
_IGNORED_BLOCKERS = {
    "B1": {
        "missing_slot",
        "stale_slot",
        "invalidated_slot",
        "disagreed_slot",
        "unconfirmed_slot",
        "pending_transition",
    },
    "B2": {
        "invalidated_slot",
        "disagreed_slot",
        "pending_transition",
    },
    "B3": set(),
}


def normalize_baseline_name(baseline: str | None) -> str:
    candidate = (baseline or "B3").upper()
    if candidate not in BASELINE_NAMES:
        raise ValueError(f"unsupported baseline: {baseline}")
    return candidate


def ignored_blockers_for(baseline: str | None) -> set[str]:
    return _IGNORED_BLOCKERS[normalize_baseline_name(baseline)]


def apply_baseline_policy(output: dict[str, Any], baseline: str | None) -> dict[str, Any]:
    normalized = normalize_baseline_name(baseline)
    if normalized == "B3":
        if "policy_swap" not in output:
            output["policy_swap"] = normalized
        return output

    ignored_blockers = ignored_blockers_for(normalized)
    if "verdict" in output and "blockers" in output:
        blockers = [
            blocker
            for blocker in output.get("blockers", [])
            if blocker.get("kind") not in ignored_blockers
        ]
        output["blockers"] = blockers
        if "pending_transition" in ignored_blockers:
            output["open_obligations"] = []
        output["verdict"] = _policy_verdict(output.get("family", ""), blockers)
    if (
        "status" in output
        and normalized in {"B1", "B2"}
        and output.get("status") == "ABORTED"
    ):
        output["status"] = ack_state_for_family(output.get("family", "")).value
        output["abort_reason"] = None
    output["policy_swap"] = normalized
    return output


def baseline_view_payload(payload: dict[str, Any], baseline: str | None) -> dict[str, Any]:
    projected = deepcopy(payload)
    return apply_baseline_policy(projected, baseline)


def _policy_verdict(family: str, blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return Verdict.ACT.value
    if all(blocker.get("refreshable", False) for blocker in blockers):
        return Verdict.REFRESH.value
    if family in _HIGH_RISK:
        return Verdict.SAFE_HOLD.value
    return Verdict.REFUSE.value
