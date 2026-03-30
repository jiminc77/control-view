#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SEEDS = (11, 21, 31)
CORE_PHASES = ("clean", "regen_traces", "replay_core", "live_e2", "live_e4", "aggregate")
CORE_BUNDLE = "core"
CORE_PLUS_B2_BUNDLE = "core_plus_b2"


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    output_root = (args.output_root or (root / "artifacts" / "experiments")).resolve()
    seeds = _parse_seeds(args.seeds)
    stamp = args.stamp or _stamp()
    jobs = _bundle_jobs(root, bundle=args.bundle, seeds=seeds, output_root=output_root)
    if args.phase != "all":
        jobs = [job for job in jobs if job.phase == args.phase]

    manifest = {
        "bundle": args.bundle,
        "phase": args.phase,
        "seeds": seeds,
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
    metadata_root = _metadata_root(root, stamp)
    _write_json(metadata_root / "manifest.json", manifest)

    if args.phase in {"clean", "all"}:
        result_payload["removed_targets"] = _clean_generated_artifacts(root)
        metadata_root = _metadata_root(root, stamp)
        _write_json(metadata_root / "manifest.json", manifest)
    if args.phase in {"regen_traces", "all"}:
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
                    "returncode": completed.returncode,
                    "cmd": regen_cmd,
                }
            )
            _write_json(metadata_root / "failed_jobs.json", result_payload["failed_jobs"])
            _write_retry_script(
                metadata_root / "retry_failed_jobs.sh",
                root=root,
                failed_jobs=result_payload["failed_jobs"],
            )
            print(json.dumps(result_payload, indent=2, sort_keys=True))
            return completed.returncode

    for job in jobs:
        completed = subprocess.run(job.cmd, cwd=root, check=False)
        if completed.returncode != 0:
            result_payload["failed_jobs"].append(
                {
                    "phase": job.phase,
                    "name": job.name,
                    "returncode": completed.returncode,
                    "cmd": job.cmd,
                }
            )

    if args.phase in {"aggregate", "all"}:
        result_payload["aggregate_outputs"] = _aggregate_outputs(root, stamp)

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
