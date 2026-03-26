from __future__ import annotations

from control_view.common.types import ValidState
from control_view.contracts.models import Blocker, EvidenceEntry


def make_blocker(
    *,
    slot_id: str,
    kind: str,
    severity: str,
    message: str,
    refreshable: bool,
    refresh_hint: str,
    safe_action: str | None = None,
    evidence: EvidenceEntry | None = None,
) -> Blocker:
    return Blocker(
        slot_id=slot_id,
        kind=kind,
        severity=severity,
        message=message,
        refreshable=refreshable,
        refresh_hint=refresh_hint,
        safe_action=safe_action,
        evidence_summary=evidence.model_dump(mode="json") if evidence else {},
    )


def blocker_for_valid_state(
    slot_id: str,
    state: ValidState,
    evidence: EvidenceEntry | None,
) -> Blocker:
    kind = {
        ValidState.MISSING: "missing_slot",
        ValidState.STALE: "stale_slot",
        ValidState.INVALIDATED: "invalidated_slot",
        ValidState.DISAGREED: "disagreed_slot",
        ValidState.UNCONFIRMED: "unconfirmed_slot",
    }.get(state, "slot_invalid")
    return make_blocker(
        slot_id=slot_id,
        kind=kind,
        severity="high",
        message=f"{slot_id} is {state.value.lower()}",
        refreshable=state in {ValidState.MISSING, ValidState.STALE, ValidState.UNCONFIRMED},
        refresh_hint=f"refresh {slot_id}",
        evidence=evidence,
    )
