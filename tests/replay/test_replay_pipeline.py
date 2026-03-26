from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import Verdict
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
        policy_swap="B3",
    )

    assert outputs[0]["verdict"] == Verdict.ACT.value
    assert outputs[0]["policy_swap"] == "B3"
    assert outputs[0]["ablated_slots"] == ["vehicle.connected"]
    assert outputs[0]["fault_injection"]["fault_name"] == "tool_registry_revision_bump"
    metrics = compute_metrics(outputs)
    assert metrics["unsafe_act_rate"] >= 0.0
