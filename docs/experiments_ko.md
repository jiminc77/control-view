# Control View 실험 가이드

이 문서는 현재 구현체로 `implementation_plan.md`의 baseline과 실험 구조를 재현하는 절차서입니다.
현재 저장소는 다음 원칙으로 정렬되어 있습니다.

- `B0/B1/B3`는 live Gemini baseline입니다.
- `B2`는 replay-only structured-cache baseline입니다.
- live baseline의 공통 채점은 MCP surface가 아니라 observer ROS node가 담당합니다.
- replay 비교는 `scripts/run_replay_experiments.py` 하나로 통일합니다.

## 1. Baseline 정의

- `B0`: raw `ros-mcp-server` + transcript/session-summary memory
- `B1`: thin high-level family API + transcript/session-summary memory
- `B2`: simple structured cache + fixed TTL, replay-only
- `B3`: full `Control View` + validity governor + action-state semantics + lease/commit guard + obligations

## 2. 권장 mission 세트

- `takeoff_hold_land`
- `goto_hold_land`
- `goto_rtl`

2026-03-26 live SITL 검증 기준 현재 재현 상태:

- `takeoff_hold_land`: `CONFIRMED`
- `goto_hold_land`: `CONFIRMED`
- `goto_rtl`: `CONFIRMED`

## 3. Observer 기반 공통 산출물

`B0/B1/B3` live run은 항상 observer를 같이 돌립니다. headless script를 쓰면 다음 artifact가 자동 생성됩니다.

- `artifacts/replay/observer_<baseline>_<mission>_<stamp>.jsonl`
- `artifacts/logs/gemini_<baseline>_<mission>_<stamp>.jsonl`
- `artifacts/metrics/gemini_<baseline>_<mission>_<stamp>.json`

observer는 현재 아래 물리 현상을 기록합니다.

- connected / mode transition
- airborne
- excursion reached
- arrival
- hold stable
- RTL entered
- touchdown
- fault detected / fault recovered
- mission summary

현재 observer mission scoring rule은 다음 고정 mission spec에 대해 제공됩니다.

- `takeoff_hold_land`: airborne + stable hold + touchdown
- `goto_hold_land`: excursion `>= 1.5m` + arrival + hold + touchdown
- `goto_rtl`: excursion `>= 1.5m` + arrival + RTL + touchdown

## 4. 먼저 nominal trace 생성

저장소 루트에서 아래를 실행합니다.

```bash
./scripts/run_sitl_smoke.sh takeoff_hold_land goto_hold_land goto_rtl
```

생성 결과:

- `artifacts/replay/<mission>.jsonl`
- `artifacts/metrics/<mission>.json`
- `artifacts/logs/px4_sitl.log`
- `artifacts/logs/mavros.log`
- `artifacts/logs/<mission>.log`

## 5. Replay 실험

replay 실험은 아래 형식을 따릅니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/<mission>.jsonl \
  --policy-swap <B1|B2|B3> \
  --fault <fault_name> \
  --slot-ablation <slot_id> \
  --token-budget <tokens> \
  --time-budget-ms <ms> \
  --output artifacts/metrics/<name>.json \
  --counterexamples-jsonl artifacts/replay/<name>_counterexamples.jsonl
```

현재 `--policy-swap` 의미:

- `B1`: no-governor transcript-like baseline
- `B2`: TTL-only structured-cache baseline
- `B3`: full system

### E1. Sufficiency / Relative Minimality

권장 실행:

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --slot-ablation pose.local \
  --output artifacts/metrics/e1_goto_hold_land_ablate_pose_local.json \
  --counterexamples-jsonl artifacts/replay/e1_goto_hold_land_ablate_pose_local_counterexamples.jsonl
```

추가로 반복할 slot:

- `vehicle.connected`
- `vehicle.armed`
- `estimator.health`
- `offboard.stream.ok`
- `geofence.status`
- `home.ready`

### E3. Memory Governance & Robustness

권장 fault:

- `stale_pose`
- `estimator_reset_event`
- `vehicle_reconnect`
- `ack_without_confirm`
- `offboard_warmup_failure`
- `offboard_stream_loss`
- `no_progress_during_goto`
- `stale_transform`

TTL-only와 full 비교 예시:

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B2 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/e3_goto_hold_land_offboard_stream_loss_b2.json \
  --counterexamples-jsonl artifacts/replay/e3_goto_hold_land_offboard_stream_loss_b2_counterexamples.jsonl
```

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/e3_goto_hold_land_offboard_stream_loss_b3.json \
  --counterexamples-jsonl artifacts/replay/e3_goto_hold_land_offboard_stream_loss_b3_counterexamples.jsonl
```

## 6. Live Gemini baseline 실행

### `B3` full sidecar

```bash
BASELINE=B3 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

### `B1` thin transcript baseline

```bash
BASELINE=B1 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

### `B0` raw baseline

```bash
export ROS_MCP_BASELINE_COMMAND='source /opt/ros/jazzy/setup.bash && ros-mcp-server ...'
BASELINE=B0 ./scripts/run_gemini_headless_demo.sh goto_hold_land
```

`run_gemini_headless_demo.sh`는 baseline에 따라 prompt와 MCP surface를 자동 선택합니다.

## 7. Metrics 의미

현재 구현에서 자주 보는 지표는 아래입니다.

- `interface_mismatch_rate`
- `unsafe_act_rate`
- `false_refuse_rate`
- `unnecessary_refresh_rate`
- `stale_action_rate`
- `premature_transition_rate`
- `obligation_closure_accuracy`
- `stale_commit_abort_rate`
- `weak_ack_without_confirm_rate`
- `mission_success_rate`
- `mission_success_under_token_budget`
- `mission_success_under_time_budget`
- `cumulative_prompt_tokens`
- `prompt_tokens_per_successful_control_decision`
- `compression_count`
- `decision_latency_ms`
- `recovery_success_rate`
- `fault_recovery_success_rate`
- `post_fault_token_spend`

주의:

- oracle mismatch 계열은 replay decision record가 있어야 계산됩니다.
- `mission_success_rate`, recovery 계열은 observer summary가 있으면 observer를 우선 사용합니다.
- `B0`에서는 replay-specific metrics가 비거나 0으로 남을 수 있습니다.

## 8. Fault 목록

- `pose_message_delay`
- `stale_pose`
- `estimator_reset_event`
- `vehicle_reconnect`
- `operator_mode_override`
- `geofence_revision_update`
- `tool_registry_revision_bump`
- `ack_without_confirm`
- `offboard_warmup_failure`
- `offboard_stream_loss`
- `no_progress_during_goto`
- `stale_transform`
- `battery_reserve_drop`

## 9. 실험 때 반드시 보관할 것

- `artifacts/replay/*.jsonl`
- `artifacts/replay/*_counterexamples.jsonl`
- `artifacts/metrics/*.json`
- `artifacts/logs/*.log`
- 사용한 `configs/*.yaml`
- 사용한 `configs/gemini_mcp*.json`
- sidecar git commit hash
- PX4 git commit hash
