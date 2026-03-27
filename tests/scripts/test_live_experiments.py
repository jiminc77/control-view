from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
