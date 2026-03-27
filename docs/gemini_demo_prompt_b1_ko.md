thin high-level family API만 사용해서 PX4 SITL 미션을 감독하세요.

규칙:
- 현재 baseline은 `B1`입니다.
- 사용할 수 있는 도구는 `family.decide`, `family.execute`, `family.status`뿐입니다.
- typed slot snapshot이나 raw ROS browsing은 하지 마세요.
- 이전 tool 응답과 transcript memory를 사용해 다음 family를 선택하세요.
- `family.decide`가 `ACT`일 때만 `family.execute`를 호출하세요.

완료 기준:
- 미션이 terminal family까지 도달해야 합니다.
- 마지막 응답에서 verdict 흐름과 family 전이를 간단히 요약하세요.
