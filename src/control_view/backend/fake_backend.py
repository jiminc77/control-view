from __future__ import annotations

from dataclasses import replace
from typing import Any

from control_view.backend.base import BackendActionResult, BackendAdapter, BackendSlotValue
from control_view.common.types import ActionState, JSONDict
from control_view.runtime.action_state import ack_state_for_family


class FakeBackend(BackendAdapter):
    def __init__(self) -> None:
        self._slots: dict[str, BackendSlotValue] = {}
        self._action_results: dict[str, BackendActionResult] = {}
        self._global_fix: JSONDict | None = None
        self._yaw: float | None = None

    def set_slot(
        self,
        slot_id: str,
        value: Any,
        *,
        authority_source: str = "fake_backend",
        frame_id: str | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        self._slots[slot_id] = BackendSlotValue(
            value=value,
            authority_source=authority_source,
            frame_id=frame_id,
            reason_codes=reason_codes or [],
        )

    def set_global_fix(self, fix: JSONDict | None) -> None:
        self._global_fix = fix

    def set_current_yaw(self, yaw: float | None) -> None:
        self._yaw = yaw

    def set_action_result(
        self,
        family: str,
        *,
        state: ActionState | None = None,
        response: JSONDict | None = None,
        confirm_evidence: JSONDict | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        self._action_results[family] = BackendActionResult(
            state=state or ack_state_for_family(family),
            response=response or {},
            confirm_evidence=confirm_evidence or {},
            reason_codes=reason_codes or [],
        )

    def get_current_snapshot(self, slot_ids: list[str]) -> dict[str, BackendSlotValue | None]:
        return {slot_id: self._slots.get(slot_id) for slot_id in slot_ids}

    def refresh_slot(self, slot_id: str) -> BackendSlotValue | None:
        slot = self._slots.get(slot_id)
        return replace(slot) if slot else None

    def get_global_fix(self) -> JSONDict | None:
        return self._global_fix

    def get_current_yaw(self) -> float | None:
        return self._yaw

    def _action(self, family: str, response: JSONDict | None = None) -> BackendActionResult:
        if family in self._action_results:
            return self._action_results[family]
        return BackendActionResult(
            state=ack_state_for_family(family),
            response=response or {"family": family.lower(), "accepted": True},
        )

    def set_mode(self, mode: str) -> BackendActionResult:
        return self._action("SET_MODE", {"mode_sent": True, "mode": mode})

    def arm(self) -> BackendActionResult:
        return self._action("ARM", {"success": True})

    def takeoff(self, target_altitude: float, geo_reference: JSONDict) -> BackendActionResult:
        return self._action(
            "TAKEOFF",
            {"success": True, "target_altitude": target_altitude, "geo_reference": geo_reference},
        )

    def goto(self, target_pose: JSONDict, canonical_args: JSONDict) -> BackendActionResult:
        return self._action(
            "GOTO",
            {"mode_sent": True, "target_pose": target_pose, "canonical_args": canonical_args},
        )

    def hold(self) -> BackendActionResult:
        return self._action("HOLD", {"mode_sent": True, "mode": "AUTO.LOITER"})

    def rtl(self) -> BackendActionResult:
        return self._action("RTL", {"mode_sent": True, "mode": "AUTO.RTL"})

    def land(self) -> BackendActionResult:
        return self._action("LAND", {"mode_sent": True, "mode": "AUTO.LAND"})

