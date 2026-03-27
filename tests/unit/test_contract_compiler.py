from __future__ import annotations

from pathlib import Path

import pytest

from control_view.contracts.compiler import (
    ContractValidationError,
    compile_bundle,
)
from control_view.contracts.loader import load_contract_bundle
from control_view.contracts.models import FamilyContract

ROOT = Path(__file__).resolve().parents[2]


def test_contract_bundle_compiles() -> None:
    bundle = load_contract_bundle(ROOT)
    compiled = compile_bundle(bundle)

    assert set(compiled) == {"ARM", "TAKEOFF", "GOTO", "HOLD", "RTL", "LAND"}
    assert "offboard.stream.ok" in compiled["GOTO"].required_slots
    assert "offboard.stream.ok" not in compiled["ARM"].required_slots
    offboard_schema = bundle.fields["offboard.stream.ok"].value_schema
    assert offboard_schema is not None
    assert "value" in offboard_schema["properties"]
    assert "ok" not in offboard_schema["properties"]


def test_non_goto_family_cannot_use_offboard_guard() -> None:
    bundle = load_contract_bundle(ROOT)
    bad_family = FamilyContract.model_validate(
        {
            **bundle.families["HOLD"].model_dump(),
            "guard_slots": [*bundle.families["HOLD"].guard_slots, "offboard.stream.ok"],
        }
    )
    bundle.families["HOLD"] = bad_family

    with pytest.raises(ContractValidationError):
        compile_bundle(bundle)


def test_family_role_overlap_is_rejected() -> None:
    bundle = load_contract_bundle(ROOT)
    bad_family = FamilyContract.model_validate(
        {
            **bundle.families["ARM"].model_dump(),
            "support_slots": ["vehicle.connected"],
        }
    )
    bundle.families["ARM"] = bad_family

    with pytest.raises(ContractValidationError):
        compile_bundle(bundle)
