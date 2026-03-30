# Control View Runbook

이 문서는 현재 저장소를 실제로 실행하는 절차만 다룹니다. 설명보다 실행 순서, 절대 경로 예시,
baseline별 차이, 산출물 위치를 우선합니다.

## 1. 사전조건

### 저장소 배치

권장 배치는 아래 둘 중 하나입니다.

1. sibling 배치

```text
/path/to/control-view
/path/to/PX4-Autopilot
```

2. 임의 배치 + `PX4_ROOT` 명시

```bash
export PX4_ROOT=/abs/path/to/PX4-Autopilot
```

`run_sitl_smoke.sh`와 문서 예시는 sibling 배치를 기준으로 적었고, 다르면 `PX4_ROOT`를
명시하면 됩니다.

### 필수 소프트웨어

- Ubuntu 24.04
- ROS 2 Jazzy
- MAVROS
- `uv`
- Gemini CLI

Gemini CLI는 아래처럼 확인합니다. 현재 검증 기준 버전은 `0.35.0`입니다.

```bash
gemini --version
```

### Python 환경

```bash
cd /abs/path/to/control-view
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
```

`--system-site-packages`를 유지해야 ROS 2 Jazzy가 시스템 Python에 설치한 `rclpy`,
`mavros_msgs`를 그대로 씁니다.

## 2. 로컬 Smoke

```bash
cd /abs/path/to/control-view
source .venv/bin/activate

uv run pytest
uv run ruff check src tests scripts/*.py
uv run python -m control_view.app --backend fake --dry-run
uv run control-view-observer --help
bash -n scripts/*.sh
```

이 네 개가 먼저 통과해야 합니다.

## 3. PX4 SITL + MAVROS

### PX4 SITL

```bash
cd /abs/path/to/PX4-Autopilot
HEADLESS=1 make px4_sitl gz_x500
```

PX4 shell에 들어갈 수 있으면 아래 한 줄을 먼저 넣어두는 편이 안전합니다.

```text
param set NAV_DLL_ACT 0
```

### MAVROS

다른 터미널에서:

```bash
source /opt/ros/jazzy/setup.bash
ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557
```

### 연결 확인

```bash
source /opt/ros/jazzy/setup.bash
ros2 topic echo /mavros/state --once
ros2 topic echo /mavros/local_position/odom --once
ros2 service type /mavros/set_mode
ros2 service type /mavros/cmd/command
```

`/mavros/state`에서 `connected: true`가 보여야 합니다.
실제 재실행 때는 `system_status`가 `0`이 아닌 값까지 확인하는 편이 안전합니다.
`system_status: 0`이 오래 지속되거나 기체가 뒤집힌 자세로 뜨면 stale Gazebo world를 재사용한 경우가 많으니
남아 있는 `gz sim`, PX4, MAVROS 프로세스를 종료하고 다시 올립니다.

## 4. Sidecar / Observer 수동 실행

### `B3` full sidecar

```bash
cd /abs/path/to/control-view
source .venv/bin/activate

uv run control-view-sidecar \
  --root /abs/path/to/control-view \
  --backend mavros \
  --tool-surface full \
  --baseline-policy B3
```

### `B1` thin surface

```bash
uv run control-view-sidecar \
  --root /abs/path/to/control-view \
  --backend mavros \
  --tool-surface thin \
  --baseline-policy B1
```

### Observer

```bash
uv run control-view-observer \
  --mission goto_hold_land \
  --output-jsonl /abs/path/to/control-view/artifacts/replay/observer_manual.jsonl \
  --stop-when-complete
```

### `B0`

`B0`는 sidecar를 띄우지 않습니다. raw `ros-mcp-server` 실행 명령을 사용자가 준비해야 합니다.

```bash
export ROS_MCP_BASELINE_COMMAND='source /opt/ros/jazzy/setup.bash && ros-mcp-server ...'
```

## 5. Nominal Trace 재생성

공식 replay 실험은 반드시 새 trace로 다시 생성해야 합니다. 이유는 현재 코드가
`decision_context`를 기록하고, legacy trace는 `official_trace_ready=false`로만 읽기 때문입니다.

```bash
cd /abs/path/to/control-view
source .venv/bin/activate

export PX4_ROOT=${PX4_ROOT:-/abs/path/to/PX4-Autopilot}
./scripts/run_sitl_smoke.sh takeoff_hold_land goto_hold_land goto_rtl
```

이 스크립트는 시작 전에 남아 있는 `gz sim`, PX4 SITL, MAVROS 프로세스를 정리해서
dirty simulation world를 물고 올라오지 않게 합니다.

생성 파일:

