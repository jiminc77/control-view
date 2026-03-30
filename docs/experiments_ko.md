# Control View 실험 가이드

이 문서는 `implementation_plan.md`의 `E1~E4`를 현재 저장소에서 그대로 재현하는 절차서입니다.
핵심 원칙은 세 가지입니다.

- `B0/B1/B3`는 live Gemini baseline이다.
- `B2`는 replay-only baseline이다.
- 공식 결과는 `decision_context`가 들어간 새 trace와
  `artifacts/experiments/<stamp>/<experiment>/<scenario>/<baseline>/summary.json`
  기준으로 정리한다.

## 1. Baseline 정의

- `B0`: raw `ros-mcp-server` + transcript/session-summary memory
- `B1`: thin high-level family API + transcript/session-summary memory
- `B2`: flat structured cache + global TTL + last-writer-wins, replay-only
- `B3`: full `Control View` + validity governor + lease/commit guard + obligations

## 2. 공식 산출물 규칙

### Replay

replay 실행 결과 JSON에서 아래 값이 반드시 맞아야 합니다.

- `legacy_trace_count == 0`
- `official_trace_ready == true`

이 값이 아니면 그 결과는 참고용이고, 논문용 결과로 쓰면 안 됩니다.

### Live

live 결과는 아래 디렉토리 하나를 한 run의 기준 단위로 봅니다.

```text
artifacts/experiments/<stamp>/<experiment>/<scenario>/<baseline>/
```

이 디렉토리에서 최소 보관 파일:

- `summary.json`
- `metrics/summary.json`
- `replay/observer.jsonl`
- `fault_events.jsonl`
- `effective_prompt.md`
- `scenario.yaml`

공정 비교로 인정하는 live run 조건:

- `B0`는 Gemini 전역 설정의 real `ros-mcp-server`만 사용해야 한다.
- `B1`는 thin sidecar surface만 사용해야 한다.
- `B3`는 model-only `family.step` surface만 사용해야 한다.
- `family.execute/status/decide`가 섞인 구형 `B3` artifact와 raw-wrapper 기반 `B0` artifact는 공식 비교에서 제외한다.

## 3.5. 3-Seed 실험 수와 자동화

기본 seed는 `11,21,31`로 고정한다.

- `core` bundle: 총 `78` runs
- `core_plus_b2` bundle: 총 `84` runs

구성은 아래와 같다.

- `E1`: `9 x 3 = 27`
- `E2` main: `3 scenarios x 3 baselines x 3 seeds = 27`
- `E3` core: `6 x 3 = 18`
- `E4`: `2 x 1 x 3 = 6`
- `E2`의 `B2` 보조 replay 비교를 포함하면 `+6`, 총 `84`

자동화 entrypoint:

```bash
uv run python scripts/run_experiment_matrix.py --bundle core --phase all
uv run python scripts/run_experiment_matrix.py --bundle core_plus_b2 --phase all
```

지원 phase:

- `clean`
- `regen_traces`
- `replay_core`
- `live_e2`
- `live_e4`
- `aggregate`
- `all`

실행 후 `artifacts/aggregate/<stamp>/` 아래에 아래 파일이 생성된다.

- `manifest.json`
- `result.json`
- `failed_jobs.json`
- `retry_failed_jobs.sh`
- `live_summary.json`
- `live_summary.csv`
- `replay_summary.json`
- `replay_summary.csv`

## 4. 실험 전 공통 준비

1. `docs/runbook_ko.md`의 SITL + MAVROS + Python 환경 준비를 먼저 끝냅니다.
2. nominal trace를 다시 생성합니다.

```bash
cd /abs/path/to/control-view
source .venv/bin/activate
./scripts/run_sitl_smoke.sh takeoff_hold_land goto_hold_land goto_rtl
```

