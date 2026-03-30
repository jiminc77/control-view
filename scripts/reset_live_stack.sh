#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PX4_ROOT_DEFAULT="$ROOT/../PX4-Autopilot"
PX4_ROOT="${PX4_ROOT:-$PX4_ROOT_DEFAULT}"
PX4_ROOT="$(cd "$PX4_ROOT" && pwd)"
LOG_ROOT="$ROOT/artifacts/logs/live_stack"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
JOB_NAME="manual"
ATTEMPT="1"
STOP_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$(cd "$2" && pwd)"
      PX4_ROOT_DEFAULT="$ROOT/../PX4-Autopilot"
      PX4_ROOT="${PX4_ROOT:-$PX4_ROOT_DEFAULT}"
      PX4_ROOT="$(cd "$PX4_ROOT" && pwd)"
      shift 2
      ;;
    --log-root)
      LOG_ROOT="$2"
      shift 2
      ;;
    --stamp)
      STAMP="$2"
      shift 2
      ;;
    --job-name)
      JOB_NAME="$2"
      shift 2
      ;;
    --attempt)
      ATTEMPT="$2"
      shift 2
      ;;
    --stop-only)
      STOP_ONLY=1
      shift
      ;;
    *)
      echo "Unsupported argument: $1" >&2
      exit 1
      ;;
  esac
done

RUN_LOG_DIR="$LOG_ROOT/$STAMP/${JOB_NAME}/attempt_${ATTEMPT}"
mkdir -p "$RUN_LOG_DIR"

set +u
source /opt/ros/jazzy/setup.bash
set -u

cleanup() {
  pkill -f "$PX4_ROOT/build/px4_sitl_default/bin/px4" >/dev/null 2>&1 || true
  pkill -f "ros2 launch mavros px4.launch" >/dev/null 2>&1 || true
  pkill -f "/opt/ros/jazzy/lib/mavros/mavros_node" >/dev/null 2>&1 || true
  pkill -f "ros2 launch rosbridge_server rosbridge_websocket_launch.xml" >/dev/null 2>&1 || true
  pkill -f "rosbridge_websocket" >/dev/null 2>&1 || true
  pkill -f "$PX4_ROOT/Tools/simulation/gz/worlds/default.sdf" >/dev/null 2>&1 || true
  pkill -f "^gz sim -g$" >/dev/null 2>&1 || true
}

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

wait_for_tcp_port() {
  local host="$1"
  local port="$2"
  local attempts="${3:-30}"
  for _ in $(seq 1 "$attempts"); do
    if timeout 1 bash -lc "cat < /dev/null > /dev/tcp/$host/$port" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

if [[ "$STOP_ONLY" == "1" ]]; then
  cleanup
  exit 0
fi

echo "Resetting PX4 SITL, MAVROS, and rosbridge"
cleanup
sleep 2

echo "Starting PX4 SITL from $PX4_ROOT"
setsid -f env HEADLESS=1 make -C "$PX4_ROOT" px4_sitl gz_x500 \
  >"$RUN_LOG_DIR/px4_sitl.log" 2>&1 < /dev/null
sleep 10

echo "Starting MAVROS bridge"
setsid -f bash -lc "source /opt/ros/jazzy/setup.bash && ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557" \
  >"$RUN_LOG_DIR/mavros.log" 2>&1 < /dev/null

echo "Starting rosbridge websocket on 9090"
setsid -f bash -lc "source /opt/ros/jazzy/setup.bash && ros2 launch rosbridge_server rosbridge_websocket_launch.xml port:=9090" \
  >"$RUN_LOG_DIR/rosbridge.log" 2>&1 < /dev/null

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

if ! wait_for_tcp_port 127.0.0.1 9090 30; then
  echo "Timed out waiting for rosbridge websocket on 127.0.0.1:9090" >&2
  exit 1
fi

echo "Live stack ready"
echo "logs=$RUN_LOG_DIR"
