from __future__ import annotations

from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.common.types import Verdict
from control_view.replay.metrics import compute_metrics
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

    outputs = ReplayRunner(service).replay(recorder.records)

    assert outputs[0]["verdict"] == Verdict.ACT.value
    metrics = compute_metrics(outputs)
    assert metrics["unsafe_act_rate"] >= 0.0
