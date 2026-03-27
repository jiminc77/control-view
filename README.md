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
- SQLite store, artifact revision, lease, guarded executor, obligation lifecycle 완료
- FastMCP tool surface와 `control-view-sidecar` 엔트리포인트 연결 완료
- `MavrosBackend`는 ROS 2 Jazzy용 live adapter로 연결되며, startup wait, QoS 정합성, preview warmup, pre-dispatch abort persistence를 포함
- replay / fault / oracle / metrics는 slot ablation, `B2/B3/B4` policy swap, stale-commit 집계까지 포함해 확장
- `ReplayRecorder`는 decision request/result뿐 아니라 normalized event, action transition, obligation transition, mission boundary까지 JSONL로 기록
- metrics는 mission-level success, terminal action transition, weak-ack-without-confirm을 terminal state 기준으로 계산
- 2026-03-26 live SITL 검증 기준 `gz_x500 + MAVROS + sidecar`에서 아래 nominal missions를 end-to-end `CONFIRMED`까지 재현
  - `ARM -> TAKEOFF -> HOLD -> LAND`
  - `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
  - `ARM -> TAKEOFF -> GOTO -> RTL`
- Gemini normal-mode / debug-mode용 MCP config와 headless demo script를 저장소 내부에 포함

## 빠른 시작

```bash
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
uv run pytest
uv run ruff check src tests scripts/*.py
uv run python -m control_view.app --backend fake --dry-run
bash -n scripts/*.sh
```

실제 ROS 2 Jazzy / PX4 SITL 실행 절차는 `docs/runbook_ko.md`에 정리합니다.

`--system-site-packages`를 유지하는 이유는 ROS 2 Jazzy가 시스템 Python에 설치한
`rclpy`, `mavros_msgs` 등을 그대로 재사용해야 하기 때문입니다.

## 문서

- `docs/runbook_ko.md`: 코드 사용법, 로컬 개발, Ubuntu 실행 절차
- `docs/experiments_ko.md`: replay, fault injection, metrics, 실험 산출물 정리
- `docs/gemini_demo_prompt_ko.md`: headless Gemini demo prompt

## 자동화 스크립트

```bash
./scripts/run_sitl_smoke.sh
uv run python scripts/run_replay_experiments.py \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/goto_hold_land_offboard_stream_loss.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_offboard_stream_loss_counterexamples.jsonl
./scripts/run_gemini_headless_demo.sh goto_hold_land
uv run python scripts/export_gemini_metrics.py \
  --replay-jsonl artifacts/replay/gemini_goto_hold_land_*.jsonl \
  --gemini-log artifacts/logs/gemini_goto_hold_land_*.jsonl \
  --output artifacts/metrics/gemini_goto_hold_land.json
```

- `run_sitl_smoke.sh`는 PX4 SITL, MAVROS, sidecar dry-run, nominal mission runner를 한 번에 실행합니다.
- `run_mission.py`는 mission별 replay JSONL과 metrics summary를 `artifacts/` 아래에 남기며 mission boundary와 terminal transition을 함께 기록합니다.
- `run_replay_experiments.py`는 recorded replay에 policy swap, fault injection, slot ablation을 적용하고 metrics/counterexample JSON을 생성합니다.
- `run_gemini_headless_demo.sh`는 sidecar-only Gemini session을 실행하고 Gemini JSONL log를 metrics JSON으로 변환합니다.
