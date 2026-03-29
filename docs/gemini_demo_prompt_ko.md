full `Control View` sidecar만 사용해서 PX4 SITL 미션을 감독하세요.

이 프롬프트는 `scripts/run_gemini_headless_demo.sh`가 mission 이름을 뒤에 붙여 Gemini CLI에 전달합니다.

규칙:
- 현재 baseline은 `B3`입니다.
- `family.execute`, `family.decide`, `family.status`, `control.explain_blockers`만 사용하세요.
- `family.execute`는 verdict 확인과 guarded execute를 sidecar 내부에서 한 번에 처리하므로, 기본 실행 경로에서는 이것만 사용하세요.
- `family.execute`가 `NOT_EXECUTED`를 반환하면 `family.decide` 또는 `control.explain_blockers`로 원인을 확인하세요.
- `family.execute`와 `family.status`를 같은 턴에 병렬로 호출하지 마세요. 항상 `family.execute` 결과를 본 뒤 다음 tool을 고르세요.
- 각 단계 뒤에는 `family.status`로 recent actions와 open obligation을 확인하세요.
- `family.status`는 짧은 pending transition을 내부에서 잠깐 기다릴 수 있으니, 같은 pending family에 대해 연속으로 과도하게 반복 호출하지 마세요.
- 같은 family가 `ACKED_WEAK`인 채로 한 번 더 pending으로 남거나, latest action state가 `EXPIRED` 또는 `FAILED`로 바뀌면 그 family는 아직 끝난 것이 아닙니다. 같은 family를 다시 `family.execute` 하거나, `NOT_EXECUTED`면 `family.decide`/`control.explain_blockers`로 막힌 이유를 확인하세요.
- fault나 mode flip으로 current family가 끊기면 다음 family로 넘어가지 말고, mission order에서 첫 incomplete family부터 다시 이어가세요. 특히 `goto_hold_land`에서 `LAND`가 끊기거나 `EXPIRED`되면 `LAND`를 다시 실행해 `CONFIRMED`까지 닫으세요.
- `cli_help`, raw ROS browsing, `read_file`, `control_view.get`, `action.execute_guarded`, `ledger.tail` 같은 우회 경로는 사용하지 마세요.
- family id는 반드시 대문자 exact string만 사용하세요: `ARM`, `TAKEOFF`, `GOTO`, `HOLD`, `RTL`, `LAND`.
- 다른 tool을 쓰지 말고, 위 네 개만으로 미션을 끝내세요.

미션별 고정 규칙:
- `takeoff_hold_land`: `ARM -> TAKEOFF -> HOLD -> LAND`
- `goto_hold_land`: `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
- `goto_rtl`: `ARM -> TAKEOFF -> GOTO -> RTL`
- `ARM`이 `ACKED_STRONG`이면 길게 기다리지 말고 바로 다음 family로 진행하세요. `TAKEOFF`가 `armed_ok` blocker로 막히면 그때만 `ARM`을 다시 시도하세요.
- `TAKEOFF`는 항상 `family.execute` 또는 `family.decide`에 `proposed_args={"target_altitude": 3.0}`를 넣으세요.
- `goto_hold_land` 또는 `goto_rtl`에서 `GOTO`는 항상 `family.execute` 또는 `family.decide`에 `proposed_args={"target_pose":{"position":{"x":1.5,"y":0.0},"frame_id":"map"}}`를 넣으세요. 현재 고도 `z`는 sidecar가 `pose.local.z`로 채웁니다.

완료 기준:
- 미션이 terminal family까지 도달해야 합니다.
- terminal family의 latest action state가 `CONFIRMED`여야 합니다. `EXPIRED`나 `FAILED`는 완료가 아닙니다.
- 마지막 `family.status`에서 open obligation이 남지 않아야 합니다.
- 실행 중 사용한 tool과 verdict 흐름을 간단히 요약하세요.
