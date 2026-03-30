from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_experiment_matrix_core_count() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_experiment_matrix.py"),
            "--bundle",
            "core",
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["bundle"] == "core"
    assert payload["job_count"] == 78


def test_experiment_matrix_core_plus_b2_count() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_experiment_matrix.py"),
            "--bundle",
            "core_plus_b2",
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["bundle"] == "core_plus_b2"
    assert payload["job_count"] == 84
