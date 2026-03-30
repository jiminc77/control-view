#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SEEDS = (11, 21, 31)
DEFAULT_BACKOFF_SEC = (180, 300, 600)
CORE_PHASES = ("clean", "regen_traces", "replay_core", "live_e2", "live_e4", "aggregate")
CORE_BUNDLE = "core"
CORE_PLUS_B2_BUNDLE = "core_plus_b2"
STATE_CLEAN = "__clean__"
STATE_REGEN = "__regen_traces__"
STATE_AGGREGATE = "__aggregate__"
RETRYABLE_PATTERNS = (
    "internal error",
    "internal server error",
    "temporarily unavailable",
    "resource exhausted",
    "rate limit",
    "429",
    "503",
    "deadline exceeded",
    "connection reset",
    "socket hang up",
    "stream disconnected",
)


@dataclass(frozen=True)
class Job:
    phase: str
    name: str
    cmd: list[str]


def _parse_seeds(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_SEEDS)
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_backoff(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_BACKOFF_SEC)
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _replay_job(
    *,
    root: Path,
    name: str,
    replay_jsonl: str,
    policy_swap: str,
    scenario: str,
    seed: int,
    output_rel: str,
    counterexamples_rel: str,
    fault: str | None = None,
    slot_ablation: str | None = None,
    b2_ttl_sec: float | None = None,
) -> Job:
    cmd = [
        "uv",
        "run",
        "python",
        str(root / "scripts" / "run_replay_experiments.py"),
        "--root",
        str(root),
        "--replay-jsonl",
        str(root / replay_jsonl),
        "--policy-swap",
        policy_swap,
        "--scenario",
        scenario,
        "--seed",
        str(seed),
        "--output",
        str(root / output_rel),
        "--counterexamples-jsonl",
        str(root / counterexamples_rel),
    ]
    if fault:
        cmd.extend(["--fault", fault])
    if slot_ablation:
        cmd.extend(["--slot-ablation", slot_ablation])
    if b2_ttl_sec is not None:
        cmd.extend(["--b2-ttl-sec", str(b2_ttl_sec)])
    return Job(phase="replay_core", name=name, cmd=cmd)


def _live_job(
    *,
    root: Path,
    experiment: str,
    scenario: str,
    baseline: str,
    seed: int,
    output_root: Path,
) -> Job:
    name = f"{experiment}_{scenario}_{baseline}_seed{seed}"
    return Job(
        phase="live_e2" if experiment == "E2" else "live_e4",
        name=name,
        cmd=[
            "uv",
            "run",
            "python",
            str(root / "scripts" / "run_live_experiments.py"),
            "--root",
            str(root),
            "--experiment",
            experiment,
            "--scenario",
            scenario,
            "--baseline",
            baseline,
            "--seed",
            str(seed),
            "--output-root",
            str(output_root),
        ],
    )


