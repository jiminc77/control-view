# Control View 실험 가이드

이 문서는 현재 구현체로 논문 계획서의 E1~E4를 실제로 재현하는 절차서입니다.
핵심 원칙은 다음 두 가지입니다.

- nominal mission은 먼저 live SITL에서 새 replay JSONL로 다시 생성합니다.
- replay 실험은 `scripts/run_replay_experiments.py`만 사용합니다.

예전 JSONL 중 `mission_boundary`, terminal `action_transition`, `obligation_transition`이 없는 파일은
`mission_success_rate`와 `weak_ack_without_confirm_rate`를 올바르게 계산하지 못하므로 재사용하지 않습니다.

## 1. 권장 mission 세트

- `takeoff_hold_land`
- `goto_hold_land`
- `goto_rtl`

2026-03-26 live SITL 검증 기준 현재 재현 상태:

- `takeoff_hold_land`: `CONFIRMED`
- `goto_hold_land`: `CONFIRMED`
- `goto_rtl`: `CONFIRMED`

## 2. 먼저 nominal trace 생성

저장소 루트에서 아래를 실행합니다.

```bash
./scripts/run_sitl_smoke.sh takeoff_hold_land goto_hold_land goto_rtl
```

이 명령은 다음을 자동으로 수행합니다.

- PX4 SITL `gz_x500` 기동
- MAVROS bridge 기동
- sidecar dry-run 확인
- mission runner 실행
- replay / metrics / log artifact 저장

생성 결과:

- `artifacts/replay/takeoff_hold_land.jsonl`
- `artifacts/replay/goto_hold_land.jsonl`
- `artifacts/replay/goto_rtl.jsonl`
- `artifacts/metrics/<mission>.json`
- `artifacts/logs/px4_sitl.log`
- `artifacts/logs/mavros.log`
- `artifacts/logs/<mission>.log`

## 3. replay 실험 공통 형식

모든 replay 실험은 아래 형식을 따릅니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/<mission>.jsonl \
  --policy-swap <B2|B3|B4> \
  --fault <fault_name> \
  --slot-ablation <slot_id> \
  --output artifacts/metrics/<name>.json \
  --counterexamples-jsonl artifacts/replay/<name>_counterexamples.jsonl
```

설명:

- `--policy-swap`
  - `B2`: decision-only baseline
  - `B3`: TTL freshness만 유지하고 invalidator / commit guard / pending transition gating 제거
  - `B4`: full system
- `--fault`는 생략 가능
- `--slot-ablation`은 여러 번 줄 수 있음
- `--fault-param key=value`로 fault parameter override 가능

## 4. E1. Sufficiency / Relative Minimality

목적:

- full `Control View`가 oracle decision을 얼마나 보존하는지 본다.
- 특정 slot 제거가 실제 decision mismatch를 만들면 relative minimality evidence로 쓴다.

권장 실행:

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
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

확인할 것:

- `interface_mismatch_rate`
- counterexample JSONL에 기록된 `verdict != oracle_verdict`

## 5. E2. Freshness / Authority Governance

목적:

- stale / invalidated / weak-ack 계열 fault에서 `B2`, `B3`, `B4` 차이를 본다.

권장 fault:

- `stale_pose`
- `estimator_reset_event`
- `vehicle_reconnect`
- `ack_without_confirm`
- `offboard_warmup_failure`
- `offboard_stream_loss`
- `no_progress_during_goto`
- `stale_transform`

예시 1: stale pose

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
  --fault stale_pose \
  --fault-param stale_ms=1200 \
  --output artifacts/metrics/e2_goto_hold_land_stale_pose_b4.json \
  --counterexamples-jsonl artifacts/replay/e2_goto_hold_land_stale_pose_b4_counterexamples.jsonl
```

예시 2: full system vs TTL-only 비교

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/e2_goto_hold_land_offboard_stream_loss_b3.json \
  --counterexamples-jsonl artifacts/replay/e2_goto_hold_land_offboard_stream_loss_b3_counterexamples.jsonl
