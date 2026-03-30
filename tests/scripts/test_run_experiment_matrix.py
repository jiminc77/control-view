from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_experiment_matrix",
    ROOT / "scripts" / "run_experiment_matrix.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


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


def test_retryable_failure_detection_matches_gemini_style_errors() -> None:
    assert MODULE._is_retryable_failure("Gemini internal error: 503 backend unavailable")
    assert MODULE._is_retryable_failure("resource exhausted: rate limit")
    assert not MODULE._is_retryable_failure("Timed out waiting for /mavros/set_mode")


def test_remaining_jobs_skips_completed_entries() -> None:
    jobs = [
        MODULE.Job(phase="live_e2", name="job_a", cmd=["echo", "a"]),
        MODULE.Job(phase="live_e2", name="job_b", cmd=["echo", "b"]),
    ]
    job_status = {
        "job_a": {"status": "completed"},
        "job_b": {"status": "failed_retryable"},
    }

    remaining = MODULE._remaining_jobs(jobs, job_status)

    assert [job.name for job in remaining] == ["job_b"]
