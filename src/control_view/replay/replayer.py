from __future__ import annotations

from typing import Any

from control_view.contracts.models import LeaseToken
from control_view.replay.recorder import ReplayRecord
from control_view.service import ControlViewService


class ReplayRunner:
    def __init__(self, service: ControlViewService) -> None:
        self._service = service

    def replay(self, records: list[ReplayRecord]) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        latest_leases: dict[str, dict[str, Any]] = {}
        latest_args: dict[str, dict[str, Any]] = {}

        for record in records:
            if record.record_type == "control_view_request":
                result = self._service.get_control_view(
                    record.family,
                    record.payload.get("proposed_args", {}),
                )
                latest_args[record.family or ""] = result.canonical_args
                latest_leases[record.family or ""] = (
                    result.lease_token.model_dump(mode="json") if result.lease_token else {}
                )
                outputs.append(result.model_dump(mode="json"))
            elif record.record_type == "execute_guarded_request" and record.family:
                result = self._service.execute_guarded(
                    record.family,
                    record.payload.get("canonical_args", latest_args.get(record.family, {})),
                    LeaseToken.model_validate(record.payload["lease_token"]),
                )
                outputs.append(result.model_dump(mode="json"))
        return outputs
