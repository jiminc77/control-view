from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_PROMPT_TOKEN_KEYS = {
    "prompt_tokens_per_turn",
    "prompt_tokens",
    "promptTokenCount",
    "input_tokens",
    "inputTokenCount",
}
_LATENCY_KEYS = {
    "decision_latency_ms",
    "latency_ms",
    "latencyMs",
}
_FAMILY_KEYS = {
    "family",
}


def _iter_scalars(value: Any, *, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            yield from _iter_scalars(item_value, key=item_key)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_scalars(item, key=key)
        return
    yield key, value


def _first_number(payload: dict[str, Any], keys: set[str]) -> float | None:
    for key, value in _iter_scalars(payload):
        if key not in keys:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_text(payload: dict[str, Any], keys: set[str]) -> str | None:
    for key, value in _iter_scalars(payload):
        if key in keys and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def load_gemini_turn_metrics(path: str | Path) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    target = Path(path)
    if not target.exists():
        return metrics
    for line in target.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        prompt_tokens = _first_number(payload, _PROMPT_TOKEN_KEYS)
        latency_ms = _first_number(payload, _LATENCY_KEYS)
        family = _first_text(payload, _FAMILY_KEYS)
        if prompt_tokens is None and latency_ms is None:
            continue
        metrics.append(
            {
                "family": family,
                "prompt_tokens_per_turn": prompt_tokens or 0.0,
                "decision_latency_ms": latency_ms or 0.0,
            }
        )
    return metrics


def merge_turn_metrics(
    records: list[dict[str, Any]],
    turn_metrics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [dict(record) for record in records]
    decision_indexes = [
        index
        for index, record in enumerate(merged)
        if "verdict" in record
    ]
    for index, turn_metric in zip(decision_indexes, turn_metrics, strict=False):
        merged[index]["prompt_tokens_per_turn"] = round(
            float(turn_metric.get("prompt_tokens_per_turn", 0.0)),
            4,
        )
        merged[index]["decision_latency_ms"] = round(
            float(turn_metric.get("decision_latency_ms", 0.0)),
            4,
        )
        family = turn_metric.get("family")
        if family and not merged[index].get("family"):
            merged[index]["family"] = family
    return merged