def _bundle_jobs(root: Path, *, bundle: str, seeds: list[int], output_root: Path) -> list[Job]:
    jobs: list[Job] = []
    for seed in seeds:
        jobs.extend(
            [
                _replay_job(
                    root=root,
                    name=f"e1_goto_hold_land_b3_nominal_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                    policy_swap="B3",
                    scenario="t1_low",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e1/goto_hold_land_b3_nominal_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e1_goto_hold_land_b3_nominal_seed{seed}.jsonl"
                    ),
                ),
            ]
        )
        for slot in (
            "pose.local",
            "vehicle.connected",
            "vehicle.armed",
            "estimator.health",
            "offboard.stream.ok",
            "geofence.status",
        ):
            slot_slug = slot.replace(".", "_")
            jobs.append(
                _replay_job(
                    root=root,
                    name=f"e1_goto_hold_land_{slot_slug}_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                    policy_swap="B3",
                    scenario="t1_low",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e1/goto_hold_land_{slot_slug}_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e1_goto_hold_land_{slot_slug}_seed{seed}.jsonl"
                    ),
                    slot_ablation=slot,
                )
            )
        jobs.extend(
            [
                _replay_job(
                    root=root,
                    name=f"e1_goto_rtl_b3_nominal_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_rtl.jsonl",
                    policy_swap="B3",
                    scenario="t1_low",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e1/goto_rtl_b3_nominal_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e1_goto_rtl_b3_nominal_seed{seed}.jsonl"
                    ),
                ),
                _replay_job(
                    root=root,
                    name=f"e1_goto_rtl_home_ready_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_rtl.jsonl",
                    policy_swap="B3",
                    scenario="t1_low",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e1/goto_rtl_home_ready_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e1_goto_rtl_home_ready_seed{seed}.jsonl"
                    ),
                    slot_ablation="home.ready",
                ),
                _replay_job(
                    root=root,
                    name=f"e3_ack_without_confirm_b1_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                    policy_swap="B1",
                    scenario="t3_recovery",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e3/ack_without_confirm_b1_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e3_ack_without_confirm_b1_seed{seed}.jsonl"
                    ),
                    fault="ack_without_confirm",
                ),
                _replay_job(
                    root=root,
                    name=f"e3_ack_without_confirm_b3_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                    policy_swap="B3",
                    scenario="t3_recovery",
                    seed=seed,
                    output_rel=f"artifacts/metrics/e3/ack_without_confirm_b3_seed{seed}.json",
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e3_ack_without_confirm_b3_seed{seed}.jsonl"
                    ),
                    fault="ack_without_confirm",
                ),
            ]
        )
        for ttl_sec in (2.0, 5.0, 10.0):
            ttl_slug = str(int(ttl_sec))
            jobs.append(
                _replay_job(
                    root=root,
                    name=f"e3_offboard_stream_loss_b2_ttl_{ttl_slug}_seed{seed}",
                    replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                    policy_swap="B2",
                    scenario="t3_recovery",
                    seed=seed,
                    output_rel=(
                        f"artifacts/metrics/e3/offboard_stream_loss_b2_ttl_{ttl_slug}_seed{seed}.json"
                    ),
                    counterexamples_rel=(
                        f"artifacts/replay/counterexamples/"
                        f"e3_offboard_stream_loss_b2_ttl_{ttl_slug}_seed{seed}.jsonl"
                    ),
                    fault="offboard_stream_loss",
                    b2_ttl_sec=ttl_sec,
                )
            )
        jobs.append(
            _replay_job(
                root=root,
                name=f"e3_geofence_revision_b3_seed{seed}",
                replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                policy_swap="B3",
                scenario="t2_spec_drift",
                seed=seed,
                output_rel=f"artifacts/metrics/e3/geofence_revision_b3_seed{seed}.json",
                counterexamples_rel=(
                    f"artifacts/replay/counterexamples/e3_geofence_revision_b3_seed{seed}.jsonl"
                ),
                fault="geofence_revision_update",
            )
        )

        for scenario in ("t1_low", "t1_medium", "t1_high"):
            for baseline in ("B0", "B1", "B3"):
                jobs.append(
                    _live_job(
                        root=root,
                        experiment="E2",
                        scenario=scenario,
                        baseline=baseline,
                        seed=seed,
                        output_root=output_root,
                    )
                )

        for scenario in ("t2_spec_drift", "t3_recovery"):
            jobs.append(
                _live_job(
                    root=root,
                    experiment="E4",
                    scenario=scenario,
                    baseline="B3",
                    seed=seed,
                    output_root=output_root,
                )
            )

        if bundle == CORE_PLUS_B2_BUNDLE:
            for scenario in ("t1_medium", "t1_high"):
                jobs.append(
                    _replay_job(
                        root=root,
                        name=f"e2_{scenario}_b2_seed{seed}",
                        replay_jsonl="artifacts/replay/goto_hold_land.jsonl",
                        policy_swap="B2",
                        scenario=scenario,
                        seed=seed,
                        output_rel=f"artifacts/metrics/e2/{scenario}_b2_seed{seed}.json",
                        counterexamples_rel=(
                            f"artifacts/replay/counterexamples/e2_{scenario}_b2_seed{seed}.jsonl"
                        ),
                    )
                )
    return jobs


