from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from control_view.contracts.models import (
    Blocker,
    ControlViewResult,
    ExecutionResult,
    LeaseToken,
    RefreshResult,
)


class ControlViewGetRequest(BaseModel):
    family: str
    proposed_args: dict[str, Any] = Field(default_factory=dict)


class ControlViewRefreshRequest(BaseModel):
    family: str | None = None
    slots: list[str] = Field(default_factory=list)
    proposed_args: dict[str, Any] = Field(default_factory=dict)


class ExecuteGuardedRequest(BaseModel):
    family: str
    canonical_args: dict[str, Any] = Field(default_factory=dict)
    lease_token: LeaseToken
    wait_for_previous: bool | None = None


class ExplainBlockersRequest(BaseModel):
    family: str
    proposed_args: dict[str, Any] = Field(default_factory=dict)


class LedgerTailRequest(BaseModel):
    since_mono_ns: int | None = None
    last_n: int = 20


class ExplainBlockersResult(BaseModel):
    blockers: list[Blocker] = Field(default_factory=list)
    refresh_hints: list[str] = Field(default_factory=list)
    suggested_safe_action: str


class LedgerTailResult(BaseModel):
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    open_obligations: list[dict[str, Any]] = Field(default_factory=list)
    artifact_revisions: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ControlViewResult",
    "ControlViewGetRequest",
    "ExplainBlockersResult",
    "ControlViewRefreshRequest",
    "ExecuteGuardedRequest",
    "ExecutionResult",
    "ExplainBlockersRequest",
    "LedgerTailResult",
    "LedgerTailRequest",
    "RefreshResult",
]
