from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from control_view.backend.base import BackendActionResult, BackendAdapter, BackendSlotValue
from control_view.common.types import ActionState, JSONDict
from control_view.replay.recorder import ReplayRecorder

COMMON_RAW_SLOTS = [
    "vehicle.connected",
    "vehicle.armed",
    "vehicle.mode",
    "pose.local",
    "velocity.local",
    "estimator.health",
    "failsafe.state",
    "home.position",
    "home.ready",
    "offboard.stream.ok",
]


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _summary(text: str, payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
    )


def _slot_payload(value: BackendSlotValue | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "value": value.value,
        "authority_source": value.authority_source,
        "source_header_stamp": value.source_header_stamp,
        "frame_id": value.frame_id,
        "reason_codes": value.reason_codes,
    }


def _lookup_nested_value(value: Any, path: list[str]) -> tuple[bool, Any]:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def raw_read_summary_text(payload: dict[str, Any]) -> str:
    slot_summary = {
        slot_id: (slot_payload or {}).get("value")
        for slot_id, slot_payload in (payload.get("slots", {}) or {}).items()
    }
    summary = {
        "slots": slot_summary,
        "included_runtime_context": bool(payload.get("runtime_context")),
    }
    return _json_text(summary)


def raw_action_summary_text(action: str, payload: dict[str, Any]) -> str:
    return _json_text(
        {
            "action": action,
            "state": payload.get("state"),
            "response": payload.get("response", {}),
            "reason_codes": payload.get("reason_codes", []),
        }
    )


def raw_artifact_summary_text(payload: dict[str, Any]) -> str:
    return _json_text(
        {
            "artifact": payload.get("artifact"),
            "revision": payload.get("payload", {}).get("revision"),
            "path": payload.get("path"),
        }
    )


