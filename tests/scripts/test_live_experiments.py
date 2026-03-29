from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_live_fault_injector_module():
    spec = importlib.util.spec_from_file_location(
        "control_view_live_fault_injector",
        ROOT / "scripts" / "live_fault_injector.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_fault_injector_dry_run() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "live_fault_injector.py"),
            "--scenario-config",
            str(ROOT / "configs" / "experiments" / "t3_recovery.yaml"),
            "--artifacts-dir",
            str(ROOT / "artifacts"),
            "--output-jsonl",
            str(ROOT / "artifacts" / "replay" / "fault_dry_run.jsonl"),
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["scenario"] == "t3_recovery"
    assert payload["step_count"] >= 1


def test_live_experiment_runner_dry_run(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_live_experiments.py"),
            "--experiment",
            "E2",
            "--scenario",
            "t1_medium",
            "--baseline",
            "B3",
            "--seed",
            "7",
            "--output-root",
            str(tmp_path),
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["experiment"] == "E2"
    assert payload["scenario"] == "t1_medium"
    assert payload["baseline"] == "B3"
    assert payload["mission"] == "goto_hold_land"


def test_t3_recovery_uses_observer_event_triggers() -> None:
    payload = yaml.safe_load((ROOT / "configs" / "experiments" / "t3_recovery.yaml").read_text())

    step_contract = [
        (
            step["action"],
            step.get("unit"),
            step.get("failure_type"),
            step.get("after_observer_event"),
            int(step.get("occurrence", 1)),
            float(step.get("delay_sec", 0.0)),
            step.get("fallback", {}).get("custom_mode"),
        )
        for step in payload["steps"]
    ]

    assert step_contract == [
        ("inject_failure", "mavlink_signal", "off", "excursion_reached", 1, 0.0, "AUTO.LOITER"),
        ("inject_failure", "mavlink_signal", "off", "fault_recovered", 1, 8.0, "AUTO.LOITER"),
    ]


def test_live_fault_injector_normalizes_yaml_boolean_off_token() -> None:
    module = _load_live_fault_injector_module()

    assert module._normalize_fault_token(False) == "off"
    assert module._normalize_fault_token("off") == "off"
