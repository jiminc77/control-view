from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from control_view.contracts.models import FamilyContract, FieldSpec


@dataclass(slots=True)
class ContractBundle:
    fields: dict[str, FieldSpec]
    families: dict[str, FamilyContract]


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a mapping")
    return data


def load_field_specs(root: Path) -> dict[str, FieldSpec]:
    return {
        spec.id: spec
        for spec in (
            FieldSpec.model_validate(_load_yaml(path))
            for path in sorted((root / "contracts" / "fields").glob("*.yaml"))
        )
    }


def load_family_contracts(root: Path) -> dict[str, FamilyContract]:
    return {
        contract.family: contract
        for contract in (
            FamilyContract.model_validate(_load_yaml(path))
            for path in sorted((root / "contracts" / "families").glob("*.yaml"))
        )
    }


def load_contract_bundle(root: Path) -> ContractBundle:
    return ContractBundle(fields=load_field_specs(root), families=load_family_contracts(root))

