from __future__ import annotations

from uuid import uuid4

from control_view.common.time import monotonic_ns
from control_view.common.types import ActionState, EventType
from control_view.common.utils import deep_get, stable_json_dumps
from control_view.contracts.models import ActionRecord, FamilyContract, ObligationRecord
from control_view.replay.recorder import ReplayRecorder
from control_view.runtime.governor import evaluate_expression
from control_view.storage.sqlite_store import SQLiteStore


class ObligationEngine:
    def __init__(
        self,
        store: SQLiteStore,
        event_bus=None,
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._recorder = recorder

    def _record_obligation_transition(self, record: ObligationRecord) -> None:
        if self._recorder is None:
            return
        self._recorder.record_obligation_transition(
            record.family,
            record.model_dump(mode="json"),
        )

    def _record_action_transition(self, action: ActionRecord) -> None:
        if self._recorder is None:
            return
        self._recorder.record_action_transition(
            action.family,
            action.model_dump(mode="json"),
        )

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
                notes={
                    "family": contract.family,
                    "canonical_args": action.backend_request_json,
                    "condition_started_ns": {},
                    "progress": {},
                },
            )
            self._store.upsert_obligation(obligation)
            self._record_obligation_transition(obligation)
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
            if all(
                self._condition_met(
                    condition,
                    record,
                    now_ns,
                    evidence_map,
                    backend_context or {},
                )
                for condition in record.close_conditions
            ):
                record.status = "CONFIRMED"
                record.updated_mono_ns = now_ns
                self._store.upsert_obligation(record)
                self._record_obligation_transition(record)
                self._transition_related_action(record, confirmed=True)
                continue

            failure = self._first_failure_condition(
                record,
                now_ns,
                evidence_map,
                backend_context or {},
            )
            if failure is not None:
                record.status = self._failure_status(failure)
                record.updated_mono_ns = now_ns
                self._store.upsert_obligation(record)
                self._record_obligation_transition(record)
                self._transition_related_action(record, failure_condition=failure)
                continue

            record.updated_mono_ns = now_ns
            self._store.upsert_obligation(record)
            updated_open.append(record)
        return updated_open

    def _first_failure_condition(
        self,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
    ):
        for condition in record.failure_conditions:
            if self._condition_failed(condition, record, now_ns, evidence_map, backend_context):
                return condition
        return None

    def _condition_failed(
        self,
        condition,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
    ) -> bool:
        if isinstance(condition, dict):
            if "expr" in condition:
                return self._sustained_condition_met(
                    condition,
                    record,
                    now_ns,
                    evidence_map,
                    backend_context,
                    namespace="failure",
                )
            if timeout_key := next(iter(condition), None):
                timeout_sec = condition[timeout_key]
            else:
                return False
            if timeout_key == "no_progress_within_sec":
                return self._no_progress_timeout(
                    record,
                    now_ns,
                    backend_context,
                    float(timeout_sec),
                )
            if timeout_key == "timeout_from_canonical_arg":
                return self._action_timeout_from_canonical_arg(
                    record,
                    now_ns,
                    str(timeout_sec),
                )
            if timeout_key == "disarm_within_sec_after_touchdown":
                return self._disarm_timeout_after_touchdown(
                    record,
                    now_ns,
                    evidence_map,
                    backend_context,
                    float(timeout_sec),
                )
            timeout_key, timeout_sec = next(iter(condition.items()))
            if timeout_key.endswith("_within_sec") or timeout_key == "timeout_sec":
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

    def _condition_met(
        self,
        condition,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
    ) -> bool:
        if isinstance(condition, dict):
            if "expr" in condition:
                return self._sustained_condition_met(
                    condition,
                    record,
                    now_ns,
                    evidence_map,
                    backend_context,
                    namespace="close",
                )
            return False
        return evaluate_expression(
            str(condition),
            evidence_map,
            backend_context=backend_context,
        )

    def _sustained_condition_met(
        self,
        condition: dict,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
        *,
        namespace: str,
    ) -> bool:
        expression = str(condition["expr"])
        key = f"{namespace}:{stable_json_dumps(condition)}"
        timers = record.notes.setdefault("condition_started_ns", {})
        matched = evaluate_expression(
            expression,
            evidence_map,
            backend_context=backend_context,
        )
        if not matched:
            timers.pop(key, None)
            return False
        if "for_sec" not in condition:
            return True
        started_ns = timers.setdefault(key, now_ns)
        return (now_ns - int(started_ns)) / 1_000_000_000 >= float(condition["for_sec"])

    def _no_progress_timeout(
        self,
        record: ObligationRecord,
        now_ns: int,
        backend_context: dict,
        timeout_sec: float,
    ) -> bool:
        distance_m = deep_get(backend_context, "goto.distance_m")
        progress = record.notes.setdefault("progress", {})
        if distance_m is None:
            progress.setdefault("last_improved_mono_ns", record.created_mono_ns)
            return False
        best_distance = progress.get("best_distance_m")
        last_improved = int(progress.get("last_improved_mono_ns", record.created_mono_ns))
        if best_distance is None or float(distance_m) < float(best_distance) - 0.05:
            progress["best_distance_m"] = float(distance_m)
            progress["last_improved_mono_ns"] = now_ns
            return False
        return (now_ns - last_improved) / 1_000_000_000 > timeout_sec

    def _action_timeout_from_canonical_arg(
        self,
        record: ObligationRecord,
        now_ns: int,
        key: str,
    ) -> bool:
        timeout_sec = deep_get(record.notes, f"canonical_args.{key}")
        if timeout_sec is None:
            return False
        elapsed_sec = (now_ns - record.created_mono_ns) / 1_000_000_000
        return elapsed_sec > float(timeout_sec)

    def _disarm_timeout_after_touchdown(
        self,
        record: ObligationRecord,
        now_ns: int,
        evidence_map: dict,
        backend_context: dict,
        timeout_sec: float,
    ) -> bool:
        if not bool(deep_get(backend_context, "land.on_ground")):
            record.notes.setdefault("touchdown_started_ns", None)
            return False
        if evaluate_expression("vehicle.armed == false", evidence_map):
            record.notes.pop("touchdown_started_ns", None)
            return False
        started_ns = record.notes.get("touchdown_started_ns")
        if started_ns is None:
            record.notes["touchdown_started_ns"] = now_ns
            return False
        elapsed_sec = (now_ns - int(started_ns)) / 1_000_000_000
        return elapsed_sec > timeout_sec

    def _failure_status(self, condition) -> str:
        if isinstance(condition, dict):
            key = next(iter(condition))
            if key.endswith("_within_sec") or key in {
                "timeout_sec",
                "timeout_from_canonical_arg",
                "disarm_within_sec_after_touchdown",
            }:
                return "EXPIRED"
        return "FAILED"

    def _transition_related_action(
        self,
        record: ObligationRecord,
        *,
        confirmed: bool = False,
        failure_condition=None,
    ) -> None:
        action = self._store.get_action(record.related_action_id)
        if action is None:
            return
        if confirmed:
            if action.state in {ActionState.CONFIRMED, ActionState.FAILED, ActionState.EXPIRED}:
                return
            open_obligations = [
                obligation
                for obligation in self._store.list_obligations_for_action(record.related_action_id)
                if obligation.status == "OPEN"
            ]
            if open_obligations:
                return
            action.state = ActionState.CONFIRMED
            confirmed_obligations = action.confirm_evidence_json.setdefault(
                "confirmed_obligations",
                [],
            )
            if record.kind not in confirmed_obligations:
                confirmed_obligations.append(record.kind)
            self._store.upsert_action(action)
            self._record_action_transition(action)
            if self._event_bus is not None:
                self._event_bus.publish(
                    EventType.BACKEND_CONFIRM,
                    source="obligations",
                    payload_json={
                        "action_id": action.action_id,
                        "family": action.family,
                        "state": action.state.value,
                        "obligation_id": record.obligation_id,
                    },
                )
            return

        terminal_state = (
            ActionState.EXPIRED if record.status == "EXPIRED" else ActionState.FAILED
        )
        if action.state in {ActionState.CONFIRMED, ActionState.FAILED, ActionState.EXPIRED}:
            return
        action.state = terminal_state
        reason_code = stable_json_dumps(failure_condition)
        if reason_code not in action.failure_reason_codes:
            action.failure_reason_codes.append(reason_code)
        self._store.upsert_action(action)
        self._record_action_transition(action)
        if self._event_bus is not None:
            self._event_bus.publish(
                EventType.BACKEND_CONFIRM,
                source="obligations",
                payload_json={
                    "action_id": action.action_id,
                    "family": action.family,
                    "state": action.state.value,
                    "obligation_id": record.obligation_id,
                    "failure_condition": failure_condition,
                },
            )
