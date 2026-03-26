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
    _HIGH_RISK = {"TAKEOFF", "GOTO", "RTL", "LAND"}

    def _entry(self, full_state: dict[str, Any], slot_id: str) -> dict[str, Any]:
        buckets = [
            full_state.get("critical_slots", {}),
            full_state.get("support_slots", {}),
            full_state.get("full_state", {}),
        ]
        for bucket in buckets:
            if slot_id in bucket:
                value = bucket[slot_id]
                if isinstance(value, dict) and "value_json" in value:
                    return value
                if isinstance(value, dict):
                    return {"value_json": value, "valid_state": value.get("valid_state", "VALID")}
                return {"value_json": {"value": value}, "valid_state": "VALID"}
        value = full_state.get(slot_id)
        if isinstance(value, dict):
            return {"value_json": value, "valid_state": value.get("valid_state", "VALID")}
        return {"value_json": {"value": value}, "valid_state": "VALID"}

    def _value(
        self,
        full_state: dict[str, Any],
        slot_id: str,
        dotted_path: str | None = None,
    ) -> Any:
        entry = self._entry(full_state, slot_id)
        value_json = entry.get("value_json", {})
        if dotted_path is None:
            if isinstance(value_json, dict) and "value" in value_json:
                return value_json["value"]
            return value_json
        current = value_json
        for part in dotted_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _valid(self, full_state: dict[str, Any], slot_id: str) -> bool:
        entry = self._entry(full_state, slot_id)
        return entry.get("valid_state", "VALID") == "VALID"

    def evaluate(self, family: str, full_state: dict[str, Any]) -> OracleDecision:
        blockers = []
        if not self._valid(full_state, "vehicle.connected") or not bool(
            self._value(full_state, "vehicle.connected")
        ):
            blockers.append("vehicle.connected")
        if family in {"TAKEOFF", "GOTO", "HOLD", "RTL", "LAND"} and (
            not self._valid(full_state, "vehicle.armed")
            or not bool(self._value(full_state, "vehicle.armed"))
        ):
            blockers.append("vehicle.armed")
        if family == "RTL" and not bool(self._value(full_state, "home.ready", "ready")):
            blockers.append("home.ready")
        if family in {"TAKEOFF", "GOTO", "LAND"}:
            score = float(self._value(full_state, "estimator.health", "score") or 0.0)
            if score < 0.8:
                blockers.append("estimator.health")
        if family == "GOTO":
            if not bool(self._value(full_state, "geofence.status", "target_inside")):
                blockers.append("geofence.status")
            if bool(self._value(full_state, "failsafe.state", "active")):
                blockers.append("failsafe.state")
        verdict = "ACT"
        if blockers:
            safety_blockers = {"failsafe.state", "geofence.status"}
            if family in self._HIGH_RISK and any(
                blocker in safety_blockers for blocker in blockers
            ):
                verdict = "SAFE_HOLD"
            else:
                verdict = "REFRESH"
        return OracleDecision(
            family=family,
            verdict=verdict,
            blockers=blockers,
            canonical_args=full_state.get("canonical_args", {}),
        )
