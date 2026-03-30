from __future__ import annotations

from control_view.replay.gemini_logs import (
    load_gemini_run_summary,
    load_gemini_turn_metrics,
    merge_turn_metrics,
)


def test_load_gemini_turn_metrics_extracts_nested_usage(tmp_path) -> None:
    target = tmp_path / "gemini.jsonl"
    target.write_text(
        "\n".join(
            [
                '{"type":"info","message":"boot"}',
                '{"family":"GOTO","usageMetadata":{"promptTokenCount":123}}',
            ]
        )
        + "\n"
    )

    metrics = load_gemini_turn_metrics(target)

    assert metrics == [
        {
            "family": "GOTO",
            "prompt_tokens_per_turn": 123.0,
            "recorded_mono_ns": 0,
        }
    ]


def test_load_gemini_run_summary_extracts_result_stats(tmp_path) -> None:
    target = tmp_path / "gemini.jsonl"
    target.write_text(
        "\n".join(
            [
                '{"type":"info","message":"boot"}',
                (
                    '{"type":"result","stats":{"total_tokens":200,"input_tokens":150,'
                    '"output_tokens":25,"cached":75,"tool_calls":4,"duration_ms":1234}}'
                ),
            ]
        )
        + "\n"
    )

    summary = load_gemini_run_summary(target)

    assert summary == {
        "total_tokens": 200.0,
        "input_tokens": 150.0,
        "output_tokens": 25.0,
        "cached_tokens": 75.0,
        "tool_calls": 4,
        "duration_ms": 1234.0,
    }


def test_merge_turn_metrics_attaches_to_decision_records() -> None:
    merged = merge_turn_metrics(
        [
            {"record_type": "control_view_result", "family": "ARM", "payload": {"verdict": "ACT"}},
            {
                "record_type": "action_transition",
                "family": "ARM",
                "payload": {"state": "CONFIRMED"},
            },
            {"record_type": "control_view_result", "family": "GOTO", "payload": {"verdict": "ACT"}},
        ],
        [
            {
                "family": "ARM",
                "prompt_tokens_per_turn": 10.0,
            },
            {
                "family": "GOTO",
                "prompt_tokens_per_turn": 12.0,
            },
        ],
    )

    assert merged[0]["payload"]["prompt_tokens_per_turn"] == 10.0
    assert merged[2]["payload"]["prompt_tokens_per_turn"] == 12.0
