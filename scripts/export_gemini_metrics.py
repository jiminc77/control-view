#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_view.replay.gemini_logs import load_gemini_turn_metrics, merge_turn_metrics
from control_view.replay.metrics import compute_metrics
from control_view.replay.recorder import ReplayRecorder


def _metric_records(path: Path) -> list[dict]:
    records = []
    for record in ReplayRecorder.load_jsonl(path):
        if record.record_type in {"control_view_result", "execution_result"}:
            records.append(
                {
                    "family": record.family,
                    **record.payload,
                }
            )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-jsonl", type=Path, required=True)
    parser.add_argument("--gemini-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    replay_records = _metric_records(args.replay_jsonl)
    turn_metrics = load_gemini_turn_metrics(args.gemini_log)
    merged_records = merge_turn_metrics(replay_records, turn_metrics)
    payload = {
        "replay_jsonl": str(args.replay_jsonl),
        "gemini_log": str(args.gemini_log),
        "turn_metrics_count": len(turn_metrics),
        "metrics": compute_metrics(merged_records),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
