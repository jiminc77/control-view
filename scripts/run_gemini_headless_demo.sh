#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MISSION="${1:-goto_hold_land}"
SERVER_NAME="${SERVER_NAME:-control-view-sidecar}"
OUTPUT_FORMAT="${GEMINI_OUTPUT_FORMAT:-stream-json}"
APPROVAL_MODE="${GEMINI_APPROVAL_MODE:-yolo}"
STAMP="$(date +%Y%m%d_%H%M%S)"
REPLAY_JSONL="$ROOT/artifacts/replay/gemini_${MISSION}_${STAMP}.jsonl"
GEMINI_LOG="$ROOT/artifacts/logs/gemini_${MISSION}_${STAMP}.jsonl"
METRICS_JSON="$ROOT/artifacts/metrics/gemini_${MISSION}_${STAMP}.json"
PROMPT_FILE="${PROMPT_FILE:-$ROOT/docs/gemini_demo_prompt_ko.md}"

mkdir -p "$ROOT/artifacts/replay" "$ROOT/artifacts/logs" "$ROOT/artifacts/metrics"

MISSION_PROMPT="$(cat "$PROMPT_FILE")

Mission name: ${MISSION}
Allowed tools: control-view-sidecar only.
"

gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
trap 'gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true' EXIT

gemini mcp add "$SERVER_NAME" bash -lc \
  "cd \"$ROOT\" && uv run control-view-sidecar --root \"$ROOT\" --backend mavros --record-jsonl \"$REPLAY_JSONL\""

gemini \
  --include-directories "$ROOT" \
  --allowed-mcp-server-names "$SERVER_NAME" \
  --approval-mode "$APPROVAL_MODE" \
  --output-format "$OUTPUT_FORMAT" \
  --prompt "$MISSION_PROMPT" | tee "$GEMINI_LOG"

uv run python "$ROOT/scripts/export_gemini_metrics.py" \
  --replay-jsonl "$REPLAY_JSONL" \
  --gemini-log "$GEMINI_LOG" \
  --output "$METRICS_JSON"
