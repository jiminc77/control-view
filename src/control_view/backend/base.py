from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from control_view.common.types import ActionState, JSONDict


@dataclass(slots=True)
class BackendSlotValue:
    value: Any
    authority_source: str = "backend"
    source_header_stamp: str | None = None
    frame_id: str | None = None
    reason_codes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BackendActionResult:
    state: ActionState
    response: JSONDict = field(default_factory=dict)
    confirm_evidence: JSONDict = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)


class BackendAdapter(ABC):
    @abstractmethod
    def get_current_snapshot(self, slot_ids: list[str]) -> dict[str, BackendSlotValue | None]:
        raise NotImplementedError

    @abstractmethod
    def refresh_slot(self, slot_id: str) -> BackendSlotValue | None:
        raise NotImplementedError

    @abstractmethod
    def get_global_fix(self) -> JSONDict | None:
        raise NotImplementedError

    @abstractmethod
    def get_current_yaw(self) -> float | None:
        raise NotImplementedError

    @abstractmethod
    def get_runtime_context(self) -> JSONDict:
        raise NotImplementedError

    @abstractmethod
    def set_mode(self, mode: str) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def arm(self) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def takeoff(self, target_altitude: float, geo_reference: JSONDict) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def goto(self, target_pose: JSONDict, canonical_args: JSONDict) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def hold(self) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def rtl(self) -> BackendActionResult:
        raise NotImplementedError

    @abstractmethod
    def land(self) -> BackendActionResult:
        raise NotImplementedError
