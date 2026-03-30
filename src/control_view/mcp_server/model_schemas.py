from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FamilyStepResult(BaseModel):
    family: str
    state: Literal["CONFIRMED", "PENDING", "BLOCKED", "FAILED"]
    next_action: Literal["ADVANCE", "RETRY_SAME_FAMILY", "RECOVER_PRECONDITION", "STOP"]
    recovery_family: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    retry_after_ms: int = 0
    open_obligation_count: int = 0
    action_id: str | None = None
