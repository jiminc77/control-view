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


def test_fault_injector_can_break_confirmed_goto_transition() -> None:
    injector = FaultInjector()
    records = [
        {
            "record_type": "action_transition",
            "family": "GOTO",
            "payload": {
                "action_id": "a1",
                "family": "GOTO",
                "state": "CONFIRMED",
                "ack_strength": "weak",
                "confirm_evidence_json": {"confirmed_obligations": ["NAV_PENDING"]},
            },
        },
        {
            "record_type": "mission_boundary",
            "payload": {"mission": "m1", "phase": "end", "success": True},
        },
    ]

    faulted = injector.apply(records, "no_progress_during_goto")

    assert faulted[0]["payload"]["state"] == "EXPIRED"
    assert "no_progress_within_sec:3.0" in faulted[0]["payload"]["failure_reason_codes"]
    assert faulted[1]["payload"]["success"] is False


def test_fault_injector_marks_nested_control_view_slots_invalid() -> None:
    injector = FaultInjector()
    records = [
        {
            "record_type": "control_view_result",
            "family": "GOTO",
            "payload": {
                "verdict": "ACT",
                "critical_slots": {
                    "offboard.stream.ok": {
                        "valid_state": "VALID",
                        "value_json": {"value": True},
                        "reason_codes": [],
                    }
                },
                "support_slots": {},
                "blockers": [],
            },
        }
    ]

    faulted = injector.apply(records, "offboard_stream_loss")

    slot = faulted[0]["payload"]["critical_slots"]["offboard.stream.ok"]
    assert slot["valid_state"] == "INVALIDATED"
    assert slot["value_json"]["value"] is False
    assert faulted[0]["payload"]["verdict"] == "REFRESH"
    assert faulted[0]["payload"]["blockers"][0]["kind"] == "invalidated_slot"


def test_metrics_include_interface_mismatch_rate() -> None:
    metrics = compute_metrics(
        [
            {"record_type": "mission_boundary", "payload": {"mission": "m1", "phase": "start"}},
            {
                "record_type": "control_view_result",
                "payload": {"verdict": "ACT", "oracle_verdict": "ACT"},
            },
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


def test_metrics_include_observer_and_budget_fields() -> None:
    metrics = compute_metrics(
        [
            {
                "record_type": "observer_event",
                "recorded_mono_ns": 100,
                "metadata": {"mission_id": "m1"},
                "payload": {"event_kind": "fault_detected"},
            },
            {
                "record_type": "control_view_result",
                "recorded_mono_ns": 120,
                "metadata": {"mission_id": "m1"},
                "payload": {
                    "verdict": "ACT",
                    "oracle_verdict": "SAFE_HOLD",
                    "oracle_labels": {
                        "stale_action": True,
                        "premature_transition": True,
                    },
                    "prompt_tokens_per_turn": 12.0,
                    "decision_latency_ms": 100.0,
                    "compressed": False,
                },
            },
            {
                "record_type": "observer_summary",
                "metadata": {"mission_id": "m1"},
                "payload": {
                    "mission_success": True,
                    "fault_count": 1,
                    "recovered_fault_count": 1,
                    "observer_elapsed_sec": 3.0,
                },
            },
        ],
        token_budget=25.0,
        time_budget_ms=4_000.0,
    )

    assert metrics["mission_success_rate"] == 1.0
    assert metrics["mission_success_under_token_budget"] == 1.0
    assert metrics["mission_success_under_time_budget"] == 1.0
    assert metrics["stale_action_rate"] == 1.0
    assert metrics["premature_transition_rate"] == 1.0
    assert metrics["recovery_success_rate"] == 1.0
    assert metrics["compression_count"] == 0
    assert metrics["turns_until_first_compression"] == 0
    assert metrics["post_fault_token_spend"] == 12.0


def test_metrics_fall_back_to_gemini_turn_records_for_compression() -> None:
    metrics = compute_metrics(
        [
            {
                "record_type": "gemini_turn",
                "recorded_mono_ns": 100,
                "metadata": {"mission_id": "m1"},
                "payload": {
                    "prompt_tokens_per_turn": 7.0,
                    "decision_latency_ms": 50.0,
                    "compressed": False,
                },
            },
            {
                "record_type": "gemini_turn",
                "recorded_mono_ns": 200,
                "metadata": {"mission_id": "m1"},
                "payload": {
                    "prompt_tokens_per_turn": 9.0,
                    "decision_latency_ms": 70.0,
                    "compressed": True,
                },
            },
        ]
    )

    assert metrics["compression_count"] == 1
    assert metrics["turns_until_first_compression"] == 2
    assert metrics["cumulative_prompt_tokens"] == 16.0


def test_oracle_exposes_labels_for_replay_metrics() -> None:
    from control_view.replay.oracle import RuleBasedOracle

    decision = RuleBasedOracle().evaluate(
        "GOTO",
        {
            "critical_slots": {
                "vehicle.connected": {"valid_state": "VALID", "value_json": {"value": True}},
                "vehicle.armed": {"valid_state": "VALID", "value_json": {"value": True}},
                "estimator.health": {"valid_state": "VALID", "value_json": {"score": 0.9}},
                "geofence.status": {
                    "valid_state": "INVALIDATED",
                    "value_json": {"target_inside": True},
                },
                "failsafe.state": {"valid_state": "VALID", "value_json": {"active": False}},
                "nav.progress": {"valid_state": "VALID", "value_json": {"phase": "ARRIVED"}},
            },
            "blockers": [{"kind": "invalidated_slot", "slot_id": "geofence.status"}],
            "open_obligations": [{"status": "OPEN"}],
            "fault_name": "no_progress_during_goto",
        },
    )

    assert decision.labels["arrival"] is True
    assert decision.labels["stale_action"] is True
    assert decision.labels["premature_transition"] is True
    assert decision.labels["no_progress"] is True
