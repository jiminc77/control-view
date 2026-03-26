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
        return [
            {
                **record,
                "fault_injection": {
                    "fault_name": fault_name,
                    **payload,
                },
            }
            for record in records
        ]
