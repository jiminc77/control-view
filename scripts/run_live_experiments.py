#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from control_view.replay.recorder import ReplayRecorder

SCENARIOS = {
    "t1_low": "E2",
    "t1_medium": "E2",
    "t1_high": "E2",
    "t2_spec_drift": "E4",
    "t3_recovery": "E4",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text())
    return payload or {}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _default_prompt_file(root: Path, baseline: str) -> Path:
    mapping = {
        "B0": root / "docs" / "gemini_prompt_b0.md",
        "B1": root / "docs" / "gemini_prompt_b1.md",
        "B3": root / "docs" / "gemini_prompt_b3.md",
    }
    return mapping[baseline]


def _common_prompt_file(root: Path) -> Path:
    return root / "docs" / "gemini_prompt_common.md"


def _copy_artifacts(root: Path, target: Path) -> None:
    source_root = root / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    for artifact_name in ("geofence.yaml", "mission_spec.yaml"):
        source = source_root / artifact_name
        destination = target / artifact_name
        if source.exists():
            shutil.copy2(source, destination)
            continue
        default_payload = {"revision": 0}
        destination.write_text(
            yaml.safe_dump(default_payload, sort_keys=False),
            encoding="utf-8",
        )


def _fault_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _observer_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [record.model_dump(mode="json") for record in ReplayRecorder.load_jsonl(path)]


def _summary_payload(
    *,
    scenario: dict[str, Any],
    experiment: str,
    baseline: str,
    seed: int,
    paths: dict[str, Path],
) -> dict[str, Any]:
    metrics_payload = {}
    if paths["metrics_json"].exists():
        metrics_payload = json.loads(paths["metrics_json"].read_text(encoding="utf-8"))
    gemini_summary = metrics_payload.get("gemini_summary", {}) or {}
    observer_records = _observer_records(paths["observer_jsonl"])
    observer_summary = next(
        (
            record.get("payload", {})
            for record in reversed(observer_records)
            if record.get("record_type") == "observer_summary"
        ),
        {},
    )
    fault_records = _fault_records(paths["fault_events_jsonl"])
    return {
        "experiment": experiment,
        "scenario": scenario.get("name"),
        "baseline": baseline,
        "seed": seed,
        "mission": scenario.get("mission"),
        "scenario_config": str(paths["scenario_copy"]),
        "effective_prompt": str(paths["prompt_file"]),
        "artifacts_dir": str(paths["artifacts_dir"]),
        "replay_jsonl": str(paths["replay_jsonl"]),
        "observer_jsonl": str(paths["observer_jsonl"]),
        "metrics_json": str(paths["metrics_json"]),
        "fault_events_jsonl": str(paths["fault_events_jsonl"]),
        "metrics": metrics_payload.get("metrics", {}),
        "gemini_summary": gemini_summary,
        "observer_summary": observer_summary,
        "fault_event_count": len(fault_records),
        "manual_override_needed": bool(observer_summary.get("manual_override_needed")),
        "time_to_recovery_sec": observer_summary.get("time_to_first_recovery_sec"),
        "mission_completion_after_fault": observer_summary.get("mission_success"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--experiment", choices=["E2", "E4"], required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    parser.add_argument("--baseline", choices=["B0", "B1", "B3"], required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--stamp", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    expected_experiment = SCENARIOS[args.scenario]
    if expected_experiment != args.experiment:
        raise SystemExit(
            f"scenario {args.scenario} is defined for {expected_experiment}, not {args.experiment}"
        )
    scenario_path = root / "configs" / "experiments" / f"{args.scenario}.yaml"
    scenario = _load_yaml(scenario_path)
    stamp = args.stamp or _stamp()
    output_root = (args.output_root or (root / "artifacts" / "experiments")).resolve()
    run_root = output_root / stamp / args.experiment / args.scenario / args.baseline
    paths = {
        "run_root": run_root,
        "prompt_file": run_root / "effective_prompt.md",
        "scenario_copy": run_root / "scenario.yaml",
        "summary_json": run_root / "summary.json",
        "artifacts_dir": run_root / "control_artifacts",
        "replay_jsonl": run_root / "replay" / "gemini.jsonl",
        "observer_jsonl": run_root / "replay" / "observer.jsonl",
        "gemini_log": run_root / "logs" / "gemini.jsonl",
        "metrics_json": run_root / "metrics" / "summary.json",
        "fault_events_jsonl": run_root / "fault_events.jsonl",
    }
    payload = {
        "experiment": args.experiment,
        "scenario": args.scenario,
        "baseline": args.baseline,
        "seed": args.seed,
        "mission": scenario.get("mission"),
        "run_root": str(run_root),
        "scenario_config": str(scenario_path),
        "prompt_file": str(paths["prompt_file"]),
        "artifacts_dir": str(paths["artifacts_dir"]),
    }
    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    run_root.mkdir(parents=True, exist_ok=True)
    _copy_artifacts(root, paths["artifacts_dir"])
    common_prompt = _common_prompt_file(root).read_text(encoding="utf-8").rstrip()
    baseline_prompt = _default_prompt_file(root, args.baseline).read_text(encoding="utf-8").rstrip()
    prompt_suffix = str(scenario.get("prompt_appendix", "")).strip()
    prompt_text = common_prompt + "\n\n" + baseline_prompt + "\n\n"
    prompt_text += f"Scenario: {args.scenario}\nExperiment: {args.experiment}\nSeed: {args.seed}\n"
    if prompt_suffix:
        prompt_text += "\n" + prompt_suffix + "\n"
    paths["prompt_file"].write_text(prompt_text, encoding="utf-8")
    paths["scenario_copy"].write_text(
        yaml.safe_dump(scenario, sort_keys=False),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "BASELINE": args.baseline,
            "STAMP": stamp,
            "SEED": str(args.seed),
            "PROMPT_FILE": str(paths["prompt_file"]),
            "PROMPT_FILE_IS_COMPLETE": "1",
            "REPLAY_JSONL": str(paths["replay_jsonl"]),
            "OBSERVER_JSONL": str(paths["observer_jsonl"]),
            "FAULT_EVENTS_JSONL": str(paths["fault_events_jsonl"]),
            "GEMINI_LOG": str(paths["gemini_log"]),
            "METRICS_JSON": str(paths["metrics_json"]),
            "OUTPUT_ROOT": str(run_root),
            "CONTROL_VIEW_ARTIFACTS_DIR": str(paths["artifacts_dir"]),
        }
    )
    injector_process = None
    if scenario.get("steps"):
        injector_process = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                str(root / "scripts" / "live_fault_injector.py"),
                "--scenario-config",
                str(scenario_path),
                "--artifacts-dir",
                str(paths["artifacts_dir"]),
                "--observer-jsonl",
                str(paths["observer_jsonl"]),
                "--seed",
                str(args.seed),
                "--output-jsonl",
                str(paths["fault_events_jsonl"]),
            ],
            cwd=root,
            env=env,
        )

    try:
        completed = subprocess.run(
            [
                "bash",
                str(root / "scripts" / "run_gemini_headless_demo.sh"),
                str(scenario.get("mission")),
                args.baseline,
            ],
            cwd=root,
            env=env,
            check=False,
        )
    finally:
        if injector_process is not None:
            injector_process.wait(timeout=120.0)

    summary = _summary_payload(
        scenario=scenario,
        experiment=args.experiment,
        baseline=args.baseline,
        seed=args.seed,
        paths=paths,
    )
    paths["summary_json"].write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
