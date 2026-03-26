from __future__ import annotations

import ast
from dataclasses import dataclass

from control_view.common.utils import normalize_bool_literals
from control_view.contracts.loader import ContractBundle
from control_view.contracts.models import (
    CompiledPredicate,
    CompiledViewSpec,
    FamilyContract,
    FieldSpec,
)


class ContractValidationError(ValueError):
    pass


@dataclass(slots=True)
class _AttrChainVisitor(ast.NodeVisitor):
    chains: set[str]

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        chain = self._flatten(node)
        if chain:
            self.chains.add(chain)

    def _flatten(self, node: ast.AST) -> str | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None


def _parse_expression(expression: str) -> ast.AST:
    normalized = normalize_bool_literals(expression)
    try:
        return ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise ContractValidationError(f"invalid predicate syntax: {expression}") from exc


def _collect_slot_dependencies(expression: str, known_slots: set[str]) -> list[str]:
    tree = _parse_expression(expression)
    visitor = _AttrChainVisitor(set())
    visitor.visit(tree)

    dependencies: set[str] = set()
    for chain in visitor.chains:
        parts = chain.split(".")
        prefixes = [".".join(parts[:index]) for index in range(1, len(parts) + 1)]
        matched = [prefix for prefix in prefixes if prefix in known_slots]
        if not matched:
            raise ContractValidationError(f"unknown slot reference in predicate: {chain}")
        dependencies.add(max(matched, key=len))
    return sorted(dependencies)


def _validate_fields(fields: dict[str, FieldSpec]) -> None:
    for field in fields.values():
        if not field.invalidators:
            raise ContractValidationError(f"{field.id} is missing invalidators")
        if not field.authority:
            raise ContractValidationError(f"{field.id} is missing authority policy")
        if not field.owner:
            raise ContractValidationError(f"{field.id} is missing owner")

    graph = {
        field.id: set(field.derivation.get("dependencies", [])) if field.derivation else set()
        for field in fields.values()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            raise ContractValidationError(f"circular derived-slot dependency detected at {node}")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            if dependency not in graph:
                raise ContractValidationError(f"{node} depends on unknown field {dependency}")
            dfs(dependency)
        visiting.remove(node)
        visited.add(node)

    for field_id in graph:
        dfs(field_id)


def _validate_family_slots(
    contract: FamilyContract,
    fields: dict[str, FieldSpec],
) -> dict[str, list[str]]:
    partition = {
        "guard": contract.guard_slots,
        "support": contract.support_slots,
        "confirm": contract.confirm_slots,
        "diagnostic": contract.diagnostic_slots,
    }
    for role, slots in partition.items():
        if len(slots) != len(set(slots)):
            raise ContractValidationError(f"{contract.family} repeats slots inside {role}")
        for slot_id in slots:
            if slot_id not in fields:
                raise ContractValidationError(
                    f"{contract.family} references unknown slot {slot_id}"
                )
    seen: dict[str, str] = {}
    for role, slots in partition.items():
        for slot_id in slots:
            if slot_id in seen:
                if {seen[slot_id], role} == {"guard", "confirm"}:
                    continue
                raise ContractValidationError(
                    f"{contract.family} overlaps slot {slot_id} across {seen[slot_id]} and {role}"
                )
            seen[slot_id] = role
    return partition


def _validate_family_policy(
    contract: FamilyContract,
    partition: dict[str, list[str]],
    compiled_predicates: list[CompiledPredicate],
    fields: dict[str, FieldSpec],
) -> None:
    if contract.argument_schema.get("type") != "object":
        raise ContractValidationError(f"{contract.family} argument_schema must be object")
    required = contract.argument_schema.get("required", [])
    properties = contract.argument_schema.get("properties", {})
    for field_name in required:
        if field_name not in properties:
            raise ContractValidationError(
                f"{contract.family} argument_schema requires unknown property {field_name}"
            )

    offboard_used = "offboard.stream.ok" in {
        slot for slots in partition.values() for slot in slots
    } or any(
        "offboard.stream.ok" in predicate.slot_dependencies
        for predicate in compiled_predicates
    )
    if contract.family != "GOTO" and offboard_used:
        raise ContractValidationError(f"{contract.family} violates OFFBOARD-only policy")

    for slot_id in partition["confirm"]:
        field = fields[slot_id]
        if field.status == "provisional":
            raise ContractValidationError(
                f"{contract.family} cannot use provisional slot {slot_id} in confirm slots"
            )

    invalidated_slots = contract.effects.get("invalidates", [])
    for slot_id in invalidated_slots:
        if slot_id not in fields:
            raise ContractValidationError(
                f"{contract.family} invalidates unknown slot {slot_id}"
            )


def compile_bundle(bundle: ContractBundle) -> dict[str, CompiledViewSpec]:
    _validate_fields(bundle.fields)
    known_slots = set(bundle.fields)
    compiled: dict[str, CompiledViewSpec] = {}

    for contract in bundle.families.values():
        partition = _validate_family_slots(contract, bundle.fields)
        compiled_predicates = [
            CompiledPredicate(
                id=predicate.id,
                expr=predicate.expr,
                slot_dependencies=_collect_slot_dependencies(predicate.expr, known_slots),
            )
            for predicate in contract.predicates
        ]
        _validate_family_policy(contract, partition, compiled_predicates, bundle.fields)
        required_slots = sorted({slot for slots in partition.values() for slot in slots})
        resolver_plan = {
            slot_id: {
                "owner": bundle.fields[slot_id].owner.value,
                "source": bundle.fields[slot_id].source,
            }
            for slot_id in required_slots
        }
        derivation_plan = {
            field_id: field.derivation
            for field_id, field in bundle.fields.items()
            if field.derivation and field_id in required_slots
        }
        blocker_templates = {
            slot_id: {
                "kind": "slot_invalid",
                "message": f"{slot_id} is not currently decision-valid",
            }
            for slot_id in required_slots
        }
        compiled[contract.family] = CompiledViewSpec(
            family=contract.family,
            required_slots=required_slots,
            role_partition=partition,
            predicate_plan=compiled_predicates,
            resolver_plan=resolver_plan,
            derivation_plan=derivation_plan,
            blocker_templates=blocker_templates,
            refresh_plan={
                "critical": partition["guard"] + partition["confirm"],
                "support": partition["support"],
            },
            commit_guard_slots=partition["guard"],
            obligation_templates=contract.obligation_templates,
            backend_action_plan=contract.backend_mapping,
            serializer_plan={"include_roles": ["guard", "support", "confirm"]},
        )
    return compiled
