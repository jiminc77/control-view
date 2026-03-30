#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_ROOT="${LOG_ROOT:-$ROOT/artifacts/logs/live_runs}"
EXPERIMENT="${1:?experiment is required}"
SCENARIO="${2:?scenario is required}"
BASELINE="${3:?baseline is required}"
SEED="${4:-0}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"

cleanup() {
  bash "$ROOT/scripts/reset_live_stack.sh" \
    --root "$ROOT" \
    --stamp "$STAMP" \
    --job-name "${EXPERIMENT}_${SCENARIO}_${BASELINE}" \
    --stop-only >/dev/null 2>&1 || true
}
trap cleanup EXIT
bash "$ROOT/scripts/reset_live_stack.sh" \
  --root "$ROOT" \
  --log-root "$ROOT/artifacts/logs/live_stack" \
  --stamp "$STAMP" \
  --job-name "${EXPERIMENT}_${SCENARIO}_${BASELINE}"

echo "Running fresh live experiment: $EXPERIMENT / $SCENARIO / $BASELINE / seed=$SEED"
uv run python "$ROOT/scripts/run_live_experiments.py" \
  --root "$ROOT" \
  --experiment "$EXPERIMENT" \
  --scenario "$SCENARIO" \
  --baseline "$BASELINE" \
  --seed "$SEED" \
  --output-root "$ROOT/artifacts/experiments"
