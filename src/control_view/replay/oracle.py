from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OracleDecision:
    family: str
    verdict: str
    blockers: list[str]
    canonical_args: dict[str, Any]


class RuleBasedOracle:
    def evaluate(self, family: str, full_state: dict[str, Any]) -> OracleDecision:
        blockers = []
        if not full_state.get("vehicle.connected", True):
            blockers.append("vehicle.connected")
        if family in {"TAKEOFF", "GOTO", "LAND"} and not full_state.get("vehicle.armed", True):
            blockers.append("vehicle.armed")
        verdict = "ACT" if not blockers else "REFRESH"
        return OracleDecision(
            family=family,
            verdict=verdict,
            blockers=blockers,
            canonical_args=full_state.get("canonical_args", {}),
        )