def _clean_generated_artifacts(root: Path) -> list[str]:
    removed: list[str] = []
    targets = [
        root / "artifacts" / "experiments",
        root / "artifacts" / "metrics",
        root / "artifacts" / "replay",
        root / "artifacts" / "logs",
        root / "artifacts" / "aggregate",
    ]
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
            removed.append(str(target))
    return removed


def _metadata_root(root: Path, stamp: str) -> Path:
    target = root / "artifacts" / "aggregate" / stamp
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_retry_script(path: Path, *, root: Path, failed_jobs: list[dict[str, Any]]) -> None:
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", f"cd {shlex.quote(str(root))}"]
    for job in failed_jobs:
        cmd = job.get("cmd") or []
        if not isinstance(cmd, list) or not cmd:
            continue
        lines.append(" ".join(shlex.quote(str(part)) for part in cmd))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def _write_resume_script(
    path: Path,
    *,
    root: Path,
    bundle: str,
    phase: str,
    seeds: list[int],
    stamp: str,
    output_root: Path,
) -> None:
    cmd = [
        "uv",
        "run",
        "python",
        "scripts/run_experiment_matrix.py",
        "--root",
        str(root),
        "--bundle",
        bundle,
        "--phase",
        phase,
        "--seeds",
        ",".join(str(seed) for seed in seeds),
        "--output-root",
        str(output_root),
        "--resume-stamp",
        stamp,
    ]
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(root))}",
        " ".join(shlex.quote(part) for part in cmd),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def _job_status_path(metadata_root: Path) -> Path:
    return metadata_root / "job_status.json"


def _load_job_status(metadata_root: Path) -> dict[str, dict[str, Any]]:
    target = _job_status_path(metadata_root)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, dict)
    }


def _save_job_status(metadata_root: Path, job_status: dict[str, dict[str, Any]]) -> None:
    _write_json(_job_status_path(metadata_root), job_status)


def _job_log_dir(metadata_root: Path) -> Path:
    target = metadata_root / "job_logs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _job_log_path(metadata_root: Path, *, job_name: str, attempt: int) -> Path:
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in job_name)
    return _job_log_dir(metadata_root) / f"{safe_name}.attempt{attempt}.log"


def _is_retryable_failure(output: str) -> bool:
    lowered = output.lower()
    return any(pattern in lowered for pattern in RETRYABLE_PATTERNS)


def _completed(job_status: dict[str, dict[str, Any]], name: str) -> bool:
    return job_status.get(name, {}).get("status") == "completed"


def _remaining_jobs(jobs: list[Job], job_status: dict[str, dict[str, Any]]) -> list[Job]:
    return [job for job in jobs if not _completed(job_status, job.name)]


def _record_builtin_status(
    *,
    job_status: dict[str, dict[str, Any]],
    metadata_root: Path,
    name: str,
    phase: str,
    status: str,
    returncode: int = 0,
    cmd: list[str] | None = None,
) -> None:
    job_status[name] = {
        "phase": phase,
        "status": status,
        "returncode": returncode,
        "cmd": cmd or [],
        "attempt_count": 1,
    }
    _save_job_status(metadata_root, job_status)


