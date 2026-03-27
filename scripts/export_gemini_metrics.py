#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_view.replay.gemini_logs import load_gemini_turn_metrics, merge_turn_metrics
from control_view.replay.metrics import compute_metrics
from control_view.replay.recorder import ReplayRecorder


def _metric_records(path: Path | None) -> list[dict]:
    if path is None:
        return []
    return [
        record.model_dump(mode="json")
        for record in ReplayRecorder.load_jsonl(path)
    ]


def _turn_records(turn_metrics: list[dict], *, mission_id: str) -> list[dict]:
    return [
        {
            "record_type": "gemini_turn",
            "recorded_mono_ns": int(turn.get("recorded_mono_ns", 0) or 0),
            "metadata": {"mission_id": mission_id},
            "payload": {
                "family": turn.get("family"),
                "prompt_tokens_per_turn": turn.get("prompt_tokens_per_turn", 0.0),
                "decision_latency_ms": turn.get("decision_latency_ms", 0.0),
                "compressed": bool(turn.get("compressed")),
            },
        }
        for turn in turn_metrics
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-jsonl", type=Path, default=None)
    parser.add_argument("--observer-jsonl", type=Path, default=None)
    parser.add_argument("--gemini-log", type=Path, required=True)
    parser.add_argument("--mission-id", default="__default__")
    parser.add_argument("--token-budget", type=float, default=None)
    parser.add_argument("--time-budget-ms", type=float, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    replay_records = _metric_records(args.replay_jsonl)
    observer_records = _metric_records(args.observer_jsonl)
    turn_metrics = load_gemini_turn_metrics(args.gemini_log)
    if replay_records:
        merged_records = merge_turn_metrics(replay_records, turn_metrics)
    else:
        merged_records = _turn_records(turn_metrics, mission_id=args.mission_id)
    merged_records.extend(observer_records)
    payload = {
        "replay_jsonl": str(args.replay_jsonl) if args.replay_jsonl else None,
        "observer_jsonl": str(args.observer_jsonl) if args.observer_jsonl else None,
        "gemini_log": str(args.gemini_log),
        "turn_metrics_count": len(turn_metrics),
        "metrics": compute_metrics(
            merged_records,
            token_budget=args.token_budget,
            time_budget_ms=args.time_budget_ms,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
