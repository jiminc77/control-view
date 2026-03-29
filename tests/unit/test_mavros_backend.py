from __future__ import annotations

import sys
import types

from control_view.backend.base import BackendSlotValue
from control_view.backend.mavros_backend import MavrosBackend
from control_view.common.types import ActionState


def test_takeoff_uses_relative_altitude_for_command_tol(monkeypatch) -> None:
    backend = MavrosBackend()
    backend._current_yaw = 0.42
    backend.refresh_slot = lambda slot_id: BackendSlotValue(  # type: ignore[method-assign]
        value={"position": {"z": 1.25}},
        authority_source="test",
    )

    captured: dict[str, float] = {}

    class _FakeCommandTOL:
        class Request:
            def __init__(self) -> None:
                self.min_pitch = 0.0
                self.yaw = 0.0
                self.latitude = 0.0
                self.longitude = 0.0
                self.altitude = 0.0

    monkeypatch.setitem(
        sys.modules,
        "mavros_msgs.srv",
        types.SimpleNamespace(CommandTOL=_FakeCommandTOL),
    )

    def _fake_call_service(client_name: str, request: object, timeout_sec: float):
        captured["altitude"] = request.altitude
        captured["yaw"] = request.yaw
        captured["latitude"] = request.latitude
        captured["longitude"] = request.longitude
        captured["timeout_sec"] = timeout_sec
        return types.SimpleNamespace(success=True, result=0)

    monkeypatch.setattr(backend, "_call_service", _fake_call_service)
    monkeypatch.setattr(backend, "_timeout", lambda key, default=2.0: 5.0)

    result = backend.takeoff(
        3.0,
        {
            "latitude": 47.3979709,
            "longitude": 8.5461636,
            "altitude": 47.48522838449087,
        },
    )

    assert result.state == ActionState.ACKED_STRONG
    assert captured["altitude"] == 3.0
    assert captured["yaw"] == 0.42
    assert captured["latitude"] == 47.3979709
    assert captured["longitude"] == 8.5461636
    assert captured["timeout_sec"] == 5.0
