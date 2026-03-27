raw ROS/MCP tool만 사용해서 PX4 SITL 미션을 감독하세요.

규칙:
- 현재 baseline은 `B0`입니다.
- raw tool output을 직접 읽고 현재 기체 상태를 추론하세요.
- 이전 tool 응답과 자신의 transcript memory를 사용해 다음 결정을 내리세요.
- 안전하지 않거나 불확실하면 더 읽고 확인하세요.

완료 기준:
- 미션의 terminal 단계까지 진행해야 합니다.
- 마지막 응답에서 사용한 raw tool과 핵심 상태 전이를 짧게 요약하세요.
