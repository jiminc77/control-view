#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MISSION="${1:-goto_hold_land}"
BASELINE="${BASELINE:-${2:-B3}}"
OUTPUT_FORMAT="${GEMINI_OUTPUT_FORMAT:-stream-json}"
APPROVAL_MODE="${GEMINI_APPROVAL_MODE:-yolo}"
MODEL_NAME="${GEMINI_MODEL:-}"
BACKEND="${CONTROL_VIEW_BACKEND:-mavros}"
BACKEND_CONFIG="${CONTROL_VIEW_BACKEND_CONFIG:-configs/backend_mavros.yaml}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
SERVER_NAME="${SERVER_NAME:-control-view-${BASELINE,,}}"
B0_SERVER_NAME="${B0_SERVER_NAME:-${ROS_MCP_SERVER_NAME:-ros-mcp-server}}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT/artifacts}"
REPLAY_JSONL="${REPLAY_JSONL:-$OUTPUT_ROOT/replay/gemini_${BASELINE}_${MISSION}_${STAMP}.jsonl}"
OBSERVER_JSONL="${OBSERVER_JSONL:-$OUTPUT_ROOT/replay/observer_${BASELINE}_${MISSION}_${STAMP}.jsonl}"
GEMINI_LOG="${GEMINI_LOG:-$OUTPUT_ROOT/logs/gemini_${BASELINE}_${MISSION}_${STAMP}.jsonl}"
METRICS_JSON="${METRICS_JSON:-$OUTPUT_ROOT/metrics/gemini_${BASELINE}_${MISSION}_${STAMP}.json}"
SQLITE_PATH="${SQLITE_PATH:-$OUTPUT_ROOT/control_view.sqlite3}"
POLICY_FILE="${POLICY_FILE:-$ROOT/.gemini/policies/only_mcp.toml}"
KEEP_GEMINI_LOG="${KEEP_GEMINI_LOG:-0}"
DYNAMIC_MCP_SERVER=0

case "$BASELINE" in
  B0) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_prompt_b0_raw_ros_ko.md" ;;
  B1) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_prompt_b1_thin_transcript_ko.md" ;;
  B3) DEFAULT_PROMPT_FILE="$ROOT/docs/gemini_prompt_b3_semantic_step_ko.md" ;;
  *)
    echo "Unsupported baseline: $BASELINE" >&2
    exit 1
    ;;
esac
COMMON_PROMPT_FILE="${COMMON_PROMPT_FILE:-$ROOT/docs/gemini_prompt_common_rules_en.md}"
PROMPT_FILE="${PROMPT_FILE:-$DEFAULT_PROMPT_FILE}"

mkdir -p "$(dirname "$REPLAY_JSONL")" "$(dirname "$OBSERVER_JSONL")" "$(dirname "$GEMINI_LOG")" "$(dirname "$METRICS_JSON")"

MISSION_PROMPT="$(cat "$COMMON_PROMPT_FILE")

$(cat "$PROMPT_FILE")

Mission name: ${MISSION}
Baseline: ${BASELINE}
"

OBSERVER_ARGS=(
  --mission "$MISSION"
  --output-jsonl "$OBSERVER_JSONL"
  --stop-when-complete
)

if [[ -n "${FAULT_EVENTS_JSONL:-}" ]]; then
  OBSERVER_ARGS+=(--fault-events-jsonl "$FAULT_EVENTS_JSONL")
fi

MODEL_ARGS=()
if [[ -n "$MODEL_NAME" ]]; then
  MODEL_ARGS=(--model "$MODEL_NAME")
fi

# B0/B1 should preserve Gemini CLI's text/transcript tool path.
# B3 still gets structured function responses because its model surface
# returns empty content plus structuredContent only.
export GEMINI_CLI_PREFER_MCP_STRUCTURED_CONTENT="false"

cleanup() {
  if [[ "$DYNAMIC_MCP_SERVER" == "1" ]]; then
    gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
  fi
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

uv run control-view-observer "${OBSERVER_ARGS[@]}" &
OBSERVER_PID=$!
sleep 1

if [[ "$BASELINE" != "B0" ]]; then
  uv run python "$ROOT/scripts/patch_gemini_cli_mcp_structured.py"
fi

case "$BASELINE" in
  B0)
    gemini \
      "${MODEL_ARGS[@]}" \
      --include-directories "$ROOT" \
      --allowed-mcp-server-names "$B0_SERVER_NAME" \
      --policy "$POLICY_FILE" \
      --approval-mode "$APPROVAL_MODE" \
      --output-format "$OUTPUT_FORMAT" \
      --prompt "$MISSION_PROMPT" | tee "$GEMINI_LOG"
    ;;
  B1)
    gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
    SERVER_COMMAND="cd \"$ROOT\" && uv run control-view-sidecar --root \"$ROOT\" --backend \"$BACKEND\" --backend-config \"$BACKEND_CONFIG\" --sqlite-path \"$SQLITE_PATH\" --tool-surface thin --baseline-policy B1 --record-jsonl \"$REPLAY_JSONL\""
    gemini mcp add "$SERVER_NAME" bash -lc \
      "$SERVER_COMMAND"
    DYNAMIC_MCP_SERVER=1
    gemini \
      "${MODEL_ARGS[@]}" \
      --include-directories "$ROOT" \
      --allowed-mcp-server-names "$SERVER_NAME" \
      --policy "$POLICY_FILE" \
      --approval-mode "$APPROVAL_MODE" \
      --output-format "$OUTPUT_FORMAT" \
      --prompt "$MISSION_PROMPT" | tee "$GEMINI_LOG"
    ;;
  B3)
    gemini mcp remove "$SERVER_NAME" >/dev/null 2>&1 || true
    SERVER_COMMAND="cd \"$ROOT\" && uv run control-view-sidecar --root \"$ROOT\" --backend \"$BACKEND\" --backend-config \"$BACKEND_CONFIG\" --sqlite-path \"$SQLITE_PATH\" --tool-surface model --baseline-policy B3 --record-jsonl \"$REPLAY_JSONL\""
    gemini mcp add "$SERVER_NAME" bash -lc \
      "$SERVER_COMMAND"
    DYNAMIC_MCP_SERVER=1
    gemini \
      "${MODEL_ARGS[@]}" \
      --include-directories "$ROOT" \
      --allowed-mcp-server-names "$SERVER_NAME" \
      --policy "$POLICY_FILE" \
      --approval-mode "$APPROVAL_MODE" \
      --output-format "$OUTPUT_FORMAT" \
      --prompt "$MISSION_PROMPT" | tee "$GEMINI_LOG"
    ;;
esac

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

if [[ "$KEEP_GEMINI_LOG" != "1" ]]; then
  rm -f "$GEMINI_LOG"
fi
