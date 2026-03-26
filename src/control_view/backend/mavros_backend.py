from __future__ import annotations

from typing import Any

from control_view.backend.base import BackendActionResult, BackendAdapter, BackendSlotValue
from control_view.common.types import ActionState, JSONDict


class MavrosBackend(BackendAdapter):
    def __init__(self, config: JSONDict | None = None) -> None:
        self.config = config or {}
        self._snapshot_cache: dict[str, BackendSlotValue] = {}

    def _require_ros(self) -> Any:
        try:
            import rclpy  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "rclpy is required for MavrosBackend at runtime on Ubuntu 24.04 / ROS 2 Jazzy"
            ) from exc
        return rclpy

    def update_cached_slot(self, slot_id: str, value: BackendSlotValue) -> None:
        self._snapshot_cache[slot_id] = value

    def get_current_snapshot(self, slot_ids: list[str]) -> dict[str, BackendSlotValue | None]:
        return {slot_id: self._snapshot_cache.get(slot_id) for slot_id in slot_ids}

    def refresh_slot(self, slot_id: str) -> BackendSlotValue | None:
        return self._snapshot_cache.get(slot_id)

    def get_global_fix(self) -> JSONDict | None:
        slot = self._snapshot_cache.get("backend.global_fix")
        return slot.value if slot else None

    def get_current_yaw(self) -> float | None:
        slot = self._snapshot_cache.get("backend.current_yaw")
        if not slot:
            return None
        return float(slot.value)

    def _unsupported(self, action: str) -> BackendActionResult:
        self._require_ros()
        return BackendActionResult(
            state=ActionState.FAILED,
            response={"action": action},
            reason_codes=["mavros_runtime_not_connected"],
        )

    def set_mode(self, mode: str) -> BackendActionResult:
        return self._unsupported(f"set_mode:{mode}")

    def arm(self) -> BackendActionResult:
        return self._unsupported("arm")

    def takeoff(self, target_altitude: float, geo_reference: JSONDict) -> BackendActionResult:
        return self._unsupported(f"takeoff:{target_altitude}:{geo_reference}")

    def goto(self, target_pose: JSONDict, canonical_args: JSONDict) -> BackendActionResult:
        return self._unsupported(f"goto:{target_pose}:{canonical_args}")

    def hold(self) -> BackendActionResult:
        return self._unsupported("hold")

    def rtl(self) -> BackendActionResult:
        return self._unsupported("rtl")

    def land(self) -> BackendActionResult:
        return self._unsupported("land")

