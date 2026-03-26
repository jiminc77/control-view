from __future__ import annotations

from dataclasses import dataclass

from control_view.common.types import JSONDict


@dataclass(slots=True)
class DebugCapabilityProbe:
    required_services: list[str]
    optional_action_services: list[str]


class RosMcpDebugAdapter:
    def __init__(self, config: JSONDict | None = None) -> None:
        self.config = config or {}
        self._probe = DebugCapabilityProbe(
            required_services=[
                "/rosapi/services",
                "/rosapi/topics",
                "/rosapi/service_type",
            ],
            optional_action_services=[
                "/rosapi/action_servers",
                "/rosapi/interfaces",
                "/rosapi/action_goal_details",
                "/rosapi/action_result_details",
                "/rosapi/action_feedback_details",
            ],
        )

    def probe_capabilities(self, available_services: set[str]) -> JSONDict:
        missing_required = [
            service
            for service in self._probe.required_services
            if service not in available_services
        ]
        optional_present = [
            service
            for service in self._probe.optional_action_services
            if service in available_services
        ]
        return {
            "role": "read_only_out_of_band_introspection",
            "required_services_ok": not missing_required,
            "missing_required_services": missing_required,
            "optional_action_services_present": optional_present,
            "actions_supported": bool(optional_present),
        }
