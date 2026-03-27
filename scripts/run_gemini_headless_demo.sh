#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MISSION="${1:-goto_hold_land}"
BASELINE="${BASELINE:-${2:-B3}}"
OUTPUT_FORMAT="${GEMINI_OUTPUT_FORMAT:-stream-json}"
APPROVAL_MODE="${GEMINI_APPROVAL_MODE:-yolo}"
STAMP="$(date +%Y%m%d_%H%M%S)"
SERVER_NAME="${SERVER_NAME:-control-view-${BASELINE,,}}"
REPLAY_JSONL="$ROOT/artifacts/replay/gemini_${BASELINE}_${MISSION}_${STAMP}.jsonl"
OBSERVER_JSONL="$ROOT/artifacts/replay/observer_${BASELINE}_${MISSION}_${STAMP}.jsonl"
GEMINI_LOG="$ROOT/artifacts/logs/gemini_${BASELINE}_${MISSION}_${STAMP}.jsonl"
METRICS_JSON="$ROOT/artifacts/metrics/gemini_${BASELINE}_${MISSION}_${STAMP}.json"

case "$BASELINE" in
  B0) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_demo_prompt_b0_ko.md" ;;
  B1) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_demo_prompt_b1_ko.md" ;;
  B3) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_demo_prompt_ko.md" ;;
  *)
    echo "Unsupported baseline: $BASELINE" >&2
    exit 1
    ;;
esac
PROMPT_FILE="${PROMPT_FILE:-$DEFAULT_PROMPT_FILE}"

mkdir -p "$ROOT/artifacts/replay" "$ROOT/artifacts/logs" "$ROOT/artifacts/metrics"

MISSION_PROMPT="$(cat "$PROMPT_FILE")

Mission name: ${MISSION}
Baseline: ${BASELINE}
"

gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
cleanup() {
  gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
  if [[ -n "${OBSERVER_PID:-}" ]]; then
    kill "$OBSERVER_PID" >/dev/null 2>&1 || true
    wait "$OBSERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

stop_observer() {
  if [[ -n "${OBSERVER_PID:-}" ]]; then
    kill "$OBSERVER_PID" >/dev/null 2>&1 || true
    wait "$OBSERVER_PID" >/dev/null 2>&1 || true
    OBSERVER_PID=""
  fi
}

uv run control-view-observer \
  --mission "$MISSION" \
  --output-jsonl "$OBSERVER_JSONL" \
  --stop-when-complete &
OBSERVER_PID=$!
sleep 1

case "$BASELINE" in
  B0)
    if [[ -z "${ROS_MCP_BASELINE_COMMAND:-}" ]]; then
      echo "ROS_MCP_BASELINE_COMMAND is required for B0" >&2
      exit 1
    fi
    SERVER_COMMAND="${ROS_MCP_BASELINE_COMMAND}"
    ;;
  B1)
    SERVER_COMMAND="cd \"$ROOT\" && uv run control-view-sidecar --root \"$ROOT\" --backend mavros --tool-surface thin --baseline-policy B1 --record-jsonl \"$REPLAY_JSONL\""
    ;;
  B3)
    SERVER_COMMAND="cd \"$ROOT\" && uv run control-view-sidecar --root \"$ROOT\" --backend mavros --tool-surface full --baseline-policy B3 --record-jsonl \"$REPLAY_JSONL\""
    ;;
esac

gemini mcp add "$SERVER_NAME" bash -lc \
  "$SERVER_COMMAND"

gemini \
  --include-directories "$ROOT" \
  --allowed-mcp-server-names "$SERVER_NAME" \
  --approval-mode "$APPROVAL_MODE" \
  --output-format "$OUTPUT_FORMAT" \
  --prompt "$MISSION_PROMPT" | tee "$GEMINI_LOG"

stop_observer

if [[ "$BASELINE" != "B0" ]]; then
  uv run python "$ROOT/scripts/export_gemini_metrics.py" \
    --replay-jsonl "$REPLAY_JSONL" \
    --gemini-log "$GEMINI_LOG" \
    --observer-jsonl "$OBSERVER_JSONL" \
    --mission-id "$MISSION" \
    --output "$METRICS_JSON"
else
  uv run python "$ROOT/scripts/export_gemini_metrics.py" \
    --gemini-log "$GEMINI_LOG" \
    --observer-jsonl "$OBSERVER_JSONL" \
    --mission-id "$MISSION" \
    --output "$METRICS_JSON"
fi
