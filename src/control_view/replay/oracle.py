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
    _FAMILY_GUARDS = {
        "ARM": ["vehicle.connected"],
        "TAKEOFF": [
            "vehicle.connected",
            "vehicle.armed",
            "pose.local",
            "estimator.health",
            "failsafe.state",
            "mission.spec.rev",
            "tool_registry.rev",
        ],
        "GOTO": [
            "vehicle.connected",
            "vehicle.armed",
            "pose.local",
            "estimator.health",
            "geofence.status",
            "tf.local_body",
            "failsafe.state",
            "offboard.stream.ok",
            "mission.spec.rev",
            "tool_registry.rev",
        ],
        "HOLD": [
            "vehicle.connected",
            "vehicle.armed",
            "vehicle.mode",
            "mission.spec.rev",
            "tool_registry.rev",
        ],
        "RTL": [
            "vehicle.connected",
            "vehicle.armed",
            "home.ready",
            "mission.spec.rev",
            "tool_registry.rev",
        ],
        "LAND": [
            "vehicle.connected",
            "vehicle.armed",
            "pose.local",
            "estimator.health",
            "mission.spec.rev",
            "tool_registry.rev",
        ],
    }

    def _evidence_map(self, full_state: dict[str, Any]) -> dict[str, Any]:
        evidence_map = full_state.get("evidence_map")
        return evidence_map if isinstance(evidence_map, dict) else {}

    def _entry(self, full_state: dict[str, Any], slot_id: str) -> dict[str, Any]:
        evidence_map = self._evidence_map(full_state)
        if slot_id in evidence_map and isinstance(evidence_map[slot_id], dict):
            return evidence_map[slot_id]
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

    def _valid_state(self, full_state: dict[str, Any], slot_id: str) -> str:
        entry = self._entry(full_state, slot_id)
        return str(entry.get("valid_state", "VALID"))

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
        return False

    def _blocker_kind(self, blockers: list[dict[str, Any]], kind: str) -> bool:
        return any(
            blocker.get("kind") == kind
            for blocker in blockers
            if isinstance(blocker, dict)
        )

    def _required_boolean(self, full_state: dict[str, Any], slot_id: str) -> bool:
        return self._valid(full_state, slot_id) and bool(self._value(full_state, slot_id))

    def evaluate(self, family: str, full_state: dict[str, Any]) -> OracleDecision:
        blocker_refreshable: dict[str, bool] = {}
        recorded_blockers = [
            blocker
            for blocker in full_state.get("blockers", [])
            if isinstance(blocker, dict)
        ]
        for slot_id in self._FAMILY_GUARDS.get(family, []):
            valid_state = self._valid_state(full_state, slot_id)
            if valid_state == "VALID":
                continue
            blocker_refreshable[slot_id] = valid_state in {"MISSING", "STALE", "UNCONFIRMED"}
        if family in {"ARM", "TAKEOFF", "GOTO", "HOLD", "RTL", "LAND"} and not (
            self._required_boolean(full_state, "vehicle.connected")
        ):
            blocker_refreshable["vehicle.connected"] = True
        if family in {"TAKEOFF", "GOTO", "HOLD", "RTL", "LAND"} and not (
            self._required_boolean(full_state, "vehicle.armed")
        ):
            blocker_refreshable["vehicle.armed"] = True
        if family in {"TAKEOFF", "GOTO", "LAND"}:
            score = float(self._value(full_state, "estimator.health", "score") or 0.0)
            if score < 0.8:
                blocker_refreshable["estimator.health"] = True
        if family == "GOTO":
            if not bool(self._value(full_state, "geofence.status", "target_inside")):
                blocker_refreshable["geofence.status"] = True
            if bool(self._value(full_state, "failsafe.state", "active")):
                blocker_refreshable["failsafe.state"] = True
            if not self._required_boolean(full_state, "offboard.stream.ok"):
                blocker_refreshable["offboard.stream.ok"] = True
        if family == "RTL" and not bool(self._value(full_state, "home.ready", "ready")):
            blocker_refreshable["home.ready"] = True
        if full_state.get("open_obligations"):
            blocker_refreshable["open_obligations"] = False
        blockers = sorted(blocker_refreshable)
        verdict = "ACT"
        if blockers:
            if all(blocker_refreshable[blocker] for blocker in blockers):
                verdict = "REFRESH"
            elif family in self._HIGH_RISK:
                verdict = "SAFE_HOLD"
            else:
                verdict = "REFUSE"
        nav_phase = str(self._value(full_state, "nav.progress", "phase") or "")
        labels = {
            "arrival": nav_phase == "ARRIVED" or bool(full_state.get("arrival_seen")),
            "touchdown": bool(
                self._value(full_state, "land", "on_ground")
                or full_state.get("touchdown_seen")
            ),
            "no_progress": self._has_reason_token(full_state, "no_progress"),
            "stale_action": (
                self._blocker_kind(recorded_blockers, "stale_slot")
                or self._blocker_kind(recorded_blockers, "invalidated_slot")
                or self._has_reason_token(full_state, "stale")
                or self._has_reason_token(full_state, "revision_update")
                or self._has_reason_token(full_state, "revision_bump")
                or self._has_reason_token(full_state, "critical_slot_revision_changed")
            ),
            "premature_transition": self._has_reason_token(full_state, "ack_without_confirm")
            or self._has_reason_token(full_state, "premature"),
            "degraded_safe_outcome": bool(full_state.get("degraded_safe_outcome"))
            or verdict == "SAFE_HOLD",
        }
        return OracleDecision(
            family=family,
            verdict=verdict,
            blockers=blockers,
            canonical_args=full_state.get("recorded_canonical_args")
            or full_state.get("canonical_args", {}),
            labels=labels,
        )
