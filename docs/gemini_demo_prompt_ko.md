Control View sidecar만 사용해서 PX4 SITL 미션을 감독하세요.

이 프롬프트는 `scripts/run_gemini_headless_demo.sh`가 mission 이름을 뒤에 붙여 Gemini CLI에 전달합니다.

규칙:
- raw ROS browsing은 하지 마세요.
- 먼저 `control_view.get`으로 각 family의 verdict와 blockers를 확인하세요.
- `ACT`일 때만 `action.execute_guarded`를 호출하세요.
- `SAFE_HOLD`나 `REFRESH`가 나오면 blockers를 보고 다음 결정을 내리세요.
- 각 단계마다 `ledger.tail`로 open obligation과 action state를 확인하세요.

완료 기준:
- 미션이 terminal family까지 도달해야 합니다.
- 마지막 `ledger.tail`에서 open obligation이 남지 않아야 합니다.
- 실행 중 사용한 tool과 verdict 흐름을 간단히 요약하세요.
