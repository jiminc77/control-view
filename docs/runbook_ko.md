# Control View Sidecar 사용법 / 실행 가이드

이 문서는 현재 저장소의 로컬 개발 방식, Ubuntu 24.04 + ROS 2 Jazzy + PX4 SITL 실행 절차,
baseline별 Gemini headless 실행 경로를 한 번에 정리한 runbook입니다.

## 1. 로컬 개발 / smoke

### 환경 준비

```bash
cd /path/to/control-view
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
```

### 테스트 실행

```bash
uv run pytest
uv run ruff check src tests scripts/*.py
uv run python -m control_view.app --backend fake --dry-run
uv run control-view-observer --help
bash -n scripts/*.sh
```

## 2. Python API 예시

```python
from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.service import ControlViewService

root = Path("/path/to/control-view")
backend = FakeBackend()
backend.set_slot("vehicle.connected", True)
backend.set_slot("vehicle.mode", "MANUAL")
backend.set_slot("failsafe.state", {"active": False})

service = ControlViewService(root, backend=backend)
view = service.get_control_view("ARM")
print(view.verdict)
print(view.lease_token)
```

## 3. Ubuntu 24.04 + ROS 2 Jazzy + PX4 SITL

### 저장소 가져오기

```bash
git clone https://github.com/jiminc77/control-view.git
cd control-view
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
```

### PX4 SITL / MAVROS

1. ROS 2 Jazzy와 MAVROS를 Ubuntu 24.04에 설치합니다.
2. PX4 SITL을 `gz_x500` 기준으로 실행합니다.

```bash
HEADLESS=1 make px4_sitl gz_x500
```

headless SITL에서는 PX4 shell에서 아래를 한 번 적용하면 datalink-loss auto-disarm을 피하기 쉽습니다.

```text
param set NAV_DLL_ACT 0
```

3. MAVROS bridge를 실행합니다.

```bash
source /opt/ros/jazzy/setup.bash
ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557
```

4. 필요한 topic/service가 살아 있는지 확인합니다.

```bash
source /opt/ros/jazzy/setup.bash
ros2 topic echo /mavros/state --once
ros2 topic echo /mavros/local_position/odom --once
```

## 4. Sidecar / Thin Surface / Observer 실행

### `B3` full sidecar

```bash
uv run control-view-sidecar \
  --root "$(pwd)" \
  --backend mavros \
  --tool-surface full \
  --baseline-policy B3
```

### `B1` thin transcript surface

```bash
uv run control-view-sidecar \
  --root "$(pwd)" \
  --backend mavros \
  --tool-surface thin \
  --baseline-policy B1
```

### Observer

```bash
uv run control-view-observer \
  --mission goto_hold_land \
  --output-jsonl artifacts/replay/observer_manual.jsonl \
  --stop-when-complete
```

기동 전 smoke는 다음으로 확인합니다.

```bash
uv run python -m control_view.app --root "$(pwd)" --backend mavros --dry-run
uv run python -m control_view.app \
  --root "$(pwd)" \
  --backend mavros \
  --tool-surface thin \
  --baseline-policy B1 \
  --record-jsonl artifacts/replay/manual_session_b1.jsonl
```

## 5. one-command SITL smoke

저장소 루트에서 아래를 실행하면 PX4 SITL, MAVROS, sidecar dry-run, mission runner를 순서대로 기동합니다.

```bash
./scripts/run_sitl_smoke.sh
```

특정 mission만 돌릴 수도 있습니다.

```bash
./scripts/run_sitl_smoke.sh takeoff_hold_land
./scripts/run_sitl_smoke.sh goto_hold_land
./scripts/run_sitl_smoke.sh goto_rtl
```

산출물 경로:

- `artifacts/replay/<mission>.jsonl`
- `artifacts/metrics/<mission>.json`
- `artifacts/logs/px4_sitl.log`
- `artifacts/logs/mavros.log`
- `artifacts/logs/<mission>.log`

## 6. Replay 실험 실행

nominal trace를 만든 뒤 replay/fault 실험은 아래 CLI로 재현합니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/goto_hold_land_offboard_stream_loss.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_offboard_stream_loss_counterexamples.jsonl
```

slot ablation, baseline 비교, budget 조건은 플래그만 바꾸면 됩니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B2 \
  --slot-ablation pose.local \
  --token-budget 4000 \
  --time-budget-ms 15000 \
  --output artifacts/metrics/goto_hold_land_b2_pose_local.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_b2_pose_local_counterexamples.jsonl
```

## 7. Gemini headless baseline 실행

### `B3`

```bash
BASELINE=B3 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

### `B1`

```bash
BASELINE=B1 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

### `B0`

```bash
export ROS_MCP_BASELINE_COMMAND='source /opt/ros/jazzy/setup.bash && ros-mcp-server ...'
BASELINE=B0 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

headless script는 baseline에 맞는 MCP surface를 선택하고 observer를 함께 띄운 뒤
`artifacts/metrics/gemini_<baseline>_<mission>_<stamp>.json`를 생성합니다.

## 8. MCP config 파일

- `configs/gemini_mcp_b0.json`: raw `ros-mcp-server`
- `configs/gemini_mcp_b1.json`: thin transcript surface
- `configs/gemini_mcp_b3.json`: full `Control View`
- `configs/gemini_mcp.json`: 현재 기본 full config
- `configs/gemini_mcp_debug.json`: full sidecar + read-only raw debug path

## 9. 현재 구현 범위

- 완료
- field ontology 16개
- family contracts 6개
- loader/compiler/validation
- SQLite state store
- fake backend
- live `MavrosBackend`
- materializer + contextual slot derivation
- governor + canonical args + lease
- guarded executor + pre-dispatch abort persistence
- obligation open/close + confirm/fail/expire transition
- full MCP tool surface
- thin transcript MCP tool surface
- observer ROS node + summary JSONL
- replay / fault / oracle / metrics surface
- terminal action transition / obligation transition / mission boundary recorder
- `scripts/run_replay_experiments.py`
- `scripts/run_gemini_headless_demo.sh`
- `scripts/export_gemini_metrics.py`

- 현재 제한
- `failsafe.state`는 여전히 heuristic slot입니다
- replay oracle은 rule-based baseline입니다
- observer scoring은 현재 3개 nominal mission spec에 맞춰져 있습니다
- `B2`는 replay-only baseline입니다
- `B0` 실행에는 사용자가 raw `ros-mcp-server` launch command를 제공해야 합니다
- Gemini headless demo는 로컬에 `gemini` CLI가 설치되어 있어야 합니다
