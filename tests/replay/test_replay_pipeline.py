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


def test_replay_recorder_streams_jsonl_incrementally(tmp_path: Path) -> None:
    output = tmp_path / "observer.jsonl"
    recorder = ReplayRecorder(stream_path=output)

    recorder.record("observer_event", payload={"event_kind": "arrival"})
    recorder.record("observer_summary", payload={"mission_success": True})

    lines = output.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert "observer_event" in lines[0]
    assert "observer_summary" in lines[1]


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
    result_record = next(
        record for record in recorder.records if record.record_type == "control_view_result"
    )
    assert "decision_context" in result_record.payload
    assert result_record.payload["legacy_trace"] is False


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


def test_slot_ablation_recomputes_modern_trace_decision() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.mode", "MANUAL")
    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()

    view = service.get_control_view("ARM")
    recorder.record_view_result("ARM", view.model_dump(mode="json"))

    outputs = ReplayRunner(service).replay(recorder.records, slot_ablation=["vehicle.connected"])

    assert outputs[0]["legacy_trace"] is False
    assert outputs[0]["verdict"] == Verdict.REFRESH.value
    assert outputs[0]["ablated_slots"] == ["vehicle.connected"]


def test_b2_ttl_sensitivity_depends_on_stale_timestamp() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 3.0},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "frame_id": "map",
        },
        frame_id="map",
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot("tf.local_body", {"ready": True})
    backend.set_slot("vehicle.mode", "OFFBOARD")
    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()

    view = service.get_control_view(
        "GOTO",
        {
            "target_pose": {
                "position": {"x": 1.0, "y": 0.0, "z": 3.0},
                "frame_id": "map",
            }
        },
    )
    recorder.record_view_result("GOTO", view.model_dump(mode="json"))

    strict_output = ReplayRunner(service).replay(
        recorder.records,
        fault_injector=FaultInjector(),
        fault_name="stale_pose",
        fault_params={"stale_ms": 3000},
        policy_swap="B2",
        b2_ttl_sec=2.0,
    )
    relaxed_output = ReplayRunner(service).replay(
        recorder.records,
        fault_injector=FaultInjector(),
        fault_name="stale_pose",
        fault_params={"stale_ms": 3000},
        policy_swap="B2",
        b2_ttl_sec=10.0,
    )

    assert strict_output[0]["verdict"] == Verdict.REFRESH.value
    assert relaxed_output[0]["verdict"] == Verdict.ACT.value


def test_b3_replay_preserves_explicitly_invalidated_geofence_slot() -> None:
    backend = FakeBackend()
    backend.set_slot("vehicle.connected", True)
    backend.set_slot("vehicle.armed", True)
    backend.set_slot(
        "pose.local",
        {
            "position": {"x": 0.0, "y": 0.0, "z": 3.0},
            "frame_id": "map",
            "child_frame_id": "base_link",
        },
        frame_id="map",
    )
    backend.set_slot("estimator.health", {"score": 0.95})
    backend.set_slot("failsafe.state", {"active": False})
    backend.set_slot("tf.local_body", {"ready": True})
    backend.set_slot(
        "offboard.stream.ok",
        {"value": True, "publish_rate_hz": 20.0, "last_publish_age_ms": 10.0},
    )
    backend.set_slot("vehicle.mode", "OFFBOARD")
    service = ControlViewService(ROOT, backend=backend)
    recorder = ReplayRecorder()

    view = service.get_control_view(
        "GOTO",
        {
            "target_pose": {
                "position": {"x": 1.0, "y": 0.0, "z": 3.0},
                "frame_id": "map",
            }
        },
    )
    recorder.record_view_result("GOTO", view.model_dump(mode="json"))

    outputs = ReplayRunner(service).replay(
        recorder.records,
        fault_injector=FaultInjector(),
        fault_name="geofence_revision_update",
        policy_swap="B3",
    )

    assert outputs[0]["verdict"] == Verdict.SAFE_HOLD.value
    assert outputs[0]["critical_slots"]["geofence.status"]["valid_state"] == "INVALIDATED"