3. 새 trace인지 확인합니다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --output /tmp/goto_hold_land_check.json
```

확인 조건:

- `legacy_trace_count`가 `0`
- `official_trace_ready`가 `true`

## 5. E1. Sufficiency / Relative Minimality

### 목적

`B3`의 family-level decision interface가 유지되는지,
그리고 slot 제거가 실제로 `verdict`, `canonical_args`, `blockers`를 바꾸는지 본다.

### 입력

- 새 nominal trace
- replay baseline `B3`
- ablation 대상 slot

### 권장 slot

- `pose.local`
- `vehicle.connected`
- `vehicle.armed`
- `estimator.health`
- `offboard.stream.ok`
- `geofence.status`
- `home.ready`

### 실행 순서

1. unablated 기준 결과를 먼저 만든다.

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --scenario t1_low \
  --seed 11 \
  --output /abs/path/to/control-view/artifacts/metrics/e1_goto_hold_land_b3_nominal.json \
  --counterexamples-jsonl /abs/path/to/control-view/artifacts/replay/e1_goto_hold_land_b3_nominal_counterexamples.jsonl
```

2. slot별 ablation을 돌린다.

```bash
for slot in pose.local vehicle.connected vehicle.armed estimator.health offboard.stream.ok geofence.status; do
  uv run python scripts/run_replay_experiments.py \
    --root /abs/path/to/control-view \
    --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
    --policy-swap B3 \
    --scenario t1_low \
    --seed 11 \
    --slot-ablation "$slot" \
    --output "/abs/path/to/control-view/artifacts/metrics/e1_goto_hold_land_${slot}.json" \
    --counterexamples-jsonl "/abs/path/to/control-view/artifacts/replay/e1_goto_hold_land_${slot}_counterexamples.jsonl"
done
```

`RTL`까지 볼 때는 `goto_rtl.jsonl`에서 `home.ready`를 따로 돌립니다.

### 반드시 확인할 값

- `interface_mismatch_rate`
- `unsafe_accept_after_ablation`
- `canonical_arg_error_rate`
- `blocker_explanation_loss`
- `counterexample_count`
- `official_trace_ready`

### 결과 표 정리

한 줄을 `mission × slot`으로 둡니다.

권장 컬럼:

- `mission`
- `slot`
- `interface_mismatch_rate`
- `unsafe_accept_after_ablation`
- `canonical_arg_error_rate`
- `blocker_explanation_loss`
- `counterexample_count`
- `official_trace_ready`

### 해석

- unablated `B3`보다 mismatch가 명확히 올라가면 그 slot은 indispensable 후보입니다.
- `unsafe_accept_after_ablation > 0`이면 그 slot은 safety-critical입니다.
- `canonical_arg_error_rate > 0`이면 slot이 단순 gating이 아니라 arg synthesis에도 필요합니다.
- counterexample JSONL은 논문 본문이나 부록에 넣을 실제 예시로 씁니다.

## 6. E2. Budgeted Context-Churn Efficiency

### 목적

`T1` chatter level이 올라갈 때 `B3`가 token과 mission wall-clock, mission success를 얼마나 안정적으로 유지하는지 본다.

### 입력

- live Gemini session
- `t1_low`, `t1_medium`, `t1_high`
- baseline `B0`, `B1`, `B3`

### 실행 순서

각 scenario마다 baseline을 반복합니다.

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E2 \
  --scenario t1_low \
  --baseline B3 \
  --seed 11 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E2 \
  --scenario t1_medium \
  --baseline B1 \
  --seed 11 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E2 \
  --scenario t1_high \
  --baseline B0 \
  --seed 11 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

`B2`는 live가 아니라 replay-only이므로 E2 본표에는 넣지 않고, 필요하면 medium/high trace를
재생성한 뒤 replay 보조 비교로만 넣습니다.

### 생성 파일

각 run마다:

- `summary.json`
- `metrics/summary.json`
- `replay/observer.jsonl`
- `effective_prompt.md`

### 반드시 확인할 값

- `mission_success_rate`
- `mission_success_under_token_budget`
- `mission_success_under_time_budget`
- `cumulative_prompt_tokens`
- `prompt_tokens_per_successful_control_decision`
- `mission_duration_ms`

### 결과 표 정리

한 줄을 `scenario × baseline × seed`로 둡니다.

권장 컬럼:

- `scenario`
- `baseline`
- `seed`
- `mission_success_rate`
- `cumulative_prompt_tokens`
- `prompt_tokens_per_successful_control_decision`
- `mission_duration_ms`
- `manual_override_needed`

### 해석

- chatter level이 올라갈수록 `B0/B1`의 token, wall-clock 증가폭이 더 크면 계획과 일치합니다.
- `B3`의 success가 비슷하거나 더 높으면서 token과 `mission_duration_ms`가 작으면 효율 주장에 유리합니다.

