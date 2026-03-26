from __future__ import annotations

from control_view.replay.fault_injector import FaultInjector
from control_view.replay.metrics import compute_metrics


def test_fault_injector_supports_expanded_fault_set() -> None:
    injector = FaultInjector()
    records = [
        {
            "record_type": "control_view_result",
            "payload": {},
            "critical_slots": {
                "pose.local": {"valid_state": "VALID", "value_json": {"position": {}}}
            },
        }
    ]

    faulted = injector.apply(records, "tool_registry_revision_bump", revision=3)

    assert faulted[0]["fault_injection"]["fault_name"] == "tool_registry_revision_bump"
    assert faulted[0]["fault_injection"]["revision"] == 3


def test_metrics_include_interface_mismatch_rate() -> None:
    metrics = compute_metrics(
        [
            {"record_type": "mission_boundary", "payload": {"mission": "m1", "phase": "start"}},
            {"record_type": "control_view_result", "payload": {"verdict": "ACT", "oracle_verdict": "ACT"}},
            {
                "record_type": "control_view_result",
                "payload": {"verdict": "REFRESH", "oracle_verdict": "ACT"},
            },
            {
                "record_type": "action_transition",
                "payload": {
                    "action_id": "a1",
                    "family": "GOTO",
                    "state": "ABORTED",
                    "ack_strength": "weak",
                    "failure_reason_codes": [],
                },
            },
            {
                "record_type": "execution_result",
                "payload": {
                    "action_id": "a1",
                    "status": "ABORTED",
                    "abort_reason": "critical_slot_revision_changed:pose.local",
                },
            },
            {
                "record_type": "mission_boundary",
                "payload": {"mission": "m1", "phase": "end", "success": False},
            },
        ]
    )

    assert metrics["interface_mismatch_rate"] == 0.5
    assert metrics["stale_commit_abort_rate"] == 1.0
    assert metrics["mission_success_rate"] == 0.0
    assert metrics["weak_ack_without_confirm_rate"] == 1.0


def test_metrics_treat_confirmed_weak_ack_as_success() -> None:
    metrics = compute_metrics(
        [
            {"record_type": "mission_boundary", "payload": {"mission": "m1", "phase": "start"}},
            {
                "record_type": "action_transition",
                "payload": {
                    "action_id": "a1",
                    "family": "HOLD",
                    "state": "ACKED_WEAK",
                    "ack_strength": "weak",
                },
            },
            {
                "record_type": "action_transition",
                "payload": {
                    "action_id": "a1",
                    "family": "HOLD",
                    "state": "CONFIRMED",
                    "ack_strength": "weak",
                },
            },
            {
                "record_type": "obligation_transition",
                "payload": {
                    "obligation_id": "o1",
                    "family": "HOLD",
                    "status": "CONFIRMED",
                },
            },
            {
                "record_type": "mission_boundary",
                "payload": {"mission": "m1", "phase": "end", "success": True},
            },
        ]
    )

    assert metrics["mission_success_rate"] == 1.0
    assert metrics["weak_ack_without_confirm_rate"] == 0.0
