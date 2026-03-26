from __future__ import annotations

from control_view.replay.fault_injector import FaultInjector
from control_view.replay.metrics import compute_metrics


def test_fault_injector_supports_expanded_fault_set() -> None:
    injector = FaultInjector()
    records = [
        {
            "record_type": "control_view_result",
            "payload": {},
            "critical_slots": {"pose.local": {"valid_state": "VALID", "value_json": {"position": {}}}},
        }
    ]

    faulted = injector.apply(records, "tool_registry_revision_bump", revision=3)

    assert faulted[0]["fault_injection"]["fault_name"] == "tool_registry_revision_bump"
    assert faulted[0]["fault_injection"]["revision"] == 3


def test_metrics_include_interface_mismatch_rate() -> None:
    metrics = compute_metrics(
        [
            {"verdict": "ACT", "oracle_verdict": "ACT"},
            {"verdict": "REFRESH", "oracle_verdict": "ACT"},
            {"status": "ABORTED", "abort_reason": "critical_slot_revision_changed:pose.local"},
        ]
    )

    assert metrics["interface_mismatch_rate"] == 0.5
    assert metrics["stale_commit_abort_rate"] == 1.0