## 7. E3. Memory Governance & Robustness

### 목적

`B2`가 단순 TTL tuning만으로 `B3`를 대체할 수 없는지,
그리고 `B1`이 stale action / pending obligation / revision drift에 취약한지 본다.

### 입력

- 새 replay trace
- scenario `t2_spec_drift`, `t3_recovery`
- baseline `B1`, `B2`, `B3`

### 권장 fault

- `geofence_revision_update`
- `tool_registry_revision_bump`
- `vehicle_reconnect`
- `ack_without_confirm`
- `offboard_stream_loss`
- `no_progress_during_goto`
- `stale_transform`
- `stale_pose`

### 실행 순서

1. `B1/B3` 비교:

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B1 \
  --scenario t3_recovery \
  --seed 31 \
  --fault ack_without_confirm \
  --output /abs/path/to/control-view/artifacts/metrics/e3_ack_without_confirm_b1.json \
  --counterexamples-jsonl /abs/path/to/control-view/artifacts/replay/e3_ack_without_confirm_b1_counterexamples.jsonl
```

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --scenario t3_recovery \
  --seed 31 \
  --fault ack_without_confirm \
  --output /abs/path/to/control-view/artifacts/metrics/e3_ack_without_confirm_b3.json \
  --counterexamples-jsonl /abs/path/to/control-view/artifacts/replay/e3_ack_without_confirm_b3_counterexamples.jsonl
```

2. `B2` TTL slice:

```bash
for ttl in 2 5 10; do
  uv run python scripts/run_replay_experiments.py \
    --root /abs/path/to/control-view \
    --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
    --policy-swap B2 \
    --scenario t3_recovery \
    --seed 31 \
    --fault offboard_stream_loss \
    --b2-ttl-sec "$ttl" \
    --output "/abs/path/to/control-view/artifacts/metrics/e3_offboard_stream_loss_b2_ttl_${ttl}.json" \
    --counterexamples-jsonl "/abs/path/to/control-view/artifacts/replay/e3_offboard_stream_loss_b2_ttl_${ttl}_counterexamples.jsonl"
done
```

3. revision drift 예시:

```bash
uv run python scripts/run_replay_experiments.py \
  --root /abs/path/to/control-view \
  --replay-jsonl /abs/path/to/control-view/artifacts/replay/goto_hold_land.jsonl \
  --policy-swap B3 \
  --scenario t2_spec_drift \
  --seed 21 \
  --fault geofence_revision_update \
  --output /abs/path/to/control-view/artifacts/metrics/e3_geofence_revision_b3.json \
  --counterexamples-jsonl /abs/path/to/control-view/artifacts/replay/e3_geofence_revision_b3_counterexamples.jsonl
```

### 반드시 확인할 값

- `stale_action_rate`
- `premature_transition_rate`
- `obligation_closure_accuracy`
- `blocker_resolution_time`
- `operator_takeover_rate`
- `unsafe_act_after_fault`
- `mission_success_rate`

### 결과 표 정리

한 줄을 `scenario × fault × baseline × ttl × seed`로 둡니다.

권장 컬럼:

- `scenario`
- `fault`
- `baseline`
- `b2_ttl_sec`
- `seed`
- `stale_action_rate`
- `premature_transition_rate`
- `obligation_closure_accuracy`
- `blocker_resolution_time`
- `operator_takeover_rate`
- `unsafe_act_after_fault`
- `mission_success_rate`

### 해석

- `B2`가 `ttl=2`에서는 맞고 `ttl=10`에서는 무너진다면, 그 자체가 TTL-only 한계를 보여줍니다.
- `unsafe_act_after_fault`와 `interface_mismatch_rate`가 `B1/B2`와 `B3`를 가르는 1차 지표입니다.
- `premature_transition_rate`와 `obligation_closure_accuracy`는 `ack_without_confirm` 같은 특정 fault에서 weak-ack handling을 해석할 때 보조 지표로 봅니다. 항상 baseline separation을 단독으로 설명하지는 않습니다.
- revision drift fault는 `stale_action_rate`가 핵심 확인값입니다.
- `unsafe_act_after_fault`가 0이 아니면 safety regression입니다.

## 8. E4. Live System Validation

### 목적

offline replay 결과가 실제 closed-loop runtime에서도 재현되는지 본다.

