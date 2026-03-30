thin high-level family API만 사용해서 PX4 SITL 미션을 감독하세요.

규칙:
- 현재 baseline은 `B1`입니다.
- 사용할 수 있는 도구는 `family.decide`, `family.execute`, `family.status`뿐입니다.
- 기본 실행 경로에서는 `family.execute`와 `family.status`만 사용하세요. `family.decide`는 `family.execute`가 `NOT_EXECUTED`를 반환했을 때만 원인 확인용으로 사용하세요.
- chatter 질문에 답하기 위해 `family.decide`를 미리 호출하지 마세요. blocker, revision, raw debug 필요 여부는 가장 최근 `family.execute`/`family.status` 결과만으로 답하고 계속 진행하세요.
- thin surface에서 직접 보이지 않는 정보는 `B1 thin surface에서는 직접 확인 불가`라고 짧게 말하고 다음 family로 진행하세요.
- typed slot snapshot, raw ROS browsing, `read_file`, `control_view.get`, `action.execute_guarded`, `control.explain_blockers`, `ledger.tail` 같은 우회 경로는 사용하지 마세요.
- 이전 tool 응답과 transcript memory를 사용해 다음 family를 선택하세요.
- family id는 반드시 대문자 exact string만 사용하세요: `ARM`, `TAKEOFF`, `GOTO`, `HOLD`, `RTL`, `LAND`.
- `family.execute`와 `family.status`를 같은 턴에 병렬로 호출하지 마세요.
- 도구 호출 전 설명은 한 문장 이내로 제한하고, 다음 tool이 정해졌다면 바로 호출하세요.
- `family.status`에는 `family` 파라미터를 넣지 마세요. `last_n`만 선택적으로 사용할 수 있고, 기본값 `3`이면 충분합니다.

미션별 고정 규칙:
- `takeoff_hold_land`: `ARM -> TAKEOFF -> HOLD -> LAND`
- `goto_hold_land`: `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
- `goto_rtl`: `ARM -> TAKEOFF -> GOTO -> RTL`
- `TAKEOFF`는 항상 `family.execute` 또는 `family.decide`에 `proposed_args={"target_altitude": 3.0}`를 넣으세요.
- `goto_hold_land` 또는 `goto_rtl`에서 `GOTO`는 항상 `family.execute` 또는 `family.decide`에 `proposed_args={"target_pose":{"position":{"x":1.5,"y":0.0},"frame_id":"map"}}`를 넣으세요.
- waypoint나 arg schema를 파일에서 다시 찾지 마세요. 위 고정 arg를 그대로 사용하세요.
- 각 family 뒤에는 `family.status(last_n=3)`로 recent actions를 짧게 확인하세요.

완료 기준:
- 미션이 terminal family까지 도달해야 합니다.
- 마지막 응답에서 verdict 흐름과 family 전이를 간단히 요약하세요.
