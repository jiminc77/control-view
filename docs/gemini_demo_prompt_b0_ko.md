raw ROS/MCP tool만 사용해서 PX4 SITL 미션을 감독하세요.

규칙:
- 현재 baseline은 `B0`입니다.
- 사용할 수 있는 tool은 `raw.read`, `raw.wait`, `raw.read_artifact`, `raw.arm`, `raw.takeoff`, `raw.goto`, `raw.hold`, `raw.rtl`, `raw.land`, `raw.set_mode`입니다.
- raw tool output을 직접 읽고 현재 기체 상태를 추론하세요.
- 이전 tool 응답과 자신의 transcript memory를 사용해 다음 결정을 내리세요.
- 안전하지 않거나 불확실하면 더 읽고 확인하세요.
- `read_file`, structured sidecar tool, raw ROS 외의 우회 tool은 사용하지 마세요.
- 규칙을 다시 길게 설명하거나 같은 계획을 반복하지 마세요. 도구 호출 전 설명은 한 문장 이내로 제한하세요.
- 같은 이유로 2번 이상 연속해서 망설이지 마세요. 다음 tool이 정해졌다면 바로 호출하세요.
- `takeoff_hold_land`는 `raw.arm -> raw.takeoff(target_altitude=3.0) -> raw.hold -> raw.land` 순서를 따르세요.
- `goto_hold_land`는 `raw.arm -> raw.takeoff(target_altitude=3.0) -> raw.goto(x=1.5,y=0.0,z=3.0) -> raw.hold -> raw.land` 순서를 따르세요.
- `goto_rtl`는 `raw.arm -> raw.takeoff(target_altitude=3.0) -> raw.goto(x=1.5,y=0.0,z=3.0) -> raw.rtl` 순서를 따르세요.
- 각 action 뒤에는 `raw.wait` 또는 `raw.read`로 `vehicle.mode`, `vehicle.armed`, `pose.local`, `offboard.stream.ok`를 확인하세요.
- `raw.takeoff` 뒤에는 상태 확인을 한 번만 하고 바로 다음 action으로 진행하세요. `goto_hold_land`와 `goto_rtl`에서는 takeoff 다음에 단 한 번의 status check 후 `raw.goto`를 호출하세요.
- 이미 필요한 상태가 확인되면 같은 점검을 반복하지 마세요.
- 초기 warmup 규칙:
  arm 전에 `vehicle.connected=true`, `pose.local` 존재, `home.position` 존재를 먼저 확인하세요.
  `estimator.health.veto_flags`는 참고용입니다. `pos_vert_agl`이 남아 있어도 `vehicle.connected`, `pose.local`, `home.position`이 안정적으로 보이면 2번 이상 추가 대기하지 말고 `raw.arm`을 진행하세요.
  raw baseline에서는 `home.ready` 대신 `home.position` 존재 여부를 readiness 신호로 쓰세요.
- `raw.takeoff`는 `raw.read` 또는 `raw.wait`에서 `vehicle.armed=true`가 확인된 뒤에만 호출하세요.
- arm이 실패하면 mode를 바꾸거나 takeoff를 먼저 시도하지 말고, `raw.wait`로 2~5초 기다린 뒤 readiness를 다시 읽고 `raw.arm`만 재시도하세요.
- 이륙 전에는 `raw.set_mode`를 사용하지 마세요. preflight에서 허용되는 action은 readiness 확인, `raw.arm`, `raw.takeoff`뿐입니다.

완료 기준:
- 미션의 terminal 단계까지 진행해야 합니다.
- 마지막 응답에서 사용한 raw tool과 핵심 상태 전이를 짧게 요약하세요.
