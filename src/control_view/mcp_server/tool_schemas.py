from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from control_view.contracts.models import ExecutionResult, LeaseToken


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


class ExplainBlockersRequest(BaseModel):
    family: str
    proposed_args: dict[str, Any] = Field(default_factory=dict)


class LedgerTailRequest(BaseModel):
    last_n: int = 20


__all__ = [
    "ControlViewGetRequest",
    "ControlViewRefreshRequest",
    "ExecuteGuardedRequest",
    "ExecutionResult",
    "ExplainBlockersRequest",
    "LedgerTailRequest",
]

