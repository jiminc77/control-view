#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_ROOT="${PX4_ROOT:-$ROOT/../PX4-Autopilot}"
BACKEND_CONFIG="${BACKEND_CONFIG:-$ROOT/configs/backend_mavros.yaml}"
LOG_DIR="${LOG_DIR:-$ROOT/artifacts/logs}"
MISSIONS=("$@")

if [[ ${#MISSIONS[@]} -eq 0 ]]; then
  MISSIONS=("takeoff_hold_land" "goto_hold_land" "goto_rtl")
fi

mkdir -p "$LOG_DIR" "$ROOT/artifacts/replay" "$ROOT/artifacts/metrics"

if [[ ! -d "$PX4_ROOT" ]]; then
  echo "PX4 root not found: $PX4_ROOT" >&2
  exit 1
fi

set +u
source /opt/ros/jazzy/setup.bash
set -u

cleanup() {
  if [[ -n "${MAVROS_PID:-}" ]]; then
    kill "$MAVROS_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PX4_PID:-}" ]]; then
    kill "$PX4_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

wait_for_service() {
  local service_name="$1"
  local attempts="${2:-60}"
  for _ in $(seq 1 "$attempts"); do
    if ros2 service list 2>/dev/null | grep -qx "$service_name"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_connected_state() {
  local attempts="${1:-20}"
  local state_output
  for _ in $(seq 1 "$attempts"); do
    if state_output="$(timeout 5s ros2 topic echo /mavros/state --once 2>/dev/null)" \
      && grep -q "connected: true" <<<"$state_output"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

echo "Starting PX4 SITL from $PX4_ROOT"
HEADLESS=1 make -C "$PX4_ROOT" px4_sitl gz_x500 >"$LOG_DIR/px4_sitl.log" 2>&1 &
PX4_PID=$!
sleep 10

echo "Starting MAVROS bridge"
ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557 >"$LOG_DIR/mavros.log" 2>&1 &
MAVROS_PID=$!

if ! wait_for_service /mavros/set_mode 60; then
  echo "Timed out waiting for /mavros/set_mode" >&2
  exit 1
fi

if ! wait_for_connected_state 20; then
  echo "Timed out waiting for connected /mavros/state" >&2
  exit 1
fi

uv run python -m control_view.app \
  --root "$ROOT" \
  --backend mavros \
  --backend-config "$BACKEND_CONFIG" \
  --dry-run | tee "$LOG_DIR/sidecar_dry_run.log"

for mission in "${MISSIONS[@]}"; do
  echo "Running mission: $mission"
  uv run python "$ROOT/scripts/run_mission.py" \
    --root "$ROOT" \
    --backend-config "$BACKEND_CONFIG" \
    --mission "$mission" | tee "$LOG_DIR/${mission}.log"
done
