from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TranscriptDecisionResult(BaseModel):
    family: str
    verdict: str
    canonical_args: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    blocker_count: int
    can_execute: bool
    recommended_next: str


class TranscriptExecutionResult(BaseModel):
    family: str
    verdict: str
    status: str
    action_id: str | None = None
    canonical_args: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    abort_reason: str | None = None
    next_check: str = "family.status"


class TranscriptStatusResult(BaseModel):
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    pending_families: list[str] = Field(default_factory=list)
    open_obligation_count: int
