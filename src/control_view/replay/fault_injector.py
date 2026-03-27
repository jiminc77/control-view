from __future__ import annotations

from copy import deepcopy
from typing import Any


class FaultInjector:
    def apply(
        self,
        records: list[dict[str, Any]],
        fault_name: str,
        **params: Any,
    ) -> list[dict[str, Any]]:
        mutated = deepcopy(records)
        fault_defaults = {
            "pose_message_delay": {"stale_ms": params.get("stale_ms", 1000)},
            "stale_pose": {"stale_ms": params.get("stale_ms", 1000)},
            "estimator_reset_event": {"reason_code": "estimator_reset_detected"},
            "vehicle_reconnect": {"reason_code": "vehicle_reconnect"},
            "operator_mode_override": {"mode": params.get("mode", "POSCTL")},
            "geofence_revision_update": {"revision": params.get("revision", 2)},
            "tool_registry_revision_bump": {"revision": params.get("revision", 2)},
            "ack_without_confirm": {"confirmed": False},
            "offboard_warmup_failure": {"reason_code": "offboard_warmup_failed"},
            "offboard_stream_loss": {"reason_code": "offboard_stream_lost"},
            "no_progress_during_goto": {"reason_code": "no_progress_within_sec:3.0"},
            "stale_transform": {"reason_code": "stale_transform"},
            "battery_reserve_drop": {"margin_fraction": params.get("margin_fraction", 0.05)},
        }
        payload = fault_defaults.get(fault_name, params)
        for record in mutated:
            record["fault_injection"] = {"fault_name": fault_name, **payload}
            self._apply_fault(record, fault_name, **payload)
        return mutated

    def _apply_fault(self, record: dict[str, Any], fault_name: str, **payload: Any) -> None:
        if fault_name in {"pose_message_delay", "stale_pose"}:
            self._mark_slot_fault(
                record,
                slot_id="pose.local",
                valid_state="STALE",
                reason_code="stale_pose",
                blocker_kind="stale_slot",
                verdict="REFRESH",
            )
            return
        if fault_name == "stale_transform":
            self._mark_slot_fault(
                record,
                slot_id="tf.local_body",
                valid_state="STALE",
                reason_code=str(payload["reason_code"]),
                blocker_kind="stale_slot",
                verdict="REFRESH",
            )
            return
        if fault_name == "estimator_reset_event":
            self._mark_slot_fault(
                record,
                slot_id="estimator.health",
                valid_state="INVALIDATED",
                reason_code=str(payload["reason_code"]),
                blocker_kind="invalidated_slot",
                verdict="REFRESH",
            )
            return
        if fault_name == "vehicle_reconnect":
            self._mark_slot_fault(
                record,
                slot_id="vehicle.connected",
                valid_state="INVALIDATED",
                reason_code=str(payload["reason_code"]),
                blocker_kind="invalidated_slot",
                verdict="REFRESH",
                forced_value=False,
            )
            return
        if fault_name == "geofence_revision_update":
            self._bump_artifact_revision(record, "geofence", int(payload["revision"]))
            self._mark_slot_fault(
                record,
                slot_id="geofence.status",
                valid_state="INVALIDATED",
                reason_code="geofence_revision_update",
                blocker_kind="invalidated_slot",
                verdict="REFRESH",
            )
            self._update_slot_field(
                record,
                "geofence.status",
                ["artifact_revision"],
                int(payload["revision"]),
            )
            return
        if fault_name == "tool_registry_revision_bump":
            self._bump_artifact_revision(record, "tool_registry", int(payload["revision"]))
            self._mark_slot_fault(
                record,
                slot_id="tool_registry.rev",
                valid_state="INVALIDATED",
                reason_code="tool_registry_revision_bump",
                blocker_kind="invalidated_slot",
                verdict="REFRESH",
            )
            return
        if fault_name == "battery_reserve_drop":
            self._update_slot_field(
                record,
                "battery.margin",
                ["margin_fraction"],
                float(payload["margin_fraction"]),
            )
            return
        if fault_name == "ack_without_confirm":
            self._set_action_state(
                record,
                family="GOTO",
                new_state="ACKED_WEAK",
                allowed_states={"CONFIRMED"},
                reason_code="ack_without_confirm",
            )
            self._set_action_state(
                record,
                family="HOLD",
                new_state="ACKED_WEAK",
                allowed_states={"CONFIRMED"},
                reason_code="ack_without_confirm",
            )
            self._set_action_state(
                record,
                family="RTL",
                new_state="ACKED_WEAK",
                allowed_states={"CONFIRMED"},
                reason_code="ack_without_confirm",
            )
            self._set_action_state(
                record,
                family="LAND",
                new_state="ACKED_WEAK",
                allowed_states={"CONFIRMED"},
                reason_code="ack_without_confirm",
            )
            self._set_obligation_status(
                record,
                family=None,
                new_status="OPEN",
                allowed_statuses={"CONFIRMED"},
            )
            self._mark_mission_failure(record, "ack_without_confirm")
            return
        if fault_name == "offboard_warmup_failure":
            self._set_action_state(
                record,
                family="GOTO",
                new_state="FAILED",
                allowed_states={"ACKED_WEAK", "CONFIRMED"},
                reason_code=str(payload["reason_code"]),
            )
            self._set_obligation_status(
                record,
                family="GOTO",
                new_status="FAILED",
                allowed_statuses={"OPEN", "CONFIRMED"},
            )
            self._mark_mission_failure(record, str(payload["reason_code"]))
            return
        if fault_name == "offboard_stream_loss":
            self._mark_slot_fault(
                record,
                slot_id="offboard.stream.ok",
                valid_state="INVALIDATED",
                reason_code=str(payload["reason_code"]),
                blocker_kind="invalidated_slot",
                verdict="REFRESH",
                forced_value=False,
            )
            self._set_action_state(
                record,
                family="GOTO",
                new_state="FAILED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code=str(payload["reason_code"]),
            )
            self._set_obligation_status(
                record,
                family="GOTO",
                new_status="FAILED",
                allowed_statuses={"OPEN", "CONFIRMED"},
            )
            self._mark_mission_failure(record, str(payload["reason_code"]))
            return
        if fault_name == "no_progress_during_goto":
            self._set_action_state(
                record,
                family="GOTO",
                new_state="EXPIRED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code=str(payload["reason_code"]),
            )
            self._set_obligation_status(
                record,
                family="GOTO",
                new_status="EXPIRED",
                allowed_statuses={"OPEN", "CONFIRMED"},
            )
            self._mark_mission_failure(record, str(payload["reason_code"]))
            return
        if fault_name == "operator_mode_override":
            self._update_slot_field(record, "vehicle.mode", ["value"], str(payload["mode"]))
            self._set_action_state(
                record,
                family="GOTO",
                new_state="FAILED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code="operator_mode_override",
            )
            self._set_action_state(
                record,
                family="HOLD",
                new_state="FAILED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code="operator_mode_override",
            )
            self._set_action_state(
                record,
                family="RTL",
                new_state="FAILED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code="operator_mode_override",
            )
            self._set_action_state(
                record,
                family="LAND",
                new_state="FAILED",
                allowed_states={"CONFIRMED", "ACKED_WEAK"},
                reason_code="operator_mode_override",
            )
            self._set_obligation_status(
                record,
                family=None,
                new_status="FAILED",
                allowed_statuses={"OPEN", "CONFIRMED"},
            )
            self._mark_mission_failure(record, "operator_mode_override")

    def _target(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = record.get("payload")
        if isinstance(payload, dict):
            return payload
        return record

    def _family(self, record: dict[str, Any]) -> str | None:
        family = record.get("family")
        if isinstance(family, str):
            return family
        target = self._target(record)
        family = target.get("family")
        return str(family) if isinstance(family, str) else None

    def _mark_slot_fault(
        self,
        record: dict[str, Any],
        *,
        slot_id: str,
        valid_state: str,
        reason_code: str,
        blocker_kind: str,
        verdict: str,
        forced_value: Any | None = None,
    ) -> None:
        target = self._target(record)
        slot = self._slot_entry(target, slot_id)
        if slot is None:
            return
        slot["valid_state"] = valid_state
        reason_codes = slot.setdefault("reason_codes", [])
        if reason_code not in reason_codes:
            reason_codes.append(reason_code)
        if forced_value is not None:
            value_json = slot.setdefault("value_json", {})
            if "value" in value_json or not isinstance(value_json, dict):
                slot["value_json"] = {"value": forced_value}
            elif slot_id == "vehicle.connected":
                value_json["value"] = forced_value
            elif slot_id == "offboard.stream.ok":
                value_json["value"] = forced_value
        blockers = target.setdefault("blockers", [])
        if isinstance(blockers, list) and not any(
            blocker.get("slot_id") == slot_id and blocker.get("kind") == blocker_kind
            for blocker in blockers
            if isinstance(blocker, dict)
        ):
            blockers.append(
                {
                    "slot_id": slot_id,
                    "kind": blocker_kind,
                    "severity": "high",
                    "message": f"{slot_id} became {valid_state.lower()} during replay",
                    "refreshable": blocker_kind in {"stale_slot", "invalidated_slot"},
                    "refresh_hint": f"refresh {slot_id}",
                    "evidence_summary": {},
                }
            )
        if "verdict" in target:
            target["verdict"] = verdict

    def _slot_entry(self, target: dict[str, Any], slot_id: str) -> dict[str, Any] | None:
        for bucket_name in ("critical_slots", "support_slots"):
            bucket = target.get(bucket_name)
            if not isinstance(bucket, dict) or slot_id not in bucket:
                continue
            entry = bucket.get(slot_id)
            if isinstance(entry, dict):
                return entry
        return None

    def _update_slot_field(
        self,
        record: dict[str, Any],
        slot_id: str,
        path: list[str],
        value: Any,
    ) -> None:
        target = self._target(record)
        entry = self._slot_entry(target, slot_id)
        if entry is None:
            return
        cursor = entry.setdefault("value_json", {})
        if not isinstance(cursor, dict):
            return
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
            if not isinstance(cursor, dict):
                return
        cursor[path[-1]] = value

    def _bump_artifact_revision(
        self,
        record: dict[str, Any],
        artifact_name: str,
        revision: int,
    ) -> None:
        target = self._target(record)
        artifact_revisions = target.get("artifact_revisions")
        if not isinstance(artifact_revisions, list):
            artifact_revisions = record.get("artifact_revisions")
        if not isinstance(artifact_revisions, list):
            return
        replaced = False
        for item in artifact_revisions:
            if not isinstance(item, dict) or item.get("artifact_name") != artifact_name:
                continue
            item["revision"] = revision
            payload = item.get("payload")
            if isinstance(payload, dict):
                payload["revision"] = revision
            replaced = True
        if not replaced:
            artifact_revisions.append(
                {
                    "artifact_name": artifact_name,
                    "revision": revision,
                    "payload": {"revision": revision},
                }
            )
        if target.get("artifact_revisions") is None and "artifact_revisions" not in target:
            record["artifact_revisions"] = artifact_revisions

    def _set_action_state(
        self,
        record: dict[str, Any],
        *,
        family: str,
        new_state: str,
        allowed_states: set[str],
        reason_code: str,
    ) -> None:
        if self._family(record) != family:
            return
        target = self._target(record)
        state_key = "state" if "state" in target else "status" if "status" in target else None
        if state_key is None:
            return
        current_state = target.get(state_key)
        if current_state not in allowed_states:
            return
        target[state_key] = new_state
        if new_state.startswith("ACKED_"):
            target["ack_strength"] = "weak" if new_state == "ACKED_WEAK" else "strong"
        if isinstance(target.get("confirm_evidence_json"), dict):
            target["confirm_evidence_json"].pop("confirmed_obligations", None)
        failure_reason_codes = target.setdefault("failure_reason_codes", [])
        if isinstance(failure_reason_codes, list) and reason_code not in failure_reason_codes:
            failure_reason_codes.append(reason_code)
        if state_key == "status":
            target["abort_reason"] = None if new_state.startswith("ACKED_") else reason_code

    def _set_obligation_status(
        self,
        record: dict[str, Any],
        *,
        family: str | None,
        new_status: str,
        allowed_statuses: set[str],
    ) -> None:
        target = self._target(record)
        if "obligation_id" not in target or "status" not in target:
            return
        if family is not None and self._family(record) != family:
            return
        if target.get("status") not in allowed_statuses:
            return
        target["status"] = new_status

    def _mark_mission_failure(self, record: dict[str, Any], reason: str) -> None:
        target = self._target(record)
        if target.get("phase") != "end":
            return
        target["success"] = False
        failure_reasons = target.setdefault("failure_reasons", [])
        if isinstance(failure_reasons, list) and reason not in failure_reasons:
            failure_reasons.append(reason)