- `artifacts/replay/takeoff_hold_land.jsonl`
- `artifacts/replay/goto_hold_land.jsonl`
- `artifacts/replay/goto_rtl.jsonl`
- `artifacts/metrics/<mission>.json`
- `artifacts/logs/px4_sitl.log`
- `artifacts/logs/mavros.log`

새 trace인지 확인하려면 아래를 실행합니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --output /tmp/goto_hold_land_check.json
```

출력 JSON에서 아래 두 값이 중요합니다.

- `legacy_trace_count == 0`
- `official_trace_ready == true`

둘 중 하나라도 아니면 trace를 다시 생성해야 합니다.

## 6. Replay 실험 CLI

기본 형식:

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --scenario t3_recovery \
  --seed 31 \
  --fault offboard_stream_loss \
  --slot-ablation pose.local \
  --b2-ttl-sec 5 \
  --token-budget 4000 \
  --time-budget-ms 15000 \
  --output /abs/path/to/control-view/artifacts/metrics/example.json \
  --counterexamples-jsonl /abs/path/to/control-view/artifacts/replay/example_counterexamples.jsonl
```

주요 플래그:

- `--policy-swap B1|B2|B3`
- `--scenario t1_low|t1_medium|t1_high|t2_spec_drift|t3_recovery`
- `--seed <int>`
- `--fault <fault_name>`
- `--slot-ablation <slot_id>`
- `--b2-ttl-sec <float>`

`B2` sensitivity slice는 `2 / 5 / 10`초를 그대로 쓰는 것을 권장합니다.

## 7. Live 실험 CLI

### Dry-run

먼저 output tree와 prompt/artifact 경로만 확인합니다.

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E2 \
  --scenario t1_low \
  --baseline B3 \
  --seed 11 \
  --output-root /abs/path/to/control-view/artifacts/experiments \
  --dry-run
```

### 실제 실행

`run_live_experiments.py`는 실험별 artifact copy를 만들고,
`run_gemini_headless_demo.sh`와 `live_fault_injector.py`를 묶어 실행합니다.
여기서 `B0/B1/B3` 모두 Gemini CLI MCP path를 사용하며, `B3`는 model-only control-view surface를 사용합니다.

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E4 \
  --scenario t3_recovery \
  --baseline B3 \
  --seed 31 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

지원 조합:

- `E2`: `t1_low`, `t1_medium`, `t1_high`
- `E4`: `t2_spec_drift`, `t3_recovery`
- baseline: `B0`, `B1`, `B3`

`B0`는 아래 env가 없으면 실행되지 않습니다.

```bash
export ROS_MCP_BASELINE_COMMAND='source /opt/ros/jazzy/setup.bash && ros-mcp-server ...'
```

## 8. Output Layout

live runner 기본 output root:

```text
artifacts/experiments/<stamp>/<experiment>/<scenario>/<baseline>/
```

그 아래에 아래 파일이 생깁니다.

- `summary.json`
- `effective_prompt.md`
- `scenario.yaml`
- `fault_events.jsonl`
- `control_artifacts/geofence.yaml`
- `control_artifacts/mission_spec.yaml`
- `replay/gemini.jsonl`
- `replay/observer.jsonl`
- `logs/gemini.jsonl`
- `metrics/summary.json`

이 경로가 공식 정리 단위입니다.

## 9. Baseline별 차이

### `B3`

- full `Control View`
- validity governor
- obligation / lease / commit guard 포함

### `B1`

- high-level family API는 같음
- transcript/session-summary memory baseline
- replay에서는 current decision snapshot만 사용

### `B2`

- live baseline이 아니라 replay-only baseline
- flat cache
- global TTL
- last-writer-wins
- invalidation / obligation / revision-aware commit guard 없음

### `B0`

- raw `ros-mcp-server`
- transcript/session-summary memory
- replay 비교 지표는 비어 있거나 0일 수 있음

## 10. 자주 막히는 지점

### `official_trace_ready=false`

원인:

- 예전 trace
- `decision_context` 없는 수동 JSONL

조치:

- `./scripts/run_sitl_smoke.sh ...`로 trace 재생성

### `gemini` 명령 없음

```bash
gemini --version
```

여기서 실패하면 Gemini CLI부터 다시 설치해야 합니다.

### spec drift가 반영되지 않음

현재 live runner는 각 실험 디렉토리의 `control_artifacts/` 복사본을 sidecar가 읽게 합니다.
직접 수동 실행할 때 spec drift를 시험하려면 아래 env를 같이 넣어야 합니다.

```bash
export CONTROL_VIEW_ARTIFACTS_DIR=/abs/path/to/isolated_artifacts
```
