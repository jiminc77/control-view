from __future__ import annotations

import time
from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import ActionState, Verdict
from control_view.replay.fault_injector import FaultInjector
from control_view.replay.metrics import compute_metrics
from control_view.replay.oracle import RuleBasedOracle
from control_view.replay.recorder import ReplayRecorder
from control_view.replay.replayer import ReplayRunner
from control_view.service import ControlViewService

ROOT = Path(__file__).resolve().parents[2]


def test_replay_runner_and_metrics() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})

    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()
    recorder.record_view_request("ARM", {})
    recorder.record_view_result(
        "ARM",
        {
            "verdict": Verdict.ACT.value,
            "canonical_args": {},
            "critical_slots": {
                "vehicle.connected": {"valid_state": "VALID", "value_json": {"value": True}},
            },
            "support_slots": {},
            "open_obligations": [],
        },
    )

    outputs = ReplayRunner(service).replay(
        recorder.records,
        mode="single_step",
        single_step_count=1,
        fault_injector=FaultInjector(),
        fault_name="tool_registry_revision_bump",
        oracle=RuleBasedOracle(),
        slot_ablation=["vehicle.connected"],
        policy_swap="B2",
    )

    assert outputs[0]["verdict"] == Verdict.ACT.value
    assert outputs[0]["policy_swap"] == "B2"
    assert outputs[0]["ablated_slots"] == ["vehicle.connected"]
    assert outputs[0]["fault_injection"]["fault_name"] == "tool_registry_revision_bump"
    metrics = compute_metrics(outputs)
    assert metrics["unsafe_act_rate"] >= 0.0


def test_replay_runner_prefers_recorded_view_result_over_request() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()

    view = service.get_control_view("ARM")
    recorder.record_view_request("ARM", {})
    recorder.record_view_result("ARM", view.model_dump(mode="json"))

    outputs = ReplayRunner(service).replay(recorder.records)

    assert [output["record_type"] for output in outputs] == ["control_view_result"]


def test_replay_runner_refreshes_expired_request_only_lease() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()

    view = service.get_control_view("ARM")
    recorder.record_view_result("ARM", view.model_dump(mode="json"))
    recorder.record_execute_request(
        "ARM",
        view.canonical_args,
        view.lease_token.model_dump(mode="json"),
    )
    time.sleep(0.4)

    outputs = ReplayRunner(service).replay(recorder.records)

    assert outputs[1]["record_type"] == "execute_guarded_request"
    assert outputs[1]["status"] == ActionState.ACKED_STRONG.value


def test_service_recorder_captures_requests_results_and_artifacts() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    backend.set_slot("failsafe.state", {"active": False})
    recorder = ReplayRecorder()

    service = ControlViewService(ROOT, backend=backend, recorder=recorder)
    view = service.get_control_view("ARM")
    service.execute_guarded("ARM", view.canonical_args, view.lease_token)
    backend.set_slot("vehicle.armed", True)
    service.get_control_view("HOLD")

    record_types = [record.record_type for record in recorder.records]

    assert "artifact_revision" in record_types
    assert "control_view_request" in record_types
    assert "control_view_result" in record_types
    assert "execute_guarded_request" in record_types
    assert "execution_result" in record_types
    assert "action_transition" in record_types
    assert "obligation_transition" in record_types
    assert "normalized_event" in record_types
    assert "ledger_snapshot" in record_types


def test_policy_swap_removes_stale_commit_abort() -> None:
    recorder = ReplayRecorder()
    recorder.record_execution_result(
        "GOTO",
        {
            "status": ActionState.ABORTED.value,
            "action_id": "a1",
            "opened_obligation_ids": [],
            "abort_reason": "critical_slot_revision_changed:failsafe.state",
        },
    )

    service = ControlViewService(ROOT, backend=FakeBackend())
    b3_output = ReplayRunner(service).replay(recorder.records, policy_swap="B3")
    b2_output = ReplayRunner(service).replay(recorder.records, policy_swap="B2")

    assert b3_output[0]["status"] == ActionState.ABORTED.value
    assert b2_output[0]["status"] == ActionState.ACKED_WEAK.value
    assert b2_output[0]["abort_reason"] is None


def test_policy_swap_distinguishes_full_from_ttl_only_and_no_governor() -> None:
    recorder = ReplayRecorder()
    recorder.record_view_result(
        "GOTO",
        {
            "verdict": Verdict.SAFE_HOLD.value,
            "canonical_args": {},
            "critical_slots": {},
            "support_slots": {},
            "blockers": [
                {
                    "slot_id": "pose.local",
                    "kind": "stale_slot",
                    "severity": "high",
                    "message": "pose.local is stale",
                    "refreshable": True,
                    "refresh_hint": "refresh pose.local",
                    "evidence_summary": {},
                }
            ],
            "open_obligations": [],
        },
    )

    service = ControlViewService(ROOT, backend=FakeBackend())
    b1_output = ReplayRunner(service).replay(recorder.records, policy_swap="B1")
    b2_output = ReplayRunner(service).replay(recorder.records, policy_swap="B2")
    b3_output = ReplayRunner(service).replay(recorder.records, policy_swap="B3")

    assert b1_output[0]["verdict"] == Verdict.ACT.value
    assert b2_output[0]["verdict"] == Verdict.REFRESH.value
    assert b3_output[0]["verdict"] == Verdict.SAFE_HOLD.value
