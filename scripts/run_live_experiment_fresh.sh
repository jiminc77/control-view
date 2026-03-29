#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_ROOT="${PX4_ROOT:-$ROOT/../PX4-Autopilot}"
PX4_ROOT="$(cd "$PX4_ROOT" && pwd)"
LOG_ROOT="${LOG_ROOT:-$ROOT/artifacts/logs/live_runs}"
EXPERIMENT="${1:?experiment is required}"
SCENARIO="${2:?scenario is required}"
BASELINE="${3:?baseline is required}"
SEED="${4:-0}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
RUN_LOG_DIR="$LOG_ROOT/$STAMP/${EXPERIMENT}_${SCENARIO}_${BASELINE}"

mkdir -p "$RUN_LOG_DIR"

set +u
source /opt/ros/jazzy/setup.bash
set -u

cleanup() {
  if [[ -n "${MAVROS_PID:-}" ]]; then
    kill "$MAVROS_PID" >/dev/null 2>&1 || true
    wait "$MAVROS_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${PX4_PID:-}" ]]; then
    kill "$PX4_PID" >/dev/null 2>&1 || true
    wait "$PX4_PID" >/dev/null 2>&1 || true
  fi
  pkill -f "$PX4_ROOT/build/px4_sitl_default/bin/px4" >/dev/null 2>&1 || true
  pkill -f "ros2 launch mavros px4.launch" >/dev/null 2>&1 || true
  pkill -f "/opt/ros/jazzy/lib/mavros/mavros_node" >/dev/null 2>&1 || true
  pkill -f "$PX4_ROOT/Tools/simulation/gz/worlds/default.sdf" >/dev/null 2>&1 || true
  pkill -f "^gz sim -g$" >/dev/null 2>&1 || true
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

wait_for_ready_state() {
  local attempts="${1:-30}"
  local state_output
  for _ in $(seq 1 "$attempts"); do
    if state_output="$(timeout 5s ros2 topic echo /mavros/state --once 2>/dev/null)" \
      && grep -q "connected: true" <<<"$state_output" \
      && grep -Eq "system_status: [1-9][0-9]*" <<<"$state_output"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_topic_once() {
  local topic_name="$1"
  local attempts="${2:-30}"
  for _ in $(seq 1 "$attempts"); do
    if timeout 5s ros2 topic echo "$topic_name" --once >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

echo "Cleaning up stale PX4 SITL, MAVROS, and Gazebo processes"
cleanup
sleep 2

echo "Starting PX4 SITL from $PX4_ROOT"
HEADLESS=1 make -C "$PX4_ROOT" px4_sitl gz_x500 >"$RUN_LOG_DIR/px4_sitl.log" 2>&1 &
PX4_PID=$!
sleep 10

echo "Starting MAVROS bridge"
ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557 >"$RUN_LOG_DIR/mavros.log" 2>&1 &
MAVROS_PID=$!

if ! wait_for_service /mavros/set_mode 60; then
  echo "Timed out waiting for /mavros/set_mode" >&2
  exit 1
fi

if ! wait_for_ready_state 30; then
  echo "Timed out waiting for ready /mavros/state (connected=true and system_status>0)" >&2
  exit 1
fi

if ! wait_for_topic_once /mavros/local_position/odom 30; then
  echo "Timed out waiting for /mavros/local_position/odom" >&2
  exit 1
fi

if ! wait_for_topic_once /mavros/home_position/home 30; then
  echo "Timed out waiting for /mavros/home_position/home" >&2
  exit 1
fi

if ! wait_for_topic_once /mavros/global_position/global 30; then
  echo "Timed out waiting for /mavros/global_position/global" >&2
  exit 1
fi

echo "Running fresh live experiment: $EXPERIMENT / $SCENARIO / $BASELINE / seed=$SEED"
uv run python "$ROOT/scripts/run_live_experiments.py" \
  --root "$ROOT" \
  --experiment "$EXPERIMENT" \
  --scenario "$SCENARIO" \
  --baseline "$BASELINE" \
  --seed "$SEED" \
  --output-root "$ROOT/artifacts/experiments"
