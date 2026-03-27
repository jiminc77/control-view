# Control View Sidecar 사용법 / 실행 가이드

이 문서는 현재 저장소의 코드 사용법, 로컬 개발 방식, Ubuntu 24.04 + ROS 2 Jazzy + PX4 SITL 실행 절차를 한 번에 따라갈 수 있게 정리한 runbook입니다.

## 1. 로컬 개발 / 코드 smoke

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
bash -n scripts/*.sh
```

### Python API 예시

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

### `GOTO` 예시

```python
backend.set_slot("vehicle.armed", True)
backend.set_slot(
    "pose.local",
    {
        "position": {"x": 0.0, "y": 0.0, "z": 2.0},
        "frame_id": "map",
        "child_frame_id": "base_link",
    },
    frame_id="map",
)
backend.set_slot("estimator.health", {"score": 0.99})
backend.set_slot("geofence.status", {"target_inside": True, "artifact_revision": 1})
backend.set_slot("failsafe.state", {"active": False})
backend.set_slot("vehicle.mode", "POSCTL")
backend.set_slot("nav.progress", {"phase": "IN_PROGRESS", "distance_m": 5.0, "speed_mps": 0.1})

view = service.get_control_view(
    "GOTO",
    {"target_pose": {"position": {"x": 1.0, "y": 2.0, "z": 3.0}, "frame_id": "map"}},
)
result = service.execute_guarded("GOTO", view.canonical_args, view.lease_token)
print(result.status)
```

## 2. Ubuntu 24.04 + ROS 2 Jazzy 실행 절차

### 저장소 가져오기

```bash
git clone https://github.com/jiminc77/control-view.git
cd control-view
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
```

### ROS 2 Jazzy / PX4 SITL

1. ROS 2 Jazzy와 MAVROS를 Ubuntu 24.04에 설치합니다.
2. PX4 SITL을 `gz_x500` 기준으로 실행합니다.

```bash
HEADLESS=1 make px4_sitl gz_x500
```

headless SITL에서는 PX4 shell에서 다음을 한 번 적용하면 datalink-loss auto-disarm을 피할 수 있습니다.

```text
param set NAV_DLL_ACT 0
```

3. MAVROS bridge를 실행합니다.

```bash
source /opt/ros/jazzy/setup.bash
ros2 launch mavros px4.launch fcu_url:=udp://:14540@127.0.0.1:14557
```

4. 필요한 topics/services가 살아 있는지 확인합니다.

```bash
source /opt/ros/jazzy/setup.bash
ros2 topic echo /mavros/state --once
ros2 topic echo /mavros/global_position/global --once
```

5. `configs/backend_mavros.yaml` 기준으로 sidecar를 실행합니다.

### sidecar 실행

현재 저장소의 엔트리포인트는 `control-view-sidecar`이며, FastMCP stdio 서버로 동작합니다.

```bash
control-view-sidecar --root "$(pwd)" --backend mavros
```

기동 전 smoke는 다음으로 확인합니다.

```bash
uv run python -m control_view.app --root "$(pwd)" --backend mavros --dry-run
uv run python -m control_view.app \
  --root "$(pwd)" \
  --backend mavros \
  --record-jsonl artifacts/replay/manual_session.jsonl
```

### one-command SITL smoke

저장소 루트에서 아래 스크립트를 실행하면 PX4 SITL, MAVROS, sidecar dry-run, mission runner를 순서대로 기동합니다.

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

### replay 실험 실행

nominal mission trace를 만든 뒤 replay/fault 실험은 아래 CLI로 재현합니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/goto_hold_land_offboard_stream_loss.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_offboard_stream_loss_counterexamples.jsonl
```

slot ablation이나 baseline 비교는 플래그만 바꾸면 됩니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --slot-ablation pose.local \
  --output artifacts/metrics/goto_hold_land_b3_pose_local.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_b3_pose_local_counterexamples.jsonl
```

주의:

- `mission_success_rate`는 mission-level 지표입니다. mission 종료 boundary와 terminal action transition이 없는 예전 replay JSONL은 재생성해서 사용해야 합니다.
- `weak_ack_without_confirm_rate`는 weak ack action의 최종 terminal state가 `CONFIRMED`가 아니면 증가합니다.

### `ros-mcp-server` debug path

- normal mode: sidecar만 LLM에 연결
- debug mode: sidecar + `ros-mcp-server`를 read-only inspection용으로 병행
- critical control truth는 sidecar snapshot이므로, raw ROS tool output을 primary memory로 사용하지 않습니다

## 3. 2026-03-26 live SITL 검증 결과

- Ubuntu 24.04 + ROS 2 Jazzy + PX4 `gz_x500` + MAVROS 조합에서 저장소 내부 smoke harness로 검증했습니다.
- `ARM`: `ACKED_STRONG -> CONFIRMED`
- `TAKEOFF(target_altitude=3.0)`: `ACKED_STRONG -> CONFIRMED`
- `HOLD`: `ACKED_WEAK -> CONFIRMED`
- `GOTO(target_pose.map=(x+2.0, y, z>=3.0))`: `ACKED_WEAK -> CONFIRMED`
- `RTL`: `ACKED_WEAK -> CONFIRMED`
- `LAND`: `ACKED_WEAK -> CONFIRMED`
- nominal mission 3종 재현 완료
  - `ARM -> TAKEOFF -> HOLD -> LAND`
  - `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
  - `ARM -> TAKEOFF -> GOTO -> RTL`

## 4. 현재 구현 범위

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
  - FastMCP tool surface + stdio app entrypoint
  - replay / fault / oracle / metrics surface
  - terminal action transition / obligation transition / mission boundary recorder
  - `scripts/run_replay_experiments.py` replay experiment CLI
  - live SITL nominal mission 3종 confirmation
  - `scripts/run_sitl_smoke.sh` SITL harness
  - `scripts/run_gemini_headless_demo.sh` / `scripts/export_gemini_metrics.py`
  - `configs/gemini_mcp.json` / `configs/gemini_mcp_debug.json`

- 현재 제한
  - `failsafe.state`는 여전히 heuristic slot입니다
  - replay oracle은 rule-based baseline이며 learned/annotated decision oracle은 아닙니다
  - `ros-mcp-server` debug profile은 read-only launch command를 사용자가 `ROS_MCP_DEBUG_COMMAND`로 지정해야 합니다
  - Gemini headless demo는 로컬에 `gemini` CLI가 설치되어 있어야 합니다
