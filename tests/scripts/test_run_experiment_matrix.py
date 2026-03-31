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


def test_live_reset_cmd_targets_reset_hook_with_job_context() -> None:
    cmd = MODULE._live_reset_cmd(
        root=ROOT,
        hook=ROOT / "scripts" / "reset_live_stack.sh",
        stamp="20260330_test",
        job_name="E2_t1_high_B0_seed11",
        attempt=2,
    )

    assert cmd[:2] == ["bash", str(ROOT / "scripts" / "reset_live_stack.sh")]
    assert "--stamp" in cmd
    assert "20260330_test" in cmd
    assert "--job-name" in cmd
    assert "E2_t1_high_B0_seed11" in cmd
    assert cmd[-1] == "2"


def test_dry_run_reports_default_live_reset_hook() -> None:
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
    assert payload["live_reset_hook"] == str(ROOT / "scripts" / "reset_live_stack.sh")


def test_job_label_includes_progress_and_phase() -> None:
    job = MODULE.Job(phase="live_e2", name="E2_t1_high_B0_seed11", cmd=["echo", "ok"])

    label = MODULE._job_label(job=job, job_index=3, job_total=78)

    assert label == "[3/78] live_e2 E2_t1_high_B0_seed11"


def test_status_writes_brief_progress_to_stderr(capsys) -> None:
    MODULE._status("phase aggregate started")

    captured = capsys.readouterr()

    assert "phase aggregate started" in captured.err
    assert captured.out == ""
