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
        if fault_name == "geofence_revision_update":
            return self._annotate(mutated, fault_name, revision=params.get("revision", 2))
        if fault_name == "ack_without_confirm":
            return self._annotate(mutated, fault_name, confirmed=False)
        if fault_name == "offboard_stream_loss":
            return self._annotate(mutated, fault_name, stream_ok=False)
        if fault_name == "stale_pose":
            return self._annotate(mutated, fault_name, stale_ms=params.get("stale_ms", 1000))
        return self._annotate(mutated, fault_name)

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