class RawSession:
    def __init__(
        self,
        *,
        backend: BackendAdapter,
        artifacts_dir: Path,
        recorder: ReplayRecorder | None = None,
    ) -> None:
        self._backend = backend
        self._artifacts_dir = artifacts_dir
        self._recorder = recorder

    def _resolve_slot(self, slot_id: str) -> BackendSlotValue | None:
        direct_value = self._backend.refresh_slot(slot_id)
        if direct_value is not None:
            return direct_value
        parts = slot_id.split(".")
        for index in range(len(parts) - 1, 0, -1):
            base_slot_id = ".".join(parts[:index])
            nested_path = parts[index:]
            base_value = self._backend.refresh_slot(base_slot_id)
            if base_value is None:
                continue
            found, nested_value = _lookup_nested_value(base_value.value, nested_path)
            if not found:
                continue
            return BackendSlotValue(
                value=nested_value,
                authority_source=base_value.authority_source,
                source_header_stamp=base_value.source_header_stamp,
                frame_id=base_value.frame_id,
                reason_codes=list(base_value.reason_codes),
            )
        return None

    def read_slots(
        self,
        slots: list[str] | None = None,
        *,
        include_runtime_context: bool = False,
    ) -> dict[str, Any]:
        requested_slots = slots or list(COMMON_RAW_SLOTS)
        payload = {
            "slots": {
                slot_id: _slot_payload(self._resolve_slot(slot_id))
                for slot_id in requested_slots
            },
            "runtime_context": (
                self._backend.get_runtime_context() if include_runtime_context else {}
            ),
        }
        self._record("raw.read", {"slots": requested_slots}, payload)
        return payload

    def wait(self, seconds: float, slots: list[str] | None = None) -> dict[str, Any]:
        duration_sec = min(max(float(seconds), 0.0), 15.0)
        time.sleep(duration_sec)
        payload = self.read_slots(slots)
        payload["slept_sec"] = duration_sec
        return payload

    def read_artifact(self, artifact: str) -> dict[str, Any]:
        path = self._artifacts_dir / f"{artifact}.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        result = {
            "artifact": artifact,
            "path": str(path),
            "payload": payload or {},
        }
        self._record("raw.read_artifact", {"artifact": artifact}, result)
        return result

    def arm(self) -> dict[str, Any]:
        return self._action("raw.arm", self._backend.arm())

    def takeoff(self, target_altitude: float) -> dict[str, Any]:
        global_fix = self._backend.get_global_fix()
        if not global_fix:
            payload = {
                "state": ActionState.FAILED.value,
                "response": {"target_altitude": target_altitude},
                "confirm_evidence": {},
                "reason_codes": ["global_fix_missing"],
            }
            self._record("raw.takeoff", {"target_altitude": target_altitude}, payload)
            return payload
        return self._action(
            "raw.takeoff",
            self._backend.takeoff(float(target_altitude), global_fix),
            request={"target_altitude": target_altitude},
        )

    def goto(
        self,
        *,
        x: float,
        y: float,
        z: float,
        frame_id: str = "map",
        yaw: float | None = None,
        stream_rate_hz: float | None = None,
    ) -> dict[str, Any]:
        target_pose: JSONDict = {
            "position": {
                "x": round(float(x), 3),
                "y": round(float(y), 3),
                "z": round(float(z), 3),
            },
            "frame_id": frame_id,
        }
        if yaw is not None:
            target_pose["yaw"] = round(float(yaw), 3)
        canonical_args: JSONDict = {"target_pose": target_pose}
        if stream_rate_hz is not None:
            canonical_args["stream_rate_hz"] = float(stream_rate_hz)
        return self._action(
            "raw.goto",
            self._backend.goto(target_pose, canonical_args),
            request=canonical_args,
        )

    def hold(self) -> dict[str, Any]:
        return self._action("raw.hold", self._backend.hold())

    def rtl(self) -> dict[str, Any]:
        return self._action("raw.rtl", self._backend.rtl())

    def land(self) -> dict[str, Any]:
        return self._action("raw.land", self._backend.land())

    def set_mode(self, mode: str) -> dict[str, Any]:
        return self._action("raw.set_mode", self._backend.set_mode(mode), request={"mode": mode})

    def _action(
        self,
        tool_name: str,
        result: BackendActionResult,
        *,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "state": result.state.value,
            "response": result.response,
            "confirm_evidence": result.confirm_evidence,
            "reason_codes": result.reason_codes,
        }
        self._record(tool_name, request or {}, payload)
        return payload

    def _record(
        self,
        tool_name: str,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        if self._recorder is None:
            return
        self._recorder.record(
            "raw_tool_call",
            payload={
                "tool_name": tool_name,
                "request": request,
                "response": response,
            },
        )


def register_raw_tools(
    server: FastMCP,
    *,
    backend: BackendAdapter,
    artifacts_dir: Path,
    recorder: ReplayRecorder | None = None,
) -> None:
    session = RawSession(backend=backend, artifacts_dir=artifacts_dir, recorder=recorder)

    @server.tool(name="raw.read")
    def raw_read(
        slots: list[str] | None = None,
        include_runtime_context: bool = False,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.read_slots(slots, include_runtime_context=include_runtime_context)
        return _summary(raw_read_summary_text(payload), payload)

    @server.tool(name="raw.wait")
    def raw_wait(
        seconds: float = 1.0,
        slots: list[str] | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.wait(seconds, slots)
        return _summary(raw_read_summary_text(payload), payload)

    @server.tool(name="raw.read_artifact")
    def raw_read_artifact(
        artifact: str,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.read_artifact(artifact)
        return _summary(raw_artifact_summary_text(payload), payload)

    @server.tool(name="raw.arm")
    def raw_arm(wait_for_previous: bool | None = None) -> ToolResult:
        del wait_for_previous
        payload = session.arm()
        return _summary(raw_action_summary_text("raw.arm", payload), payload)

    @server.tool(name="raw.takeoff")
    def raw_takeoff(
        target_altitude: float,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.takeoff(target_altitude)
        return _summary(raw_action_summary_text("raw.takeoff", payload), payload)

    @server.tool(name="raw.goto")
    def raw_goto(
        x: float,
        y: float,
        z: float,
        frame_id: str = "map",
        yaw: float | None = None,
        stream_rate_hz: float | None = None,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.goto(
            x=x,
            y=y,
            z=z,
            frame_id=frame_id,
            yaw=yaw,
            stream_rate_hz=stream_rate_hz,
        )
        return _summary(raw_action_summary_text("raw.goto", payload), payload)

    @server.tool(name="raw.hold")
    def raw_hold(wait_for_previous: bool | None = None) -> ToolResult:
        del wait_for_previous
        payload = session.hold()
        return _summary(raw_action_summary_text("raw.hold", payload), payload)

    @server.tool(name="raw.rtl")
    def raw_rtl(wait_for_previous: bool | None = None) -> ToolResult:
        del wait_for_previous
        payload = session.rtl()
        return _summary(raw_action_summary_text("raw.rtl", payload), payload)

    @server.tool(name="raw.land")
    def raw_land(wait_for_previous: bool | None = None) -> ToolResult:
        del wait_for_previous
        payload = session.land()
        return _summary(raw_action_summary_text("raw.land", payload), payload)

    @server.tool(name="raw.set_mode")
    def raw_set_mode(
        mode: str,
        wait_for_previous: bool | None = None,
    ) -> ToolResult:
        del wait_for_previous
        payload = session.set_mode(mode)
        return _summary(raw_action_summary_text("raw.set_mode", payload), payload)
