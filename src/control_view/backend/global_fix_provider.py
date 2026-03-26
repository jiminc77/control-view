from __future__ import annotations

from control_view.backend.base import BackendAdapter
from control_view.common.types import JSONDict


class GlobalFixProvider:
    def __init__(self, backend: BackendAdapter) -> None:
        self._backend = backend

    def current_fix(self) -> JSONDict | None:
        return self._backend.get_global_fix()

    def current_yaw(self) -> float | None:
        return self._backend.get_current_yaw()

