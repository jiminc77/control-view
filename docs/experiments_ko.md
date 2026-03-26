# Control View 실험 가이드

이 문서는 현재 구현체로 어떤 실험을 어떻게 재현할지, 무엇을 기록하고 어떤 지표를 계산할지 정리합니다.

## 1. 권장 mission 세트

- `ARM -> TAKEOFF -> HOLD -> LAND`
- `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
- `ARM -> TAKEOFF -> GOTO -> RTL`

2026-03-26 live SITL 기준 현재 재현 상태는 다음과 같습니다.

- `ARM -> TAKEOFF -> HOLD -> LAND`: `CONFIRMED`
- `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`: `CONFIRMED`
- `ARM -> TAKEOFF -> GOTO -> RTL`: `CONFIRMED`

## 2. fault injection 후보

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

현재 코드는 `FaultInjector.apply(records, fault_name, **params)`로 slot valid state, artifact revision, weak-ack record까지 실제로 변형할 수 있습니다.

```python
from control_view.replay.fault_injector import FaultInjector

injector = FaultInjector()
faulted_records = injector.apply(records, "stale_pose", stale_ms=1200)
```

## 3. replay 사용법

```python
from pathlib import Path

from control_view.backend.fake_backend import FakeBackend
from control_view.replay.recorder import ReplayRecorder
from control_view.replay.replayer import ReplayRunner
from control_view.service import ControlViewService

root = Path("/path/to/control-view")
service = ControlViewService(root, backend=FakeBackend())
recorder = ReplayRecorder()

recorder.record_view_request("ARM", {})
records = recorder.records

runner = ReplayRunner(service)
outputs = runner.replay(
    records,
    mode="single_step",
    fault_injector=injector,
    fault_name="tool_registry_revision_bump",
    oracle=None,
    slot_ablation=["pose.local"],
    policy_swap="B3",
)
```

`policy_swap` 의미:

- `B2`: decision-only baseline
- `B3`: TTL freshness만 유지하고 invalidator / commit-guard / pending-transition gating 제거
- `B4`: full system

## 4. 메트릭 계산

```python
from control_view.replay.metrics import compute_metrics

metrics = compute_metrics(outputs)
print(metrics)
```

현재 기본 메트릭:

- `interface_mismatch_rate`
- `mission_success_rate`
- `unsafe_act_rate`
- `false_refuse_rate`
- `unnecessary_refresh_rate`
- `stale_commit_abort_rate`
- `weak_ack_without_confirm_rate`
- `prompt_tokens_per_turn`
- `decision_latency_ms`

`stale_commit_abort_rate`는 persisted `ABORTED` action record를 기준으로 계산합니다.

`prompt_tokens_per_turn`와 `decision_latency_ms`는 Gemini JSONL log를 merge해서 계산할 수 있습니다.

```bash
./scripts/run_gemini_headless_demo.sh goto_hold_land

uv run python scripts/export_gemini_metrics.py \
  --replay-jsonl artifacts/replay/gemini_goto_hold_land_YYYYMMDD_HHMMSS.jsonl \
  --gemini-log artifacts/logs/gemini_goto_hold_land_YYYYMMDD_HHMMSS.jsonl \
  --output artifacts/metrics/gemini_goto_hold_land.json
```

## 5. Ubuntu 실험 시 기록할 것

- `uv venv .venv --python 3.12 --system-site-packages` 사용 여부와 설치한 Python 패키지 버전
- PX4 SITL 실행 커맨드
- world / vehicle 설정
- sidecar git commit hash
- ROS topic/service availability
- 각 decision point의 `control_view.get` 결과
- 각 `action.execute_guarded` 결과
- opened / closed obligations
- geofence / tool_registry revision 변화
- replay JSONL 산출물
- Gemini CLI JSONL log와 metrics export JSON

## 6. 권장 실험 산출물

- `artifacts/replay/*.jsonl`
- `artifacts/metrics/*.json`
- `artifacts/logs/*.log`
- 실험 당시 사용한 `configs/*.yaml`
- 실험 당시 사용한 `configs/gemini_mcp*.json`
- `ledger.tail` 캡처와 `control_view.get` / `action.execute_guarded` structured JSON 샘플