```

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
  --fault offboard_stream_loss \
  --output artifacts/metrics/e2_goto_hold_land_offboard_stream_loss_b4.json \
  --counterexamples-jsonl artifacts/replay/e2_goto_hold_land_offboard_stream_loss_b4_counterexamples.jsonl
```

확인할 것:

- `unsafe_act_rate`
- `false_refuse_rate`
- `unnecessary_refresh_rate`
- `stale_commit_abort_rate`
- `weak_ack_without_confirm_rate`

## 6. E3. Efficiency vs Transcript Memory

목적:

- sidecar-only Gemini run에서 prompt token과 latency를 기록한다.

실행:

```bash
./scripts/run_gemini_headless_demo.sh goto_hold_land
```

이후 Gemini JSONL과 replay JSONL을 합쳐 metrics를 만든다.

```bash
uv run python scripts/export_gemini_metrics.py \
  --replay-jsonl artifacts/replay/gemini_goto_hold_land_YYYYMMDD_HHMMSS.jsonl \
  --gemini-log artifacts/logs/gemini_goto_hold_land_YYYYMMDD_HHMMSS.jsonl \
  --output artifacts/metrics/gemini_goto_hold_land.json
```

확인할 것:

- `prompt_tokens_per_turn`
- `decision_latency_ms`
- `mission_success_rate`

## 7. E4. Obligation-Mediated Composition

목적:

- pending transition이 premature next-step execution을 막는지 본다.

권장 조합:

- `goto_hold_land` + `ack_without_confirm`
- `goto_hold_land` + `offboard_stream_loss`
- `goto_hold_land` + `no_progress_during_goto`
- `goto_rtl` + `operator_mode_override`

예시:

```bash
uv run python scripts/run_replay_experiments.py \
  --root "$(pwd)" \
  --replay-jsonl artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B4 \
  --fault no_progress_during_goto \
  --output artifacts/metrics/e4_goto_hold_land_no_progress.json \
  --counterexamples-jsonl artifacts/replay/e4_goto_hold_land_no_progress_counterexamples.jsonl
```

확인할 것:

- `action_transition`이 `CONFIRMED`에서 `EXPIRED` 또는 `FAILED`로 바뀌는지
- 관련 `obligation_transition`이 `OPEN`으로 남지 않고 terminal로 닫히는지
- mission end boundary가 `success: false`로 바뀌는지

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

현재 구현에서 실제로 영향을 주는 fault와 범위:

- slot validity 변경
  - `stale_pose`
  - `estimator_reset_event`
  - `vehicle_reconnect`
  - `geofence_revision_update`
  - `offboard_stream_loss`
  - `stale_transform`
- action / obligation terminal state 변경
  - `ack_without_confirm`
  - `offboard_warmup_failure`
  - `offboard_stream_loss`
  - `no_progress_during_goto`
  - `operator_mode_override`
- artifact/support field 변경
  - `tool_registry_revision_bump`
  - `battery_reserve_drop`

## 9. Metrics 의미

- `mission_success_rate`
  - mission boundary 기준 success 비율
  - boundary가 없으면 terminal action과 obligation state로 fallback 계산
- `weak_ack_without_confirm_rate`
  - weak ack action 중 최종 terminal state가 `CONFIRMED`가 아닌 비율
- `stale_commit_abort_rate`
  - terminal action 중 `critical_slot_revision_changed:*`로 abort된 비율
- `interface_mismatch_rate`
  - replay output verdict와 oracle verdict 불일치 비율

## 10. 실험 때 반드시 보관할 것

- `artifacts/replay/*.jsonl`
- `artifacts/replay/*_counterexamples.jsonl`
- `artifacts/metrics/*.json`
- `artifacts/logs/*.log`
- 사용한 `configs/*.yaml`
- 사용한 `configs/gemini_mcp*.json`
- sidecar git commit hash
- PX4 git commit hash
- `control_view.get` / `action.execute_guarded` / `ledger.tail` JSON 샘플
