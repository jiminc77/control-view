#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from control_view.backend.fake_backend import FakeBackend
from control_view.baselines import BASELINE_NAMES, normalize_baseline_name
from control_view.replay.fault_injector import FaultInjector
from control_view.replay.metrics import compute_metrics
from control_view.replay.oracle import RuleBasedOracle
from control_view.replay.recorder import ReplayRecorder
from control_view.replay.replayer import ReplayRunner
from control_view.service import ControlViewService


def _parse_fault_params(values: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"fault param must be key=value, got: {item}")
        key, raw_value = item.split("=", 1)
        lowered = raw_value.lower()
        if lowered in {"true", "false"}:
            parsed[key] = lowered == "true"
            continue
        try:
            parsed[key] = int(raw_value)
            continue
        except ValueError:
            pass
        try:
            parsed[key] = float(raw_value)
            continue
        except ValueError:
            pass
        parsed[key] = raw_value
    return parsed


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(record, sort_keys=True) for record in records)
    path.write_text(content + ("\n" if records else ""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--replay-jsonl", type=Path, required=True)
    parser.add_argument("--policy-swap", choices=list(BASELINE_NAMES), default="B3")
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--fault", default=None)
    parser.add_argument("--fault-param", action="append", default=[])
    parser.add_argument("--slot-ablation", action="append", default=[])
    parser.add_argument("--b2-ttl-sec", type=float, default=5.0)
    parser.add_argument("--token-budget", type=float, default=None)
    parser.add_argument("--time-budget-ms", type=float, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counterexamples-jsonl", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    records = ReplayRecorder.load_jsonl(args.replay_jsonl)
    fault_params = _parse_fault_params(args.fault_param)
    outputs = ReplayRunner(
        ControlViewService(root, backend=FakeBackend()),
    ).replay(
        records,
        fault_injector=FaultInjector() if args.fault else None,
        fault_name=args.fault,
        fault_params=fault_params,
        slot_ablation=args.slot_ablation,
        policy_swap=normalize_baseline_name(args.policy_swap),
        oracle=RuleBasedOracle(),
        b2_ttl_sec=args.b2_ttl_sec,
    )
    legacy_trace_count = sum(1 for output in outputs if bool(output.get("legacy_trace")))
    metrics = compute_metrics(
        outputs,
        token_budget=args.token_budget,
        time_budget_ms=args.time_budget_ms,
    )
    counterexamples = [
        output
        for output in outputs
        if output.get("verdict") is not None
        and output.get("oracle_verdict") is not None
        and output["verdict"] != output["oracle_verdict"]
    ]
    payload = {
        "replay_jsonl": str(args.replay_jsonl),
        "policy_swap": args.policy_swap,
        "scenario": args.scenario,
        "seed": args.seed,
        "fault": args.fault,
        "fault_params": fault_params,
        "slot_ablation": args.slot_ablation,
        "b2_ttl_sec": args.b2_ttl_sec,
        "token_budget": args.token_budget,
        "time_budget_ms": args.time_budget_ms,
        "output_count": len(outputs),
        "counterexample_count": len(counterexamples),
        "legacy_trace_count": legacy_trace_count,
        "official_trace_ready": legacy_trace_count == 0,
        "metrics": metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if args.counterexamples_jsonl is not None:
        _write_jsonl(args.counterexamples_jsonl, counterexamples)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
