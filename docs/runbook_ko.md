# Control View Sidecar 사용법 / 실행 가이드

이 문서는 현재 저장소의 코드 사용법, 로컬 개발 방식, Ubuntu 24.04 + ROS 2 Jazzy + PX4 SITL 실행 절차를 한 번에 따라갈 수 있게 정리한 runbook입니다.

## 1. 로컬 Mac 개발

### 환경 준비

```bash
cd /Users/jimin/Code/HUSL/control-view
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[dev]'
```

### 테스트 실행

```bash
pytest
ruff check src tests
python -m control_view.app
```

### Python API 예시

```python
from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.service import ControlViewService

root = Path("/Users/jimin/Code/HUSL/control-view")
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
backend.set_slot("offboard.stream.ok", {"value": True, "publish_rate_hz": 20.0})
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
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### ROS 2 Jazzy / PX4 SITL

1. ROS 2 Jazzy와 MAVROS를 Ubuntu 24.04에 설치합니다.
2. PX4 SITL을 `gz_x500` 기준으로 실행합니다.

```bash
make px4_sitl gz_x500
```

3. MAVROS bridge와 필요한 topics/services가 살아 있는지 확인합니다.
4. `configs/backend_mavros.yaml` 기준으로 sidecar를 실행합니다.

### sidecar 실행

현재 저장소의 엔트리포인트는 `control-view-sidecar`입니다.

```bash
control-view-sidecar
```

실제 MCP stdio 서버 실행은 이후 `FastMCP` runner 연결만 추가하면 되도록 구조를 잡아 둔 상태입니다. 현재는 service/runtime 자체를 검증하는 데 초점을 두고 있습니다.

### `ros-mcp-server` debug path

- normal mode: sidecar만 LLM에 연결
- debug mode: sidecar + `ros-mcp-server`를 read-only inspection용으로 병행
- critical control truth는 sidecar snapshot이므로, raw ROS tool output을 primary memory로 사용하지 않습니다

## 3. 현재 구현 범위

- 완료
  - field ontology 16개
  - family contracts 6개
  - loader/compiler/validation
  - SQLite state store
  - fake backend
  - materializer
  - governor
  - canonical args
  - lease
  - guarded executor
  - obligation open/close
  - replay skeleton

- 현재 제한
  - `MavrosBackend`는 Ubuntu 런타임 연결용 skeleton입니다
  - `FastMCP` tool 실행 경로는 코드 골격까지 완료됐고, stdio server wiring은 Ubuntu 검증 단계에서 마감 예정입니다
  - replay oracle은 rule-based skeleton입니다

