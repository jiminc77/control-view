from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from control_view.common.time import monotonic_ns
from control_view.common.types import ValidState, Verdict
from control_view.common.utils import deep_get, normalize_bool_literals, to_namespace
from control_view.contracts.models import (
    Blocker,
    CompiledViewSpec,
    EvidenceEntry,
    FieldSpec,
    ObligationRecord,
)
from control_view.runtime.blockers import blocker_for_valid_state, make_blocker


class ExpressionValue:
    def __init__(self, value: Any, metadata: dict[str, Any] | None = None) -> None:
        self._value = value
        self._metadata = metadata or {}

    def __getattr__(self, name: str) -> Any:
        if name in self._metadata:
            return self._metadata[name]
        if isinstance(self._value, dict) and name in self._value:
            return ExpressionValue(self._value[name])
        raise AttributeError(name)

    def _comparison_value(self) -> Any:
        if isinstance(self._value, dict) and "value" in self._value:
            return self._value["value"]
        return self._value

    def __eq__(self, other: object) -> bool:
        return self._comparison_value() == other

    def __ne__(self, other: object) -> bool:
        return self._comparison_value() != other

    def __lt__(self, other: Any) -> bool:
        return self._comparison_value() < other

    def __le__(self, other: Any) -> bool:
        return self._comparison_value() <= other

    def __gt__(self, other: Any) -> bool:
        return self._comparison_value() > other

    def __ge__(self, other: Any) -> bool:
        return self._comparison_value() >= other

    def __bool__(self) -> bool:
        return bool(self._comparison_value())

    def unwrap(self) -> Any:
        return self._comparison_value()


def build_expression_context(
    evidence_map: dict[str, EvidenceEntry],
    *,
    canonical_args: dict[str, Any] | None = None,
    backend_context: dict[str, Any] | None = None,
) -> Any:
    context: dict[str, Any] = {"args": canonical_args or {}, "backend": backend_context or {}}
    for slot_id, entry in evidence_map.items():
        parts = slot_id.split(".")
        cursor = context
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = ExpressionValue(
            entry.value_json,
            metadata={
                "valid_state": entry.valid_state.value,
                "revision": entry.revision,
                "authority_source": entry.authority_source,
                "frame_id": entry.frame_id,
            },
        )
    return to_namespace(context)


def evaluate_expression(
    expression: str,
    evidence_map: dict[str, EvidenceEntry],
    *,
    canonical_args: dict[str, Any] | None = None,
    backend_context: dict[str, Any] | None = None,
) -> bool:
    context = build_expression_context(
        evidence_map,
        canonical_args=canonical_args,
        backend_context=backend_context,
    )
    try:
        result = eval(normalize_bool_literals(expression), {"__builtins__": {}}, vars(context))
    except (AttributeError, KeyError, NameError, TypeError):
        return False
    if isinstance(result, ExpressionValue):
        return bool(result)
    return bool(result)


@dataclass(slots=True)
class GovernorEvaluation:
    verdict: Verdict
    blockers: list[Blocker]
    critical_slots: dict[str, EvidenceEntry]
    support_slots: dict[str, EvidenceEntry]


class Governor:
    def __init__(self, fields: dict[str, FieldSpec]) -> None:
        self._fields = fields

    def evaluate(
        self,
        contract,
        compiled: CompiledViewSpec,
        evidence_map: dict[str, EvidenceEntry],
        *,
        canonical_args: dict[str, Any],
        open_obligations: list[ObligationRecord],
        extra_blockers: list[Blocker] | None = None,
        backend_context: dict[str, Any] | None = None,
    ) -> GovernorEvaluation:
        blockers = list(extra_blockers or [])
        critical_slots = {
            slot_id: evidence_map[slot_id]
            for slot_id in compiled.role_partition["guard"]
            if slot_id in evidence_map
        }
        support_slots = {
            slot_id: evidence_map[slot_id]
            for slot_id in compiled.role_partition["support"]
            if slot_id in evidence_map
        }

        for slot_id in compiled.role_partition["guard"]:
            field = self._fields[slot_id]
            entry = evidence_map.get(slot_id)
            state = self._resolve_valid_state(field, entry, contract.risk_class)
            if entry and state != entry.valid_state:
                entry.valid_state = state
            if state != ValidState.VALID:
                blockers.append(blocker_for_valid_state(slot_id, state, entry))

        if open_obligations:
            blockers.append(
                make_blocker(
                    slot_id="open_obligations",
                    kind="pending_transition",
                    severity="high",
                    message="another action transition is still pending confirmation",
                    refreshable=False,
                    refresh_hint="wait for confirmation or inspect ledger",
                )
            )

        if not blockers:
            for predicate in compiled.predicate_plan:
                if not evaluate_expression(
                    predicate.expr,
                    evidence_map,
                    canonical_args=canonical_args,
                    backend_context=backend_context,
                ):
                    slot_id = (
                        predicate.slot_dependencies[0]
                        if predicate.slot_dependencies
                        else "predicate"
                    )
                    blockers.append(
                        make_blocker(
                            slot_id=slot_id,
                            kind="predicate_failed",
                            severity="high",
                            message=f"{predicate.id} failed for {contract.family}",
                            refreshable=True,
                            refresh_hint=f"refresh {slot_id}",
                            evidence=evidence_map.get(slot_id),
                        )
                    )
        return GovernorEvaluation(
            verdict=self.finalize_verdict(blockers, contract.risk_class),
            blockers=blockers,
            critical_slots=critical_slots,
            support_slots=support_slots,
        )

    def _resolve_valid_state(
        self,
        field: FieldSpec,
        entry: EvidenceEntry | None,
        risk_class: str,
    ) -> ValidState:
        if entry is None:
            return ValidState.MISSING
        if entry.valid_state == ValidState.MISSING:
            return entry.valid_state
        if any(reason in field.invalidators for reason in entry.reason_codes):
            return ValidState.INVALIDATED
        ttl_ms = deep_get(field.freshness, f"ttl_ms.{risk_class}")
        if ttl_ms is not None:
            age_ms = (monotonic_ns() - entry.received_mono_ns) / 1_000_000
            if age_ms > float(ttl_ms):
                return ValidState.STALE
        return ValidState.VALID

    @staticmethod
    def finalize_verdict(blockers: list[Blocker], risk_class: str) -> Verdict:
        if not blockers:
            return Verdict.ACT
        if all(blocker.refreshable for blocker in blockers):
            return Verdict.REFRESH
        if risk_class == "high":
            return Verdict.SAFE_HOLD
        return Verdict.REFUSE
