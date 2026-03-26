from __future__ import annotations

from uuid import uuid4

from control_view.common.time import monotonic_ns
from control_view.contracts.models import ActionRecord, FamilyContract, ObligationRecord
from control_view.runtime.governor import evaluate_expression
from control_view.storage.sqlite_store import SQLiteStore


class ObligationEngine:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def open_for_action(
        self,
        contract: FamilyContract,
        action: ActionRecord,
    ) -> list[ObligationRecord]:
        opened: list[ObligationRecord] = []
        for template in contract.obligation_templates:
            if template.open_on != action.state:
                continue
            obligation = ObligationRecord(
                obligation_id=str(uuid4()),
                family=contract.family,
                kind=template.id,
                status="OPEN",
                created_mono_ns=action.requested_mono_ns,
                updated_mono_ns=action.requested_mono_ns,
                open_on_action_state=template.open_on,
                close_conditions=template.close_when,
                failure_conditions=template.fail_when,
                related_action_id=action.action_id,
                notes={"family": contract.family},
            )
            self._store.upsert_obligation(obligation)
            opened.append(obligation)
        return opened

    def reconcile(
        self,
        evidence_map: dict,
        *,
        backend_context: dict | None = None,
    ) -> list[ObligationRecord]:
        open_records = self._store.list_open_obligations()
        now_ns = monotonic_ns()
        updated_open: list[ObligationRecord] = []
        for record in open_records:
            failed = any(
                self._condition_failed(
                    condition,
                    record,
                    now_ns,
                    evidence_map,
                    backend_context or {},
                )
                for condition in record.failure_conditions
            )
            if failed:
                record.status = "FAILED"
                record.updated_mono_ns = now_ns
                self._store.upsert_obligation(record)
                continue

            if all(
                self._condition_met(condition, evidence_map, backend_context or {})
                for condition in record.close_conditions
            ):
                record.status = "CONFIRMED"
                record.updated_mono_ns = now_ns
                self._store.upsert_obligation(record)
                continue

            updated_open.append(record)
        return updated_open

    def _condition_failed(
        self,
        condition,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
    ) -> bool:
        if isinstance(condition, dict):
            timeout_key, timeout_sec = next(iter(condition.items()))
            if timeout_key.endswith("_within_sec"):
                elapsed_sec = (now_ns - record.created_mono_ns) / 1_000_000_000
                return elapsed_sec > float(timeout_sec)
            return False
        if isinstance(condition, str):
            signals = backend_context.get("signals", {})
            if condition in signals:
                return bool(signals[condition])
        return evaluate_expression(
            str(condition),
            evidence_map,
            backend_context=backend_context,
        )

    def _condition_met(self, condition, evidence_map: dict, backend_context: dict) -> bool:
        if isinstance(condition, dict):
            return False
        return evaluate_expression(
            str(condition),
            evidence_map,
            backend_context=backend_context,
        )
