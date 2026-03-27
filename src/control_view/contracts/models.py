from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_view.common.types import (
    ActionState,
    FieldClass,
    JSONDict,
    SlotOwner,
    ValidState,
    Verdict,
)


class PredicateSpec(BaseModel):
    id: str
    expr: str


class ObligationTemplate(BaseModel):
    id: str
    open_on: ActionState
    close_when: list[Any]
    fail_when: list[Any] = Field(default_factory=list)


class SafeHoldMapping(BaseModel):
    backend_action: str


class FieldSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    field_class: FieldClass = Field(alias="class")
    owner: SlotOwner
    value_type: str | None = None
    value_schema: JSONDict | None = None
    source: JSONDict
    authority: JSONDict
    derivation: JSONDict | None = None
    revision_rule: str
    freshness: JSONDict
    invalidators: list[str]
    serialization_policy: JSONDict
    status: str | None = None

    @model_validator(mode="after")
    def validate_value_shape(self) -> FieldSpec:
        if not self.value_type and not self.value_schema:
            raise ValueError("field must declare either value_type or value_schema")
        return self


class FamilyContract(BaseModel):
    family: str
    risk_class: str
    argument_schema: JSONDict
    guard_slots: list[str]
    support_slots: list[str]
    confirm_slots: list[str]
    diagnostic_slots: list[str]
    predicates: list[PredicateSpec]
    backend_mapping: JSONDict
    effects: JSONDict
    obligation_templates: list[ObligationTemplate]
    safe_hold_mapping: SafeHoldMapping


class CompiledPredicate(BaseModel):
    id: str
    expr: str
    slot_dependencies: list[str]


class CompiledViewSpec(BaseModel):
    family: str
    required_slots: list[str]
    role_partition: dict[str, list[str]]
    predicate_plan: list[CompiledPredicate]
    resolver_plan: dict[str, JSONDict]
    derivation_plan: dict[str, JSONDict]
    blocker_templates: dict[str, JSONDict]
    refresh_plan: dict[str, list[str]]
    commit_guard_slots: list[str]
    obligation_templates: list[ObligationTemplate]
    backend_action_plan: JSONDict
    serializer_plan: JSONDict


class EvidenceEntry(BaseModel):
    slot_id: str
    value_json: JSONDict | None = None
    quality_json: JSONDict = Field(default_factory=dict)
    authority_source: str
    received_mono_ns: int
    received_wall_time: str
    source_header_stamp: str | None = None
    revision: int
    frame_id: str | None = None
    valid_state: ValidState
    lineage_event_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ActionRecord(BaseModel):
    action_id: str
    family: str
    requested_mono_ns: int
    state: ActionState
    ack_strength: str | None = None
    backend_request_json: JSONDict = Field(default_factory=dict)
    backend_response_json: JSONDict = Field(default_factory=dict)
    confirm_evidence_json: JSONDict = Field(default_factory=dict)
    failure_reason_codes: list[str] = Field(default_factory=list)
    related_obligation_ids: list[str] = Field(default_factory=list)


class ObligationRecord(BaseModel):
    obligation_id: str
    family: str
    kind: str
    status: str
    created_mono_ns: int
    updated_mono_ns: int
    open_on_action_state: ActionState
    close_conditions: list[Any]
    failure_conditions: list[Any]
    related_action_id: str
    notes: JSONDict = Field(default_factory=dict)


class LeaseToken(BaseModel):
    lease_id: str
    family: str
    issued_mono_ns: int
    expires_mono_ns: int
    critical_slot_revisions: dict[str, int]
    arg_hash: str
    nonce: str
    signature: str


class Blocker(BaseModel):
    slot_id: str
    kind: str
    severity: str
    message: str
    refreshable: bool
    refresh_hint: str
    safe_action: str | None = None
    evidence_summary: JSONDict = Field(default_factory=dict)


class ControlViewResult(BaseModel):
    family: str
    verdict: Verdict
    canonical_args: JSONDict
    critical_slots: dict[str, EvidenceEntry]
    support_slots: dict[str, EvidenceEntry]
    blockers: list[Blocker]
    open_obligations: list[ObligationRecord]
    commit_guard_slots: list[str] = Field(default_factory=list)
    commit_guard_revisions: dict[str, int] = Field(default_factory=dict)
    decision_context: JSONDict = Field(default_factory=dict)
    lease_token: LeaseToken | None = None
    lease_expires_in_ms: int | None = None


class RefreshResult(BaseModel):
    refreshed_slots: list[str]
    unresolved_blockers: list[Blocker]
    new_verdict: Verdict


class ExecutionResult(BaseModel):
    status: ActionState
    action_id: str
    opened_obligation_ids: list[str]
    abort_reason: str | None = None
