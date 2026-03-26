# Control View Sidecar

`Control View`는 PX4 + MAVROS 기반 드론 supervisory control을 위해, transcript 대신
family-specific typed state만 유지하는 sidecar MCP server입니다.

현재 저장소는 다음 목표를 중심으로 구현됩니다.

- family contract와 field ontology를 YAML로 관리
- runtime에서 현재 `Control View`를 materialize
- validity governor, lease, obligation semantics를 통해 guarded execution 수행
- MCP tool surface로 Gemini CLI 같은 LLM client에 strict typed snapshot 제공
- PX4 SITL + Gazebo + ROS 2 Jazzy 환경에서 replay와 mission validation 지원

## 현재 상태

- M0: contracts / compiler / validation
- M1+: storage, materialization, governor, executor, replay harness 순차 구현

## 빠른 시작

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[dev]'
pytest
```

실제 ROS 2 Jazzy / PX4 SITL 실행 가이드는 문서가 더 준비되면 `docs/`에 정리합니다.

## 문서

- `docs/runbook_ko.md`: 코드 사용법, 로컬 개발, Ubuntu 실행 절차
- `docs/experiments_ko.md`: replay, fault injection, metrics, 실험 산출물 정리
