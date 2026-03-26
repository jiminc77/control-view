from __future__ import annotations

from uuid import uuid4

from control_view.backend.base import BackendAdapter
from control_view.common.time import monotonic_ns
from control_view.common.types import ActionState, EventType
from control_view.contracts.models import ActionRecord, ExecutionResult, LeaseToken
from control_view.runtime.event_bus import EventBus
from control_view.runtime.governor import Governor
from control_view.runtime.obligations import ObligationEngine
from control_view.storage.sqlite_store import SQLiteStore


class Executor:
    def __init__(
        self,
        *,
        backend: BackendAdapter,
        event_bus: EventBus,
        store: SQLiteStore,
        governor: Governor,
        obligations: ObligationEngine,
        evaluate_family,
        materializer,
        compiled_specs,
        family_contracts,
        lease_manager,
    ) -> None:
        self._backend = backend
        self._event_bus = event_bus
        self._store = store
        self._governor = governor
        self._obligations = obligations
        self._evaluate_family = evaluate_family
        self._materializer = materializer
        self._compiled_specs = compiled_specs
        self._family_contracts = family_contracts
        self._lease_manager = lease_manager

    def execute_guarded(
        self,
        family: str,
        canonical_args: dict,
        lease_token: LeaseToken,
    ) -> ExecutionResult:
        action_id = str(uuid4())
        if lease_token.family != family:
            return ExecutionResult(
                status=ActionState.ABORTED,
                action_id=action_id,
                opened_obligation_ids=[],
                abort_reason="lease_family_mismatch",
            )
        if not self._lease_manager.verify_signature(lease_token):
            return ExecutionResult(
                status=ActionState.ABORTED,
                action_id=action_id,
                opened_obligation_ids=[],
                abort_reason="lease_signature_invalid",
            )
        if monotonic_ns() > lease_token.expires_mono_ns:
            return ExecutionResult(
                status=ActionState.EXPIRED,
                action_id=action_id,
                opened_obligation_ids=[],
                abort_reason="lease_expired",
            )
        if self._lease_manager.canonical_arg_hash(canonical_args) != lease_token.arg_hash:
            return ExecutionResult(
                status=ActionState.ABORTED,
                action_id=action_id,
                opened_obligation_ids=[],
                abort_reason="canonical_arg_hash_mismatch",
            )

        view = self._evaluate_family(
            family,
            canonical_args,
            refresh=True,
            canonical_input=True,
        )
        for slot_id, expected_revision in lease_token.critical_slot_revisions.items():
            entry = view.critical_slots.get(slot_id)
            if entry and entry.revision != expected_revision:
                return ExecutionResult(
                    status=ActionState.ABORTED,
                    action_id=action_id,
                    opened_obligation_ids=[],
                    abort_reason=f"critical_slot_revision_changed:{slot_id}",
                )
        if view.verdict.value != "ACT":
            return ExecutionResult(
                status=ActionState.ABORTED,
                action_id=action_id,
                opened_obligation_ids=[],
                abort_reason="guard_recheck_failed",
            )

        requested_mono_ns = monotonic_ns()
        request_record = ActionRecord(
            action_id=action_id,
            family=family,
            requested_mono_ns=requested_mono_ns,
            state=ActionState.REQUESTED,
            backend_request_json=canonical_args,
        )
        self._store.upsert_action(request_record)
        self._event_bus.publish(
            EventType.BACKEND_REQUEST,
            source="executor",
            payload_json={
                "family": family,
                "action_id": action_id,
                "canonical_args": canonical_args,
            },
        )
        result = self._dispatch(family, canonical_args)
        record = ActionRecord(
            action_id=action_id,
            family=family,
            requested_mono_ns=requested_mono_ns,
            state=result.state,
            ack_strength=(
                "strong"
                if result.state == ActionState.ACKED_STRONG
                else "weak"
                if result.state == ActionState.ACKED_WEAK
                else None
            ),
            backend_request_json=canonical_args,
            backend_response_json=result.response,
            confirm_evidence_json=result.confirm_evidence,
            failure_reason_codes=result.reason_codes,
            related_obligation_ids=[],
        )
        self._store.upsert_action(record)
        self._event_bus.publish(
            EventType.BACKEND_ACK,
            source="executor",
            payload_json={
                "family": family,
                "action_id": action_id,
                "state": result.state.value,
                "response": result.response,
            },
        )
        opened = []
        if result.state in {ActionState.ACKED_STRONG, ActionState.ACKED_WEAK}:
            opened = self._obligations.open_for_action(self._family_contracts[family], record)
            if opened:
                record.related_obligation_ids = [item.obligation_id for item in opened]
                self._store.upsert_action(record)

        return ExecutionResult(
            status=result.state,
            action_id=action_id,
            opened_obligation_ids=[item.obligation_id for item in opened],
            abort_reason=result.reason_codes[0] if result.reason_codes else None,
        )

    def _dispatch(self, family: str, canonical_args: dict):
        if family == "ARM":
            return self._backend.arm()
        if family == "TAKEOFF":
            return self._backend.takeoff(
                float(canonical_args["target_altitude"]),
                canonical_args["current_geo_reference"],
            )
        if family == "GOTO":
            return self._backend.goto(canonical_args["target_pose"], canonical_args)
        if family == "HOLD":
            return self._backend.hold()
        if family == "RTL":
            return self._backend.rtl()
        if family == "LAND":
            return self._backend.land()
        return self._backend.set_mode(family)
