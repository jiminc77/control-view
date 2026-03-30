from __future__ import annotations

import json

from control_view.backend.fake_backend import FakeBackend
from control_view.mcp_server.raw_tools import (
    RawSession,
    raw_action_summary_text,
    raw_artifact_summary_text,
    raw_read_summary_text,
)
from control_view.mcp_server.tools import (
    control_view_summary_text,
    explain_blockers_summary_text,
    ledger_tail_summary_text,
)


def test_control_view_summary_text_exposes_canonical_args_and_lease_token() -> None:
    payload = {
        "family": "ARM",
        "verdict": "ACT",
        "canonical_args": {},
        "blockers": [],
        "open_obligations": [],
        "lease_token": {
            "lease_id": "lease-1",
            "family": "ARM",
            "issued_mono_ns": 1,
            "expires_mono_ns": 2,
            "critical_slot_revisions": {"vehicle.connected": 3},
            "arg_hash": "hash",
            "nonce": "nonce",
            "signature": "sig",
        },
        "lease_expires_in_ms": 250,
    }

    summary = control_view_summary_text(payload)

    assert "family=ARM" in summary
    assert "verdict=ACT" in summary
    assert "blockers=0" in summary
    assert "lease" in summary


def test_explain_blockers_summary_text_lists_messages() -> None:
    payload = {
        "blockers": [
            {"message": "takeoff requires target_altitude"},
            {"kind": "predicate_failed"},
        ],
        "refresh_hints": ["provide target_altitude"],
        "suggested_safe_action": "HOLD",
    }

    summary = explain_blockers_summary_text("TAKEOFF", payload)

    assert "family=TAKEOFF" in summary
    assert "blockers=2" in summary
    assert "first=takeoff requires target_altitude" in summary
    assert "safe=HOLD" in summary


def test_ledger_tail_summary_text_surfaces_recent_actions_and_open_obligations() -> None:
    payload = {
        "recent_actions": [
            {"family": "ARM", "state": "CONFIRMED", "failure_reason_codes": []},
            {"family": "GOTO", "state": "ABORTED", "failure_reason_codes": ["lease_expired"]},
        ],
        "open_obligations": [
            {"family": "GOTO", "kind": "NAV_PENDING", "status": "open"},
        ],
        "artifact_revisions": [
            {"artifact_name": "geofence", "revision": 2},
            {"artifact_name": "mission_spec", "revision": 1},
        ],
    }

    summary = ledger_tail_summary_text(payload)

    assert "latest=ARM:CONFIRMED" in summary
    assert "obligations=1" in summary
    assert "artifacts=2" in summary


def test_raw_read_summary_text_surfaces_slot_values() -> None:
    payload = {
        "slots": {
            "vehicle.mode": {"value": "OFFBOARD"},
            "vehicle.armed": {"value": True},
        },
        "runtime_context": {},
    }

    summary = json.loads(raw_read_summary_text(payload))

    assert summary["slots"] == {
        "vehicle.mode": "OFFBOARD",
        "vehicle.armed": True,
    }
    assert summary["included_runtime_context"] is False


def test_raw_action_summary_text_surfaces_state_and_response() -> None:
    payload = {
        "state": "ACKED_WEAK",
        "response": {"mode": "OFFBOARD", "mode_sent": True},
        "reason_codes": [],
    }

    summary = json.loads(raw_action_summary_text("raw.set_mode", payload))

    assert summary["action"] == "raw.set_mode"
    assert summary["state"] == "ACKED_WEAK"
    assert summary["response"]["mode"] == "OFFBOARD"


def test_raw_session_read_slots_supports_nested_slot_paths(tmp_path) -> None:
    backend = FakeBackend()
    backend.set_slot(
        "estimator.health",
        {"score": 0.9, "veto_flags": ["pos_vert_agl"]},
        authority_source="fake",
    )
    session = RawSession(backend=backend, artifacts_dir=tmp_path)

    payload = session.read_slots(["estimator.health.veto_flags"])

    assert payload["slots"]["estimator.health.veto_flags"]["value"] == ["pos_vert_agl"]


def test_raw_artifact_summary_text_surfaces_revision_and_path() -> None:
    payload = {
        "artifact": "mission_spec",
        "path": "/tmp/mission_spec.yaml",
        "payload": {"revision": 2},
    }

    summary = json.loads(raw_artifact_summary_text(payload))

    assert summary["artifact"] == "mission_spec"
    assert summary["revision"] == 2
    assert summary["path"] == "/tmp/mission_spec.yaml"
