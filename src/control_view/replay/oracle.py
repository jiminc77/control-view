from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OracleDecision:
    family: str
    verdict: str
    blockers: list[str]
    canonical_args: dict[str, Any]
    labels: dict[str, bool]


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

    def _has_reason_token(self, full_state: dict[str, Any], token: str) -> bool:
        lowered = token.lower()
        for key in ("fault_name", "reason_code"):
            value = full_state.get(key)
            if isinstance(value, str) and lowered in value.lower():
                return True
        for item in full_state.get("blockers", []):
            if not isinstance(item, dict):
                continue
            joined = " ".join(
                str(item.get(field, ""))
                for field in ("kind", "message", "slot_id")
            ).lower()
            if lowered in joined:
                return True
        for item in full_state.get("normalized_event_history", []):
            payload = item.get("payload", item)
            if isinstance(payload, dict) and lowered in str(payload).lower():
                return True
        return False

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
        nav_phase = str(self._value(full_state, "nav.progress", "phase") or "")
        labels = {
            "arrival": nav_phase == "ARRIVED" or bool(full_state.get("arrival_seen")),
            "touchdown": bool(
                self._value(full_state, "land", "on_ground")
                or full_state.get("touchdown_seen")
            ),
            "no_progress": self._has_reason_token(full_state, "no_progress"),
            "stale_action": (
                any(
                    blocker.get("kind") in {"stale_slot", "invalidated_slot"}
                    for blocker in full_state.get("blockers", [])
                    if isinstance(blocker, dict)
                )
                or self._has_reason_token(full_state, "stale")
                or self._has_reason_token(full_state, "revision")
            ),
            "premature_transition": bool(full_state.get("open_obligations")) or any(
                blocker.get("kind") == "pending_transition"
                for blocker in full_state.get("blockers", [])
                if isinstance(blocker, dict)
            ),
            "degraded_safe_outcome": bool(full_state.get("degraded_safe_outcome"))
            or verdict == "SAFE_HOLD",
        }
        return OracleDecision(
            family=family,
            verdict=verdict,
            blockers=blockers,
            canonical_args=full_state.get("canonical_args", {}),
            labels=labels,
        )
