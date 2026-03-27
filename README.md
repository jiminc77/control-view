# Control View Sidecar

`Control View`는 PX4 + MAVROS 기반 드론 supervisory control을 위해 transcript 대신
family-specific typed state를 유지하는 sidecar MCP server입니다. 현재 저장소는 runtime
자체뿐 아니라 replay baseline, Gemini headless baseline, observer-based 공통 채점까지
같이 다룹니다.

핵심 목표는 다음입니다.

- family contract와 field ontology를 YAML로 관리
- runtime에서 현재 `Control View`를 materialize
- validity governor, lease, obligation semantics로 guarded execution 수행
- Gemini CLI 같은 LLM client에 baseline별 MCP surface 제공
- PX4 SITL + Gazebo + ROS 2 Jazzy 환경에서 replay / live validation / observer scoring 지원

## 현재 상태

- contracts / compiler / validation / runtime / guarded executor / obligation lifecycle 구현 완료
- `MavrosBackend`는 ROS 2 Jazzy live adapter로 연결되며 preview warmup과 pre-dispatch abort persistence를 포함
- replay harness는 `B1/B2/B3` baseline remap, slot ablation, oracle labels, fault injection을 지원
- `B0/B1/B3` Gemini baseline은 headless script와 MCP config로 실행 가능
- `B2`는 live Gemini baseline이 아니라 replay-only structured-cache baseline으로 유지
- observer node가 `B0/B1/B3`와 무관하게 동일한 ROS topic stream을 관찰해 mission success와 recovery를 기록
- `ReplayRecorder`는 decision request/result, action transition, obligation transition, mission boundary, observer event/summary를 JSONL로 기록
- 2026-03-26 live SITL 검증 기준 `gz_x500 + MAVROS + sidecar`에서 아래 nominal missions를 end-to-end `CONFIRMED`까지 재현
- `ARM -> TAKEOFF -> HOLD -> LAND`
- `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
- `ARM -> TAKEOFF -> GOTO -> RTL`

## 빠른 시작

```bash
uv venv .venv --python 3.12 --system-site-packages
source .venv/bin/activate
uv sync --extra dev
uv run pytest
uv run ruff check src tests scripts/*.py
uv run python -m control_view.app --backend fake --dry-run
uv run control-view-observer --help
bash -n scripts/*.sh
```

`--system-site-packages`를 유지하는 이유는 ROS 2 Jazzy가 시스템 Python에 설치한
`rclpy`, `mavros_msgs` 등을 그대로 재사용해야 하기 때문입니다.

## Baselines

- `B0`: raw `ros-mcp-server` + transcript/session-summary memory
- `B1`: thin high-level family API + transcript/session-summary memory
- `B2`: replay-only simple structured-cache baseline
- `B3`: full `Control View` system

## 문서

- `docs/runbook_ko.md`: 로컬 개발, sidecar/observer 실행, SITL 절차
- `docs/experiments_ko.md`: `E1~E4` 재현 절차, output layout, 결과 표 정리 방식
- `docs/gemini_demo_prompt_ko.md`: `B3` headless Gemini prompt
- `docs/gemini_demo_prompt_b1_ko.md`: `B1` headless Gemini prompt
- `docs/gemini_demo_prompt_b0_ko.md`: `B0` headless Gemini prompt

## 자동화 스크립트

```bash
./scripts/run_sitl_smoke.sh
uv run python scripts/run_replay_experiments.py \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --scenario t3_recovery \
  --seed 31 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/goto_hold_land_offboard_stream_loss.json \
  --counterexamples-jsonl artifacts/replay/goto_hold_land_offboard_stream_loss_counterexamples.jsonl
uv run python scripts/run_live_experiments.py \
  --experiment E4 \
  --scenario t3_recovery \
  --baseline B3 \
  --seed 31
BASELINE=B1 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

- `run_sitl_smoke.sh`는 PX4 SITL, MAVROS, sidecar dry-run, nominal mission runner를 한 번에 실행합니다.
- `run_mission.py`는 mission별 replay JSONL과 metrics summary를 `artifacts/` 아래에 남깁니다.
- `run_replay_experiments.py`는 recorded replay에 policy swap, fault injection, slot ablation, budget 조건을 적용하고 `official_trace_ready`를 함께 표시합니다.
- `run_live_experiments.py`는 `artifacts/experiments/<stamp>/<experiment>/<scenario>/<baseline>/` 아래에 live 결과를 정리합니다.
- `run_gemini_headless_demo.sh`는 `B0/B1/B3` baseline 중 하나를 선택해 Gemini session과 observer를 함께 실행하고 metrics JSON을 생성합니다.
