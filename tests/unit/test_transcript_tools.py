from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import Verdict
from control_view.mcp_server.transcript_tools import (
    transcript_decision_payload,
    transcript_execute_payload,
    transcript_status_payload,
)
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


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
