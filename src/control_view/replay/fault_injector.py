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
            "offboard_warmup_failure": {"warmup_ok": False},
            "offboard_stream_loss": {"stream_ok": False},
            "no_progress_during_goto": {"distance_delta_m": 0.0},
            "stale_transform": {"reason_code": "stale_transform"},
            "battery_reserve_drop": {"margin_fraction": params.get("margin_fraction", 0.05)},
        }
        return self._annotate(mutated, fault_name, **fault_defaults.get(fault_name, params))

    def _annotate(
        self,
        records: list[dict[str, Any]],
        fault_name: str,
        **payload: Any,
    ) -> list[dict[str, Any]]:
        mutated: list[dict[str, Any]] = []
        for record in records:
            updated = {
                **record,
                "fault_injection": {
                    "fault_name": fault_name,
                    **payload,
                },
            }
            if fault_name in {"pose_message_delay", "stale_pose"}:
                updated = self._mark_slot(updated, "pose.local", "STALE", "stale_pose")
            elif fault_name == "stale_transform":
                updated = self._mark_slot(updated, "tf.local_body", "STALE", "stale_transform")
            elif fault_name == "offboard_stream_loss":
                updated = self._mark_slot(
                    updated,
                    "offboard.stream.ok",
                    "INVALIDATED",
                    "offboard_stream_lost",
                )
            elif fault_name == "battery_reserve_drop":
                margin_fraction = float(payload["margin_fraction"])
                support_slots = deepcopy(updated.get("support_slots", {}))
                if "battery.margin" in support_slots:
                    support_slots["battery.margin"]["value_json"]["margin_fraction"] = margin_fraction
                    updated["support_slots"] = support_slots
            elif fault_name == "geofence_revision_update":
                updated = self._bump_artifact_revision(updated, "geofence", int(payload["revision"]))
            elif fault_name == "tool_registry_revision_bump":
                updated = self._bump_artifact_revision(updated, "tool_registry", int(payload["revision"]))
            elif fault_name == "ack_without_confirm" and updated.get("status") == "CONFIRMED":
                updated["status"] = "ACKED_WEAK"
            mutated.append(updated)
        return mutated

    def _mark_slot(
        self,
        record: dict[str, Any],
        slot_id: str,
        valid_state: str,
        reason_code: str,
    ) -> dict[str, Any]:
        for bucket_name in ("critical_slots", "support_slots"):
            bucket = deepcopy(record.get(bucket_name, {}))
            if slot_id not in bucket:
                continue
            bucket[slot_id]["valid_state"] = valid_state
            reason_codes = bucket[slot_id].setdefault("reason_codes", [])
            if reason_code not in reason_codes:
                reason_codes.append(reason_code)
            record[bucket_name] = bucket
        return record

    def _bump_artifact_revision(
        self,
        record: dict[str, Any],
        artifact_name: str,
        revision: int,
    ) -> dict[str, Any]:
        artifact_revisions = deepcopy(record.get("artifact_revisions", []))
        replaced = False
        for item in artifact_revisions:
            if item.get("artifact_name") == artifact_name:
                item["revision"] = revision
                replaced = True
        if not replaced:
            artifact_revisions.append({"artifact_name": artifact_name, "revision": revision, "payload": {}})
        record["artifact_revisions"] = artifact_revisions
        return record
