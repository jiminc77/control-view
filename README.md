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

- contracts / compiler / validation 완료
- SQLite store, lease, guarded executor, obligation skeleton 완료
- FastMCP tool surface와 `control-view-sidecar` 엔트리포인트 연결 완료
- `MavrosBackend`는 ROS 2 Jazzy용 live adapter로 연결되며, fake backend 테스트 경로도 유지
- replay/fault/metrics는 논문화용 최소 surface까지 구현
- 남은 검증의 중심은 Ubuntu 24.04 + ROS 2 Jazzy + PX4 SITL 실환경 smoke/mission 확인

## 빠른 시작

```bash
python3.12 -m venv .venv --system-site-packages
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check src tests
python -m control_view.app --backend fake --dry-run
```

실제 ROS 2 Jazzy / PX4 SITL 실행 절차는 `docs/runbook_ko.md`에 정리합니다.

## 문서

- `docs/runbook_ko.md`: 코드 사용법, 로컬 개발, Ubuntu 실행 절차
- `docs/experiments_ko.md`: replay, fault injection, metrics, 실험 산출물 정리