### 입력

- live SITL + MAVROS
- scenario `t2_spec_drift`, `t3_recovery`
- baseline 주력은 `B3`

### 실행 순서

1. dry-run으로 output tree와 scenario를 확인한다.

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E4 \
  --scenario t3_recovery \
  --baseline B3 \
  --seed 31 \
  --output-root /abs/path/to/control-view/artifacts/experiments \
  --dry-run
```

2. 실제 실행:

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E4 \
  --scenario t3_recovery \
  --baseline B3 \
  --seed 31 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

```bash
uv run python scripts/run_live_experiments.py \
  --root /abs/path/to/control-view \
  --experiment E4 \
  --scenario t2_spec_drift \
  --baseline B3 \
  --seed 21 \
  --output-root /abs/path/to/control-view/artifacts/experiments
```

### live fault 주입 방식

`live_fault_injector.py`는 아래 순서로 동작합니다.

1. 가능하면 MAVROS `CommandLong`로 `VEHICLE_CMD_INJECT_FAILURE`를 보낸다.
2. 실패하면 scenario에 적어 둔 fallback을 실행한다.
3. spec drift는 실험 디렉토리의 `control_artifacts/*.yaml`을 갱신한다.
4. 모든 step은 `fault_events.jsonl`에 남는다.

### 반드시 확인할 값

- `fault_recovery_success_rate`
- `time_to_recovery_sec`
- `manual_override_needed`
- `mission_completion_after_fault`
- `post_fault_token_spend`

위 값은 `summary.json`과 `metrics/summary.json`에 나뉘어 들어갑니다.

`t3_recovery`의 현재 시나리오는 fault injection step이 2개이므로, 성공 run에서는 아래가 함께 확인되어야 합니다.

- `fault_event_count == 2`
- `observer_summary.fault_count == 2`
- `observer_summary.recovered_fault_count == 2`

### 결과 표 정리

한 줄을 `scenario × baseline × seed`로 둡니다.

권장 컬럼:

- `scenario`
- `baseline`
- `seed`
- `fault_event_count`
- `fault_recovery_success_rate`
- `time_to_recovery_sec`
- `manual_override_needed`
- `mission_completion_after_fault`
- `post_fault_token_spend`

### 해석

- `manual_override_needed=false`이면서 `mission_completion_after_fault=true`면 가장 강한 성공 사례입니다.
- `manual_override_needed=true`라도 `unsafe act`가 없고 `SAFE_HOLD`로 끝났다면 degraded-but-safe로 분류합니다.
- `post_fault_token_spend`가 과도하게 크면 recovery cost가 높은 것입니다.

## 9. 결과 정리 형식

논문용 결과 정리는 아래 단위를 고정합니다.

- `experiment`
- `scenario`
- `baseline`
- `seed`
- `mission`

실무적으로는 아래 두 장표로 나누는 편이 가장 안전합니다.

### 표 1. Causality / Governance

- `E1/E3`를 넣습니다.
- 한 줄 = `experiment × scenario × baseline × seed`

### 표 2. Full-System Live

- `E2/E4`를 넣습니다.
- 한 줄 = `experiment × scenario × baseline × seed`

### 표 3. Counterexample 표

한 줄 = `mission × fault_or_slot × counterexample_jsonl_path`

## 10. 빠른 추출 예시

### Live summary 한 번에 보기

```bash
find /abs/path/to/control-view/artifacts/experiments -name summary.json -print0 \
| xargs -0 jq -r '[
    .experiment,
    .scenario,
    .baseline,
    .seed,
    .mission_completion_after_fault,
    .manual_override_needed,
    .time_to_recovery_sec
  ] | @tsv'
```

### Replay metrics 한 번에 보기

```bash
find /abs/path/to/control-view/artifacts/metrics -name 'e3_*.json' -print0 \
| xargs -0 jq -r '[
    .scenario,
    .policy_swap,
    .b2_ttl_sec,
    .fault,
    .metrics.stale_action_rate,
    .metrics.premature_transition_rate,
    .metrics.unsafe_act_after_fault
  ] | @tsv'
```

## 11. 반드시 같이 기록할 메타데이터

- control-view git commit
- PX4 git commit
- Gemini CLI version
- model version
- scenario 이름
- seed
- `official_trace_ready`
