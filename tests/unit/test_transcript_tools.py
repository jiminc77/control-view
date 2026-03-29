from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ActionState, Verdict
from control_view.contracts.models import ExecutionResult, LeaseToken
from control_view.mcp_server.server import build_server
from control_view.mcp_server.transcript_tools import (
    transcript_decision_payload,
    transcript_execute_payload,
    transcript_status_payload,
)
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


class _StatusSequenceService:
    def __init__(self, tails: list[dict[str, Any]]) -> None:
        self._tails = tails
        self.calls = 0

    def ledger_tail(self, *, last_n: int = 10) -> dict[str, Any]:
        index = min(self.calls, len(self._tails) - 1)
        self.calls += 1
        return self._tails[index]


class _ExecuteSequenceService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def ledger_tail(self, *, last_n: int = 10) -> dict[str, Any]:
        self.calls.append("status")
        return {"recent_actions": [], "open_obligations": []}

    def get_control_view(self, family: str, proposed_args: dict[str, Any]) -> Any:
        self.calls.append(f"view:{family}")
        lease = LeaseToken(
            lease_id="lease-1",
            family=family,
            issued_mono_ns=0,
            expires_mono_ns=1_000_000_000,
            critical_slot_revisions={},
            arg_hash="hash",
            nonce="nonce",
            signature="sig",
        )
        return SimpleNamespace(
            verdict=Verdict.ACT,
            canonical_args={},
            lease_token=lease,
            model_dump=lambda mode="json": {
                "family": family,
                "verdict": Verdict.ACT.value,
                "canonical_args": {},
                "blockers": [],
            },
        )

    def execute_guarded(
        self,
        family: str,
        canonical_args: dict[str, Any],
        lease_token: LeaseToken,
    ) -> ExecutionResult:
        self.calls.append(f"execute:{family}")
        return ExecutionResult(
            status=ActionState.ACKED_STRONG,
            action_id="action-1",
            opened_obligation_ids=[],
        )


def _goto_args() -> dict[str, object]:
    return {
        "target_pose": {
            "position": {"x": 2.0, "y": 0.0, "z": 3.0},
            "frame_id": "map",
        }
    }


def _build_service() -> ControlViewService:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 2.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot("estimator.health", {"score": 0.99})
    backend.set_slot("geofence.status", {"target_inside": True, "artifact_revision": 1})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("battery.margin", {"margin_fraction": 0.6, "reserve_fraction": 0.2})
    backend.set_slot("vehicle.mode", "POSCTL")
    backend.set_slot("nav.progress", {"phase": "IN_PROGRESS", "distance_m": 5.0, "speed_mps": 0.1})
    return ControlViewService(ROOT, backend=backend)


def test_transcript_decision_payload_is_thin() -> None:
    payload = transcript_decision_payload(
        _build_service(),
        family="GOTO",
        proposed_args=_goto_args(),
        baseline_policy="B1",
    )

    assert payload["verdict"] == Verdict.ACT.value
    assert payload["can_execute"] is True
    assert "critical_slots" not in payload
    assert "support_slots" not in payload


def test_transcript_execute_payload_uses_runtime_and_returns_status() -> None:
    payload = transcript_execute_payload(
        _build_service(),
        family="GOTO",
        proposed_args=_goto_args(),
        baseline_policy="B1",
    )

    assert payload["family"] == "GOTO"
    assert payload["status"] in {"ACKED_WEAK", "ACKED_STRONG"}
    assert payload["next_check"] == "family.status"


def test_transcript_execute_payload_settles_pending_state_before_view() -> None:
    service = _ExecuteSequenceService()

    payload = transcript_execute_payload(
        service,
        family="ARM",
        proposed_args=None,
        baseline_policy="B3",
    )

    assert payload["status"] == "ACKED_STRONG"
    assert service.calls[0] == "status"
    assert "view:ARM" in service.calls
    assert service.calls[-1] == "execute:ARM"


def test_transcript_status_payload_summarizes_pending_families() -> None:
    service = _build_service()
    transcript_execute_payload(
        service,
        family="GOTO",
        proposed_args=_goto_args(),
        baseline_policy="B1",
    )

    payload = transcript_status_payload(service, last_n=10)

    assert payload["open_obligation_count"] >= 1
    assert "GOTO" in payload["pending_families"]


def test_transcript_status_payload_waits_for_pending_transition(monkeypatch) -> None:
    service = _StatusSequenceService(
        [
            {
                "recent_actions": [
                    {
                        "action_id": "takeoff-1",
                        "family": "TAKEOFF",
                        "state": "ACKED_STRONG",
                        "failure_reason_codes": [],
                    }
                ],
                "open_obligations": [{"family": "TAKEOFF"}],
            },
            {
                "recent_actions": [
                    {
                        "action_id": "takeoff-1",
                        "family": "TAKEOFF",
                        "state": "CONFIRMED",
                        "failure_reason_codes": [],
                    }
                ],
                "open_obligations": [],
            },
        ]
    )
    monkeypatch.setattr("control_view.mcp_server.transcript_tools.time.sleep", lambda _: None)

    payload = transcript_status_payload(service, last_n=10)

    assert payload["open_obligation_count"] == 0
    assert payload["recent_actions"][0]["state"] == "CONFIRMED"
    assert service.calls >= 2


def test_transcript_decision_payload_fills_goto_altitude_from_pose() -> None:
    payload = transcript_decision_payload(
        _build_service(),
        family="GOTO",
        proposed_args={
            "target_pose": {
                "position": {"x": 2.0, "y": 0.0},
                "frame_id": "map",
            }
        },
        baseline_policy="B3",
    )

    assert payload["verdict"] == Verdict.ACT.value
    assert payload["canonical_args"]["target_pose"]["position"]["z"] == 2.0


def test_full_surface_exposes_transcript_tools() -> None:
    server = build_server(_build_service(), tool_surface="full", baseline_policy="B3")

    async def _tool_names() -> list[str]:
        return sorted(tool.name for tool in await server.list_tools())

    tool_names = asyncio.run(_tool_names())

    assert "action.execute_guarded" in tool_names
    assert "control_view.get" in tool_names
    assert "family.decide" in tool_names
    assert "family.execute" in tool_names
    assert "family.status" in tool_names


def test_full_surface_transcript_tools_ignore_wait_for_previous() -> None:
    server = build_server(_build_service(), tool_surface="full", baseline_policy="B3")

    async def _invoke() -> list[dict[str, Any]]:
        decide = await server.call_tool(
            "family.decide",
            {
                "family": "GOTO",
                "proposed_args": _goto_args(),
                "wait_for_previous": True,
            },
        )
        execute = await server.call_tool(
            "family.execute",
            {
                "family": "ARM",
                "wait_for_previous": True,
            },
        )
        status = await server.call_tool(
            "family.status",
            {
                "wait_for_previous": True,
            },
        )
        return [
            decide.structured_content,
            execute.structured_content,
            status.structured_content,
        ]

    decide_payload, execute_payload, status_payload = asyncio.run(_invoke())

    assert decide_payload["family"] == "GOTO"
    assert execute_payload["family"] == "ARM"
    assert status_payload["open_obligation_count"] >= 0