def _run_job_with_retries(
    *,
    root: Path,
    metadata_root: Path,
    job_status: dict[str, dict[str, Any]],
    job: Job,
    max_attempts: int,
    backoff_sec: list[int],
    job_timeout_sec: int,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        job_status[job.name] = {
            "phase": job.phase,
            "name": job.name,
            "status": "running",
            "returncode": None,
            "cmd": job.cmd,
            "attempt_count": attempt,
            "attempts": attempts,
        }
        _save_job_status(metadata_root, job_status)

        log_path = _job_log_path(metadata_root, job_name=job.name, attempt=attempt)
        process = subprocess.Popen(
            job.cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=job_timeout_sec)
            combined_output = (stdout or "") + (stderr or "")
            log_path.write_text(combined_output, encoding="utf-8")
            returncode = int(process.returncode or 0)
            retryable = returncode != 0 and _is_retryable_failure(combined_output)
            attempt_payload = {
                "attempt": attempt,
                "returncode": returncode,
                "retryable": retryable,
                "log_path": str(log_path),
            }
            if returncode == 0:
                attempts.append(attempt_payload)
                return {
                    "phase": job.phase,
                    "name": job.name,
                    "status": "completed",
                    "returncode": 0,
                    "cmd": job.cmd,
                    "attempt_count": attempt,
                    "attempts": attempts,
                    "last_log_path": str(log_path),
            }
            if retryable and attempt < max_attempts:
                sleep_sec = (
                    backoff_sec[min(attempt - 1, len(backoff_sec) - 1)]
                    if backoff_sec
                    else 0
                )
                attempt_payload["backoff_sec"] = sleep_sec
                attempts.append(attempt_payload)
                job_status[job.name] = {
                    "phase": job.phase,
                    "name": job.name,
                    "status": "retry_scheduled",
                    "returncode": returncode,
                    "cmd": job.cmd,
                    "attempt_count": attempt,
                    "attempts": attempts,
                    "last_log_path": str(log_path),
                    "next_backoff_sec": sleep_sec,
                }
                _save_job_status(metadata_root, job_status)
                if sleep_sec > 0:
                    time.sleep(sleep_sec)
                continue
            attempts.append(attempt_payload)
            return {
                "phase": job.phase,
                "name": job.name,
                "status": "failed_retryable" if retryable else "failed",
                "returncode": returncode,
                "cmd": job.cmd,
                "attempt_count": attempt,
                "attempts": attempts,
                "last_log_path": str(log_path),
            }
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            combined_output = (stdout or "") + (stderr or "")
            log_path.write_text(combined_output, encoding="utf-8")
            attempt_payload = {
                "attempt": attempt,
                "returncode": 124,
                "retryable": False,
                "timed_out": True,
                "log_path": str(log_path),
            }
            attempts.append(attempt_payload)
            return {
                "phase": job.phase,
                "name": job.name,
                "status": "failed_timeout",
                "returncode": 124,
                "cmd": job.cmd,
                "attempt_count": attempt,
                "attempts": attempts,
                "last_log_path": str(log_path),
            }
    return {
        "phase": job.phase,
        "name": job.name,
        "status": "failed_retryable",
        "returncode": 1,
        "cmd": job.cmd,
        "attempt_count": max_attempts,
        "attempts": attempts,
    }


def _aggregate_outputs(root: Path, stamp: str) -> dict[str, str]:
    aggregate_root = _metadata_root(root, stamp)

    live_rows: list[dict[str, Any]] = []
    for path in sorted((root / "artifacts" / "experiments").glob("*/E*/*/*/summary.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metrics = payload.get("metrics", {}) or {}
        gemini_summary = payload.get("gemini_summary", {}) or {}
        live_rows.append(
            {
                "stamp": path.parts[-5],
                "experiment": payload.get("experiment"),
                "scenario": payload.get("scenario"),
                "baseline": payload.get("baseline"),
                "seed": payload.get("seed"),
                "mission": payload.get("mission"),
                "mission_completion_after_fault": payload.get("mission_completion_after_fault"),
                "manual_override_needed": payload.get("manual_override_needed"),
                "time_to_recovery_sec": payload.get("time_to_recovery_sec"),
                "fault_event_count": payload.get("fault_event_count"),
                "mission_duration_ms": metrics.get("mission_duration_ms"),
                "cumulative_prompt_tokens": metrics.get("cumulative_prompt_tokens"),
                "prompt_tokens_per_successful_control_decision": metrics.get(
                    "prompt_tokens_per_successful_control_decision"
                ),
                "input_tokens": gemini_summary.get("input_tokens"),
                "output_tokens": gemini_summary.get("output_tokens"),
                "tool_calls": gemini_summary.get("tool_calls"),
                "duration_ms": gemini_summary.get("duration_ms"),
            }
        )

    replay_rows: list[dict[str, Any]] = []
    for path in sorted((root / "artifacts" / "metrics").glob("**/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "policy_swap" not in payload:
            continue
        metrics = payload.get("metrics", {}) or {}
        replay_rows.append(
            {
                "file": str(path.relative_to(root)),
                "policy_swap": payload.get("policy_swap"),
                "scenario": payload.get("scenario"),
                "seed": payload.get("seed"),
                "fault": payload.get("fault"),
                "slot_ablation": ",".join(payload.get("slot_ablation", [])),
                "b2_ttl_sec": payload.get("b2_ttl_sec"),
                "official_trace_ready": payload.get("official_trace_ready"),
                "interface_mismatch_rate": metrics.get("interface_mismatch_rate"),
                "unsafe_accept_after_ablation": metrics.get("unsafe_accept_after_ablation"),
                "unsafe_act_after_fault": metrics.get("unsafe_act_after_fault"),
                "canonical_arg_error_rate": metrics.get("canonical_arg_error_rate"),
                "blocker_explanation_loss": metrics.get("blocker_explanation_loss"),
                "stale_action_rate": metrics.get("stale_action_rate"),
                "premature_transition_rate": metrics.get("premature_transition_rate"),
                "obligation_closure_accuracy": metrics.get("obligation_closure_accuracy"),
            }
        )

    live_json = aggregate_root / "live_summary.json"
    replay_json = aggregate_root / "replay_summary.json"
    live_csv = aggregate_root / "live_summary.csv"
    replay_csv = aggregate_root / "replay_summary.csv"

    live_json.write_text(json.dumps(live_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    replay_json.write_text(
        json.dumps(replay_rows, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if live_rows:
        with live_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(live_rows[0]))
            writer.writeheader()
            writer.writerows(live_rows)
    if replay_rows:
        with replay_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(replay_rows[0]))
            writer.writeheader()
            writer.writerows(replay_rows)

    return {
        "aggregate_root": str(aggregate_root),
        "live_json": str(live_json),
        "replay_json": str(replay_json),
        "live_csv": str(live_csv),
        "replay_csv": str(replay_csv),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--bundle", choices=[CORE_BUNDLE, CORE_PLUS_B2_BUNDLE], default=CORE_BUNDLE)
    parser.add_argument("--phase", choices=[*CORE_PHASES, "all"], default="all")
    parser.add_argument("--seeds", default="11,21,31")
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stamp", default=None)
    parser.add_argument("--resume-stamp", default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-backoff-sec", default="180,300,600")
    parser.add_argument("--job-timeout-sec", type=int, default=900)
    parser.add_argument("--force-clean", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    output_root = (args.output_root or (root / "artifacts" / "experiments")).resolve()
    seeds = _parse_seeds(args.seeds)
    stamp = args.resume_stamp or args.stamp or _stamp()
    backoff_sec = _parse_backoff(args.retry_backoff_sec)
    jobs = _bundle_jobs(root, bundle=args.bundle, seeds=seeds, output_root=output_root)
    metadata_root = _metadata_root(root, stamp)
    job_status = _load_job_status(metadata_root)
    if args.phase != "all":
        jobs = [job for job in jobs if job.phase == args.phase]
    if args.resume_stamp:
        jobs = _remaining_jobs(jobs, job_status)

    manifest = {
        "bundle": args.bundle,
        "phase": args.phase,
        "seeds": seeds,
        "stamp": stamp,
        "resume_stamp": args.resume_stamp,
        "max_attempts": args.max_attempts,
        "retry_backoff_sec": backoff_sec,
        "job_timeout_sec": args.job_timeout_sec,
        "job_count": len(jobs),
        "jobs": [{"phase": job.phase, "name": job.name, "cmd": job.cmd} for job in jobs],
    }
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    result_payload: dict[str, Any] = {
        **manifest,
        "removed_targets": [],
        "failed_jobs": [],
        "aggregate_outputs": {},
    }
    _write_json(metadata_root / "manifest.json", manifest)
    _write_resume_script(
        metadata_root / "resume_matrix.sh",
        root=root,
        bundle=args.bundle,
        phase=args.phase,
        seeds=seeds,
        stamp=stamp,
        output_root=output_root,
    )

    if args.phase in {"clean", "all"} and (not args.resume_stamp or args.force_clean):
        result_payload["removed_targets"] = _clean_generated_artifacts(root)
        metadata_root = _metadata_root(root, stamp)
        _write_json(metadata_root / "manifest.json", manifest)
        _record_builtin_status(
            job_status=job_status,
            metadata_root=metadata_root,
            name=STATE_CLEAN,
            phase="clean",
            status="completed",
        )
    if (
        args.phase in {"regen_traces", "all"}
        and not (args.resume_stamp and _completed(job_status, STATE_REGEN))
    ):
        regen_cmd = [
            "bash",
            str(root / "scripts" / "run_sitl_smoke.sh"),
            "takeoff_hold_land",
            "goto_hold_land",
            "goto_rtl",
        ]
        completed = subprocess.run(regen_cmd, cwd=root, check=False)
        if completed.returncode != 0:
            result_payload["failed_jobs"].append(
                {
                    "phase": "regen_traces",
                    "name": "regen_traces",
                    "status": "failed",
                    "returncode": completed.returncode,
                    "cmd": regen_cmd,
                }
            )
            _record_builtin_status(
                job_status=job_status,
                metadata_root=metadata_root,
                name=STATE_REGEN,
                phase="regen_traces",
                status="failed",
                returncode=completed.returncode,
                cmd=regen_cmd,
            )
            _write_json(metadata_root / "failed_jobs.json", result_payload["failed_jobs"])
            _write_retry_script(
                metadata_root / "retry_failed_jobs.sh",
                root=root,
                failed_jobs=result_payload["failed_jobs"],
            )
            print(json.dumps(result_payload, indent=2, sort_keys=True))
            return completed.returncode
        _record_builtin_status(
            job_status=job_status,
            metadata_root=metadata_root,
            name=STATE_REGEN,
            phase="regen_traces",
            status="completed",
            cmd=regen_cmd,
        )

    for job in jobs:
        if _completed(job_status, job.name):
            continue
        outcome = _run_job_with_retries(
            root=root,
            metadata_root=metadata_root,
            job_status=job_status,
            job=job,
            max_attempts=max(args.max_attempts, 1),
            backoff_sec=backoff_sec,
            job_timeout_sec=max(args.job_timeout_sec, 1),
        )
        job_status[job.name] = outcome
        _save_job_status(metadata_root, job_status)
        if outcome["status"] != "completed":
            result_payload["failed_jobs"].append(
                {
                    "phase": outcome["phase"],
                    "name": outcome["name"],
                    "status": outcome["status"],
                    "returncode": outcome["returncode"],
                    "attempt_count": outcome["attempt_count"],
                    "cmd": outcome["cmd"],
                    "last_log_path": outcome.get("last_log_path"),
                }
            )

    if (
        args.phase in {"aggregate", "all"}
        and not (args.resume_stamp and _completed(job_status, STATE_AGGREGATE))
    ):
        result_payload["aggregate_outputs"] = _aggregate_outputs(root, stamp)
        _record_builtin_status(
            job_status=job_status,
            metadata_root=metadata_root,
            name=STATE_AGGREGATE,
            phase="aggregate",
            status="completed",
        )

    _write_json(metadata_root / "result.json", result_payload)
    _write_json(metadata_root / "failed_jobs.json", result_payload["failed_jobs"])
    _write_retry_script(
        metadata_root / "retry_failed_jobs.sh",
        root=root,
        failed_jobs=result_payload["failed_jobs"],
    )

    print(json.dumps(result_payload, indent=2, sort_keys=True))
    return 1 if result_payload["failed_jobs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
