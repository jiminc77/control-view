from __future__ import annotations

from control_view.replay.gemini_logs import load_gemini_turn_metrics, merge_turn_metrics


def test_load_gemini_turn_metrics_extracts_nested_usage(tmp_path) -> None:
    target = tmp_path / "gemini.jsonl"
    target.write_text(
        "\n".join(
            [
                '{"type":"info","message":"boot"}',
                '{"family":"GOTO","usageMetadata":{"promptTokenCount":123},"latencyMs":456}',
            ]
        )
        + "\n"
    )

    metrics = load_gemini_turn_metrics(target)

    assert metrics == [
        {
            "family": "GOTO",
            "prompt_tokens_per_turn": 123.0,
            "decision_latency_ms": 456.0,
        }
    ]


def test_merge_turn_metrics_attaches_to_decision_records() -> None:
    merged = merge_turn_metrics(
        [
            {"family": "ARM", "verdict": "ACT"},
            {"family": "ARM", "status": "CONFIRMED"},
            {"family": "GOTO", "verdict": "ACT"},
        ],
        [
            {"family": "ARM", "prompt_tokens_per_turn": 10.0, "decision_latency_ms": 100.0},
            {"family": "GOTO", "prompt_tokens_per_turn": 12.0, "decision_latency_ms": 120.0},
        ],
    )

    assert merged[0]["prompt_tokens_per_turn"] == 10.0
    assert merged[0]["decision_latency_ms"] == 100.0
    assert merged[2]["prompt_tokens_per_turn"] == 12.0
    assert merged[2]["decision_latency_ms"] == 120.0
