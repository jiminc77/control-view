from __future__ import annotations

import subprocess
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

    def probe_runtime_services(self) -> set[str]:
        command = self.config.get("service_list_command", ["ros2", "service", "list"])
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=float(self.config.get("probe_timeout_sec", 2.0)),
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return set()
        return {
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip()
        }

    def probe_runtime_capabilities(self) -> JSONDict:
        available_services = self.probe_runtime_services()
        capabilities = self.probe_capabilities(available_services)
        capabilities["available_services"] = sorted(available_services)
        return capabilities

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
