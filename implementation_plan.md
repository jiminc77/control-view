## Proposal Freeze

* **Paper type**: theory-flavored systems paper
* **Single formal object**: `Control View`
* **Scope**: LLM-based **PX4 drone supervisory tool selection** on ROS 2 / MCP stack
* **Platform freeze**: PX4 + MAVROS on Ubuntu 24.04 / ROS 2 Jazzy
* **Execution topology**: sidecar runs on the same host as the ROS graph; critical control path is host-local; `ros-mcp-server` is read-only out-of-band debug only
* **Validation first**: PX4 SITL + Gazebo `gz_x500`
* **Clarified family policy**
  1. only `GOTO` uses PX4 `OFFBOARD`
  2. `TAKEOFF` stays `CommandTOL`-based
  3. `GOTO` setpoint publisher is an allowed bounded backend helper, not a generic low-level control loop
* **Core claim**: chat transcript나 session summary는 robot control memory의 올바른 단위가 아니며, action family별로 decision에 필요한 typed evidence와 pending transition만 남기는 `Control View`가 더 적절한 memory abstraction이라는 주장
* **Main contributions**

  1. `Control View` formalism
  2. family contract로부터 `Control View`를 생성하는 compiler
  3. freshness / authority / invalidation / action-state semantics를 `Control View` validity semantics로 통합한 governor
  4. MCP sidecar realization + PX4 supervisory evaluation

---

# 1. Paper Draft

## Abstraction

### Problem setting

우리는 LLM이 raw ROS tools를 직접 훑으며 robot을 제어하는 setting이 아니라, **high-level control family**(`ARM`, `TAKEOFF`, `GOTO`, `HOLD`, `RTL`, `LAND`)를 선택하고 parameterize하는 **supervisory control** setting을 다룸. 본 버전의 concrete target은 PX4 multicopter를 MAVROS를 통해 제어하는 ROS 2 Jazzy / Ubuntu 24.04 환경이며, sidecar는 ROS graph와 같은 host에서 실행된다. 이 setting에서 중요한 memory는 “무슨 대화를 했는가”가 아니라 “지금 이 family를 실행해도 되는가, 실행하려면 어떤 argument가 필요한가, 직전 action의 effect가 아직 transition 중인가”이다.

현재 Gemini CLI는 hierarchical `GEMINI.md` context를 매 prompt마다 concatenated instruction으로 model에 보내고, `/compress`로 전체 chat context를 summary로 치환하며, sessions는 자동 저장 후 `/resume`로 이어가는 구조다. public roadmap도 long-running session의 병목을 linear history, basic compression, manual state management로 명시하고 있다. 반면 `ros-mcp-server`는 ROS topics/services/types를 browse하고 publish/subscribe·service call 하게 해 주지만, control-time precondition, effect confirmation, freshness, authority, invalidation, artifact ownership까지는 제공하지 않는다. 즉 현재 stack은 **conversation continuity + tool discovery**에는 맞지만, **control-time state sufficiency**에는 맞지 않다. [1][6][7]

본 문서의 운영 가정은 다음과 같다.

* normal mode에서는 Gemini CLI가 sidecar MCP server만 본다.
* raw `ros-mcp-server`는 별도 process의 read-only debug adapter로만 사용한다.
* critical state path는 sidecar local monotonic clock을 기준으로 평가한다.
* 최초 검증은 PX4 SITL + Gazebo `gz_x500`에서 수행한다. [11]

### Central object: Control View

시간 (t)까지의 execution history를 (h_t), action family를 (f)라고 할 때, 본 논문의 단일 formal object를 다음처럼 정의한다.

\[
V_f(h_t) = \big(E_f(h_t), \Omega_f(h_t)\big)
\]

* \(E_f(h_t)\): family \(f\)의 decision에 필요한 finite typed evidence slots
* \(\Omega_f(h_t)\): 아직 confirm되지 않은 open obligations 집합

각 evidence slot \(e_k\)는 다음 tuple로 둔다.

\[
e_k = (x_k, q_k, a_k, \tau_k, r_k, \nu_k)
\]

* \(x_k\): value
* \(q_k\): quality / uncertainty / confidence
* \(a_k\): authority source
* \(\tau_k\): observation time
* \(r_k\): revision / version
* \(\nu_k\): validity status

여기서 slot은 scalar 하나가 아니라 **update-coherent atomic slot**이다. 예를 들어 `pose.local`은 `x,y,z,yaw`를 따로 흩뿌리는 것이 아니라 `{value, frame, covariance, source, timestamp, revision}`을 한 묶음으로 취급한다. PX4/MAVROS realization에서도 `pose.local` primary source는 `/mavros/local_position/odom`, fallback은 `/mavros/local_position/pose`로 고정한다.

### Decision interface

family \(f\)에 대해 full history가 최종적으로 보존해야 하는 decision-level output을

\[
I_f(h_t) = (\texttt{verdict}, \texttt{args}, \texttt{commit_guard}, \texttt{blockers})
\]

로 둔다.

* `verdict ∈ {ACT, REFRESH, SAFE_HOLD, REFUSE}`
* `args`: family executor에 넘길 structured arguments
* `commit_guard`: execution 직전 반드시 다시 확인할 critical slots
* `blockers`: 지금 act를 막는 이유의 구조화된 설명

논문 레벨 formal interface는 계속 `args`를 사용한다. 다만 구현 surface에서는 merge, normalization, server fill을 모두 반영한 실행 인자를 `canonical_args`라는 이름으로 노출한다.

### Sufficiency

`Control View`의 핵심 정의는 “좋은 summary”가 아니라 **decision-sufficient projection**이라는 점이다.

\[
V_f(h_1)=V_f(h_2)\Rightarrow I_f(h_1)=I_f(h_2)
\quad \forall h_1,h_2\in\mathcal H_f
\]

즉 trace class \(\mathcal H_f\) 위에서, `Control View`가 같으면 family-level decision interface도 같아야 한다.

### Relative minimality

본 논문은 global minimality를 주장하지 않는다. 대신 **family-relative, ontology-relative, trace-class-relative minimality**만 주장한다.

\[
\forall k\in K_f,\ \exists h_1,h_2\in\mathcal H_f:
V_f^{-k}(h_1)=V_f^{-k}(h_2),\ I_f(h_1)\neq I_f(h_2)
\]

즉 slot 하나를 제거한 subview는 더 이상 sufficiency를 유지하지 못하는 counterexample이 있어야 한다. 이 minimality는 theorem으로 강하게 밀기보다, replay-based ablation으로 operational하게 검증한다.

### Validity semantics

freshness/authority/invalidation은 별도 object가 아니라 `Control View` entry의 validity semantics로 흡수한다.

\[
\mathrm{Valid}_f(e_k)=
\mathrm{AuthorityOK}(e_k)\wedge
\mathrm{Fresh}_f(e_k)\wedge
\neg\mathrm{Invalidated}(e_k)
\]

여기서

* `AuthorityOK`: source priority, corroboration, disagreement 처리
* `Fresh_f`: field class와 family risk를 반영한 freshness
* `Invalidated`: event-triggered invalidation

핵심은 `TTL only`가 아니라 field-typed validity라는 점이다. PX4/MAVROS target에서는

* `kinematic` fields: 짧은 TTL + frame contract + motion-triggered invalidation
* `event_discrete` fields: event invalidation 우선
* `versioned_artifact` fields: revision mismatch 시 invalid
* `derived_quality` fields: source freshness + derivation freshness를 함께 평가

로 concretize한다.

### Action-state semantics

본 realization에서는 `acknowledged`와 `confirmed`를 더 세밀하게 구분한다. backend action의 runtime lifecycle은 다음 상태 집합을 가진다.

\[
\alpha_f(h_t)\in
\{\texttt{REQUESTED},\texttt{ACKED\_WEAK},\texttt{ACKED\_STRONG},\texttt{CONFIRMED},\texttt{FAILED},\texttt{EXPIRED}\}
\]

이 distinction은 `Control View`의 별도 formal object는 아니지만, obligation opening과 blocker generation을 안정적으로 정의하기 위한 runtime semantics이다.

* `CommandBool` / `CommandTOL` 계열 response는 `ACKED_STRONG` 후보가 된다.
* `SetMode` 계열 response는 `mode_sent`에 불과하므로 `ACKED_WEAK`다.
* `CONFIRMED`는 언제나 sensor/state evidence로만 닫힌다.

즉 **`ACKED != CONFIRMED`** 가 본 구현의 first-class invariant다. [9][10]

### Open obligations

robot control에서는 `acknowledged`와 `confirmed`를 분리해야 한다. 예를 들어 `set_mode` service success는 `mode stabilized`를 뜻하지 않고, `takeoff accepted`는 `airborne confirmed`를 뜻하지 않는다. 따라서 \(\Omega_f(h_t)\)는 `ACKED-but-not-CONFIRMED` transition을 명시적으로 들고 가는 구조가 되어야 한다.

예:

* `ARM_PENDING`
* `TAKEOFF_PENDING`
* `NAV_PENDING`
* `HOLD_PENDING`
* `RTL_PENDING`
* `LAND_PENDING`

이 obligation이 close되기 전에는 다음 family decision에서 blocker로 surface되거나 `SAFE_HOLD`를 유도한다.

---

## Relative works

### 1) Conversational / agent memory

최근 survey들은 agent memory를 단순 retrieval 문제가 아니라, **good action selection을 위한 sufficient statistic**의 유지 문제로 본다. 평가 축도 recall 중심에서 utility, efficiency, adaptivity 등으로 이동 중이다. CMA는 RAG를 stateless lookup으로 보고, persistent storage·selective retention·temporal chaining이 가능한 architecture가 필요하다고 주장한다. 이 문맥에서 본 논문은 generic long-term memory가 아니라, **robot supervisory control에 필요한 decision-sufficient state abstraction**으로 문제를 더 좁게 재정의한다. [2]

### 2) Memory governance / stability

memory governance 쪽 최근 흐름은 stale memory, safety, privacy, stability를 별도 이슈로 다룬다. 이 관점은 본 논문과 맞닿아 있지만, 본 논문은 governance를 일반 memory store 차원이 아니라 **family-conditioned Control View의 validity semantics**로 구체화한다는 점에서 다르다. 즉 “무엇을 기억할까”보다 “지금 act 가능한 state인가”로 더 직접 내려간다. [3]

### 3) Embodied planning / lightweight belief interfaces

RPMS는 embodied planning에서 rule retrieval로 action feasibility를 강제하고, episodic memory applicability를 lightweight belief state로 gating하며, rules-first arbitration을 사용한다. 이 점은 본 논문의 중요한 근접선행이다. 다만 RPMS가 environment interaction planning에서 rule-memory conflict를 다룬다면, 본 논문은 **tool-using robot supervision**에서 family별 admissibility, freshness, authority, commit-time guard를 하나의 control memory abstraction으로 묶는 데 초점을 둔다. [4]

### 4) Embodied / robot memory

household robotics 쪽 memory-augmented work들은 RAG로 past actions나 object records를 retrieve해 follow-up query나 object tracking을 돕는 경우가 많다. MEMENTO는 embodied agent가 여러 memories를 함께 사용할 때 information overload와 coordination failure를 겪는다고 분석한다. 본 논문은 past interaction retrieval 자체보다, **현재 control decision에 필요한 typed state만 남기는 projection**을 제안한다는 점에서 차별적이다. [5]

---

## Methods

### Overview

1. **Family contract authoring**
   각 action family에 대해 required evidence, predicates, argument builders, backend mapping, confirm rules, invalidators, obligation templates를 정의한다.
2. **Contract-to-ControlView compiler**
   contract로부터 family별 `Control View` schema를 생성한다.
3. **Validity-governed runtime**
   ROS observations, backend action states, operator interventions, artifact revisions를 소비하며 `Control View`를 materialize하고 유지한다.

### 1) Family contract

각 family \(f\)에 대해 contract \(C_f\)를 다음 요소로 구성한다.

* `guard slots`: act 이전에 반드시 valid해야 하는 slots
* `support slots`: argument generation에 필요한 slots
* `confirm slots`: effect confirmation에 필요한 slots
* `diagnostic slots`: 실패 해석에만 필요한 slots
* `predicates`: admissibility 조건
* `backend mapping`: 어떤 MAVROS primitive로 family를 realize할지
* `invalidators`: 어떤 event가 어떤 slot을 즉시 invalid하게 만드는지
* `obligation templates`: ack 후 confirm 전까지 유지할 pending transition

예시적으로 PX4/MAVROS target의 `GOTO` contract는 다음 속성을 가진다.

* `guard`: `vehicle.connected`, `vehicle.armed`, `pose.local`, `estimator.health`, `failsafe.state`, `geofence.status`, `tf.local_body`, `offboard.stream.ok`
* `support`: `battery.margin`
* `confirm`: `vehicle.mode`, `nav.progress`
* `predicates`
  * connected is true
  * armed is true
  * pose.local is valid
  * estimator.health above threshold
  * target inside geofence
  * offboard.stream.ok is true
  * heuristic failsafe is not active
* `backend mapping`
  * bounded `OffboardStreamWorker` starts `/mavros/setpoint_position/local`
  * `/mavros/set_mode` requests `OFFBOARD`
* `invalidators`
  * any motion family requested
  * estimator reset detected
  * vehicle reconnect
  * geofence revision bump
  * offboard stream lost
* `obligation`
  * `NAV_PENDING` until `OFFBOARD` entry + arrival confirmation

### 2) Control View compiler

compiler의 입력은 human-authored family contracts와 field ontology다. 완전 자동 추론은 목표가 아니다. 대신 다음을 수행한다.

1. predicate, argument builder, confirm rule에서 slot dependency 추출
2. scalar field를 update-coherent atomic slot으로 묶음
3. slot별 resolver chain 또는 derivation plan 부착
4. slot별 validity rule 부착
5. blocker template 생성
6. family별 `CompiledViewSpec` 산출

출력은 \(f\)에 대한

* required slot set \(K_f\)
* role partition (`guard/support/confirm/diagnostic`)
* predicate evaluator
* resolver / derivation plan
* invalidation graph
* obligation rules
* backend action plan
* prompt serialization template

### 3) Evidence materialization

runtime은 raw transcript를 읽지 않고, event stream에서 current `Control View`를 materialize한다. event source는 다음 다섯 종류다.

* ROS observations
* backend action request / ack / confirm
* operator intervention
* artifact revision (`geofence`, `mission spec`, `tool registry`)
* debug capability probe result

각 slot은 단일 raw source가 아니라 resolver chain 또는 derivation으로 갱신된다.

예:

* `pose.local`
  * primary = `/mavros/local_position/odom`
  * fallback = `/mavros/local_position/pose`
  * invalidator = `any_motion_family_requested`, `estimator_reset_detected`, `vehicle_reconnect`

* `tool_registry.rev`
  * source = exposed MCP tool schema hash + backend capability probe hash
  * owner = sidecar

* `geofence.status`
  * source = sidecar-owned `artifacts/geofence.yaml` revision + `proposed_target_pose` + `pose.local`
  * never owned by PX4 itself

MCP는 `listChanged` notification과 `structuredContent`/`outputSchema`를 지원하므로, sidecar는 raw verbose text 대신 strict typed snapshot을 model에 공급할 수 있다. `ros-mcp-server`는 compatibility/debug layer로 유지하되, critical path truth source는 아니다. [6][7]

### 4) Validity governor

policy는 다음 verdict space를 가진다.

\[
\pi_f(V_f)=
\begin{cases}
\texttt{ACT} & \text{critical slots valid \& predicates true}\\
\texttt{REFRESH} & \text{blockers refreshable}\\
\texttt{SAFE\_HOLD} & \text{risk high and blocker unresolved}\\
\texttt{REFUSE} & \text{otherwise}
\end{cases}
\]

핵심은 `TTL only`가 아니라 field-typed validity라는 점이다. 추가로 본 target에서는 다음 규칙을 둔다.

* `failsafe.state`는 provisional heuristic slot이며, blocker 생성과 `SAFE_HOLD` 트리거에는 사용 가능하지만 hard safety claim의 단독 근거로는 사용하지 않는다.
* `offboard.stream.ok`는 `GOTO` precondition 전용이다.
* `home.ready`는 `RTL` admissibility에 직접 쓰이며, 기존 `home_pose` mismatch를 제거한다.
* `vehicle.connected`는 event-based freshness의 root guard 역할을 한다.

### 5) Lease and commit-time guard

freshness는 decision 시점의 성질이 아니라 **decision-to-execution interval** 전체의 문제다. 따라서 `ACT` verdict는 즉시 raw execution으로 이어지지 않고, 짧은 lease를 발급한다.

* decision 시 critical slot revision snapshot과 `canonical_args` hash를 lease에 포함
* executor가 raw control call 직전에 critical slots 재검사
* lease 만료, revision 변경, new invalidator 발생 시 execution abort
* abort 시 `REFRESH` 또는 `SAFE_HOLD`로 전이

이 설계가 asynchronous sensor drift, reconnect, last-moment mode flip, geofence revision, OFFBOARD stream loss를 다루는 핵심이다.

### 6) Obligation-mediated composition

multi-step mission은 full transcript replay가 아니라 successive `Control View`와 open obligations로 연결한다.

예:

* `TAKEOFF` 후 `TAKEOFF_PENDING`
* altitude confirmation 후 obligation close
* close 전 `GOTO` 시도 시 blocker 반환
* `GOTO` ack 후 `NAV_PENDING`
* `OFFBOARD` 진입만 됐고 arrival이 안 되면 아직 next-step transition을 열지 않음

즉 compositionality는 “이전 대화 기억”이 아니라 “이전 family가 남긴 pending effect가 정리되었는가”로 관리한다.

### 7) Realization

실제 system은 **MCP sidecar**로 구현한다.

* Gemini CLI는 raw ROS tools 대신 sidecar tools를 주로 사용
* sidecar는 current `Control View`를 유지하고 typed snapshot을 제공
* critical control path는 same-host ROS/MAVROS observations에 직접 붙는다
* 필요 시 read-only debug path로 raw `ros-mcp-server` introspection 허용
* debug adapter capability는 README 문구가 아니라 runtime probe로 판정한다
* normal operation에서는 raw topic/service browsing을 LLM의 기본 memory substrate로 쓰지 않는다

---

## Experiment

### Experimental setup

* environment: PX4 SITL + Gazebo `gz_x500` + ROS 2 Jazzy + MAVROS + sidecar MCP + Gemini CLI
* host topology: sidecar와 ROS graph는 동일 host에서 실행
* scope families: `ARM`, `TAKEOFF`, `GOTO`, `HOLD`, `RTL`, `LAND`
* traces: nominal + fault-injected supervisory missions
* evaluation granularity: **decision point** 기준
* oracle visibility: Gazebo ground-truth pose/velocity는 evaluation only이며 `Control View`에는 절대 노출하지 않음 [11]
* measurement source: token/latency/compression 계측은 Gemini CLI telemetry를 primary source로 사용
* shared protocol: 모든 baseline은 동일 model version, operator prompt template, mission seed, fault seed를 공유
* budget protocol: 결과는 unconstrained 평균과 함께 fixed `token budget`, `wall-clock budget` 기준으로도 보고
* success taxonomy: `nominal success`, `degraded-but-safe success`, `failed/operator takeover`로 통일하며 `SAFE_HOLD`는 degraded-but-safe로 계산

### Mission families

* `T1. Context-Churn Patrol`: waypoint patrol 중간에 status explanation, raw inspection, debug query 같은 non-control dialogue가 반복 삽입되는 mission
* `T2. Spec-Drift Patrol`: waypoint edit, altitude change, geofence shrink, completion criteria change가 mid-mission에 발생하는 mission
* `T3. Recovery Marathon`: 한 mission 안에서 repeated mode flip, OFFBOARD loss, reconnect, estimator degradation 같은 fault가 2~3회 발생하는 mission

### Baselines

* `B0`: raw Gemini CLI + `ros-mcp-server`
  * raw ROS/MCP tool surface + transcript/session-summary 기반 기억
  * structured state abstraction 없음
* `B1`: controlled high-level transcript baseline
  * `B3`와 동일한 family/high-level API를 사용
  * 기억은 transcript/session-summary 기반이며 sidecar memory abstraction은 없음
* `B2`: simple structured-cache baseline
  * `B3`와 동일한 family/high-level API를 사용
  * flat JSON cache + fixed global TTL(`5s`) + last-writer-wins
  * authority model, event invalidation, artifact revision graph, open obligation, revision-aware commit guard는 없음
* `B3`: full system = `Control View` + validity governor + action-state semantics + lease/commit guard + obligations

### E1. Sufficiency / relative minimality

**목적**
`B3`가 family-level decision interface를 보존하는지, 그리고 어떤 slots가 실제로 indispensable한지 확인

이 실험은 token efficiency 주장을 위한 것이 아니라, `Control View` representation의 sufficiency와 relative minimality를 검증하기 위한 실험으로 위치시킨다.

**Steps**

1. PX4/MAVROS-specific family contracts를 hand-authoring
2. `T1`~`T3`에서 decision traces 수집
3. 각 decision point에 대해 oracle decision interface 생성
4. `B3`로 `verdict`, `canonical_args`, `commit_guard`, `blockers` 계산
5. slot 제거 ablation 수행
6. interface mismatch 여부 측정
7. mismatch가 발생한 counterexample를 기록해 relative minimality evidence로 사용

**Metrics**

* `interface_mismatch_rate`
* `unsafe_accept_after_ablation`
* `canonical_arg_error_rate`
* `blocker_explanation_loss`

### E2. Budgeted Context-Churn Efficiency

**목적**
대화 잡음이 많이 끼는 `T1` 조건에서 `B3`가 memory efficiency와 decision latency를 얼마나 잘 방어하는지 검증

주 비교축은 `B0`, `B1`, `B3`로 두고, `B2`는 medium/high chatter subset에서 보조 결과만 보고한다.

**Steps**

1. `T1` mission을 chatter level `low`, `medium`, `high`로 구성
2. 동일 mission script를 `B0`, `B1`, `B3`에 반복 실행하고, `B2`는 보조 subset에 대해 실행
3. session logs, tool calls, token usage, compression count, latency를 수집
4. raw tool outputs와 prompt context 크기를 비교
5. mission completion, decision delay, operator intervention 횟수를 기록
6. 결과를 fixed `token budget`과 fixed `wall-clock budget` 기준으로 다시 집계

**Metrics**

* `mission_success_under_token_budget`
* `mission_success_under_time_budget`
* `cumulative_prompt_tokens`
* `prompt_tokens_per_successful_control_decision`
* `decision_latency_ms`
* `compression_count`
* `turns_until_first_compression`

### E3. Memory Governance & Robustness

**목적**
`T2`와 `T3`에서 `B3`가 단순 transcript baseline(`B1`)과 단순 structured TTL cache(`B2`)보다 stale continuation, revision conflict, pending obligation handling에서 얼마나 강한지 검증

이 실험이 reviewer 반론, 즉 "structured JSON + TTL이면 충분하지 않나?"에 직접 답하는 핵심 비교축이다.

**Steps**

1. `T2`와 `T3`를 다음 scenario로 구성
   * waypoint edit during `GOTO`
   * geofence shrink mid-flight
   * altitude target change after takeoff
   * acked-but-not-confirmed mode flip
   * repeated OFFBOARD warmup failure / stream loss
   * vehicle reconnect after stale readiness or home-state carryover
   * spec change 직후 stale action replay
2. 동일 trace 또는 live run을 `B1`, `B2`, `B3`에 적용
3. 각 decision point에서 verdict, action-state progression, blocker, commit outcome을 기록
4. `B2`에 대해서는 `TTL=2s/5s/10s` sensitivity slice를 추가해 결과가 단순 TTL tuning으로 뒤집히는지 확인
5. refresh, safe_hold, refuse, abort, operator takeover 분포를 비교

**Metrics**

* `success_rate`
* `stale_action_rate`
* `premature_transition_rate`
* `obligation_closure_accuracy`
* `recovery_success_rate`
* `blocker_resolution_time`
* `operator_takeover_rate`
* `unsafe_act_after_fault`

### E4. Live System Validation

**목적**
`B3`가 replay뿐 아니라 실시간 closed-loop runtime에서도 꼬임 없이 동작하는지 검증

이 실험은 comparative claim의 주 증거라기보다, 앞선 replay 기반 결과가 offline artifact가 아님을 보이는 system-level validation으로 위치시킨다.

**Steps**

1. `gz_x500` SITL를 실시간으로 구동하고 live Gemini session을 유지
2. `T2` 또는 `T3` representative mission을 `B3`로 실행
3. fault timing을 randomized schedule로 삽입
4. 가능하면 PX4 shell `failure` 명령을 우선 사용해 `gps off`, `mag off`, `rc_signal off`, `mavlink_signal off`, 지원되는 motor failure를 주입
5. `SYS_FAILURE_EN`을 활성화하고, motor 계열은 `CA_FAILURE_MODE`를 추가 설정
6. shell failure injection이 지원되지 않는 경우에만 외부 injector fallback을 사용
7. recovery path, unsafe act 여부, operator takeover 필요 여부를 기록

**Metrics**

* `fault_recovery_success_rate`
* `time_to_recovery_sec`
* `extra_tool_calls_until_recovery`
* `manual_override_needed`
* `mission_completion_after_fault`
* `post_fault_token_spend`

### Oracle construction

oracle은 full simulator state + full normalized event history를 기반으로 family admissibility를 평가하는 rule-based checker로 정의한다. 논문의 주장은 “LLM이 oracle이 된다”가 아니라, **full state를 보지 않고도 `Control View`가 oracle decision interface를 보존한다**는 것이다.

oracle input은 다음으로 고정한다.

* full normalized event history
* Gazebo ground-truth pose
* Gazebo ground-truth velocity
* mission-spec revision history
* geofence revision history

oracle role은 다음으로 고정한다.

* arrival label
* touchdown label
* no-progress label
* stale-action label
* premature-transition label
* degraded-safe outcome label
* interface oracle construction

---

## Expect Result

1. `B3`는 trace class 내 family-level decision interface를 높은 정확도로 보존할 가능성이 크다.
2. `pose.local`, `vehicle.connected`, `vehicle.armed`, `estimator.health`, `offboard.stream.ok`, `geofence.status`, `home.ready` 같은 slots는 ablation 시 즉시 mismatch를 유발할 가능성이 크다.
3. `B3`의 가장 강한 장점은 short/simple mission보다 `T2`, `T3`처럼 spec drift와 repeated recovery가 있는 조건에서 나타날 가능성이 크다.
4. `B2`는 `B1`보다 나은 결과를 보일 수 있으며, 일부 `T1` 구간에서는 `B3`에 근접할 수도 있다. 그러나 reconnect, revision-based invalidation, weak-ack handling, open obligation closure에서는 `B3`보다 열세일 가능성이 크다.
5. 따라서 strongest causal separation은 `B2` vs `B3`에서 드러날 가능성이 크며, 논문의 핵심은 "structured cache가 transcript보다 낫다"가 아니라 "structured cache만으로는 semantic invalidation과 obligation governance가 충분하지 않다"는 점으로 귀결될 가능성이 크다.
6. `B3`는 medium/high chatter의 fixed budget 조건에서 `B0`, `B1`보다 prompt token과 decision delay를 더 잘 방어할 가능성이 크지만, 모든 short/simple mission에서 일관되게 우세할 필요는 없다.

---

# 2. Implementation Specification for AI Agent

## 2.1 Scope freeze

AI Agent에게 구현을 맡길 때 가장 먼저 고정해야 할 범위를 아래처럼 명시한다.

### Environment freeze

```yaml
platform:
  autopilot: PX4
  ros_bridge: MAVROS
  host_os: Ubuntu 24.04
  host_ros: ROS 2 Jazzy
  sidecar_location: same_host_as_ros_graph
  validation_first: PX4 SITL + Gazebo

sitl_baseline:
  px4_target: make px4_sitl gz_x500
  vehicle: x500
  world: default
  oracle_mode: gazebo_ground_truth_adapter

critical_path_policy:
  control_path: host_local_only
  debug_path: ros_mcp_out_of_band_only
  remote_ros_allowed_for_debug: true
```

### 반드시 구현할 것

* `Control View` formal object를 materialize하는 sidecar MCP server
* family contracts 6개
  * `ARM`
  * `TAKEOFF`
  * `GOTO`
  * `HOLD`
  * `RTL`
  * `LAND`
* field ontology v0.2
* validity governor
* action-state semantics (`REQUESTED`, `ACKED_WEAK`, `ACKED_STRONG`, `CONFIRMED`, `FAILED`, `EXPIRED`)
* lease + commit-time guard
* obligation engine
* replay/evaluation harness
* Gemini CLI integration용 MCP tool surface
* `MavrosBackend`
* `OffboardStreamWorker` for `GOTO`
* `GlobalFixProvider` for `TAKEOFF` request fill

### 이번 버전에서 구현하지 않을 것

* full automatic contract extraction
* generic low-level control loop
* continuous online TTL learning
* multi-robot coordination
* unrestricted raw `ros-mcp-server` tool exposure in normal mode
* 복잡한 open-vocabulary perception memory
* hard-safety proof based solely on `failsafe.state`
* external geofence admin API의 완전한 제품화

### Engineering target note

Ubuntu 24.04 / ROS 2 Jazzy는 vendor-recommended baseline이라기보다 **engineering target**으로 취급한다. 따라서 dependency version pin과 CI image 고정은 필수이며, 문서 단계에서는 exact package version을 박아 넣지 않는다.

---

## 2.2 Recommended architecture

```text
Gemini CLI
   │
   │ MCP
   ▼
control-view-sidecar
   ├── ContractStore
   ├── StateStore (snapshot cache)
   ├── Ledger (append-only events)
   ├── ArtifactManager
   ├── Governor
   ├── Executor
   ├── ObligationEngine
   ├── ActionStateTracker
   ├── OffboardStreamWorker
   ├── ROS Adapter (critical path; host-local MAVROS/ROS)
   └── Optional Debug Adapter (read-only ros-mcp-server)
```

핵심 원칙은 다음 다섯 가지다.

1. **LLM은 stream을 보지 않고 snapshot만 본다**
2. **current truth는 transcript가 아니라 StateStore에 있다**
3. **execution 직전에는 항상 commit-time recheck를 한다**
4. **debug path는 control path와 분리한다**
5. **bounded backend helper는 허용하되 generic low-level loop는 구현하지 않는다**

`GOTO`용 setpoint publisher는 `OffboardStreamWorker`라는 bounded backend helper로만 존재한다. 이는 PX4 `OFFBOARD` admission과 유지에 필요한 proof-of-life를 제공하기 위한 component이며, trajectory tracking loop나 continuous planner를 의미하지 않는다. [8]

---

## 2.3 Recommended tech stack

* **Language**: Python 3.11+
* **MCP server**: `FastMCP` 또는 Python MCP SDK
* **ROS adapter**: `rclpy` 또는 `roslibpy` 기반 client
* **Storage**: SQLite + WAL mode
* **Models / schemas**: `pydantic`
* **Async runtime**: `asyncio`
* **Testing**: `pytest`
* **Replay logs**: JSONL or Parquet
* **Config**: YAML
* **Version pinning**: exact lockfile + CI image pin

ROS graph와 같은 host에서 sidecar를 실행하므로, 시간 의미는 sidecar local monotonic clock 하나로 통일한다. ROS `header.stamp`는 observational metadata로만 저장한다.

---

## 2.4 Repository layout

```text
control-view-sidecar/
├── README.md
├── pyproject.toml
├── artifacts/
│   └── geofence.yaml
├── configs/
│   ├── system.yaml
│   ├── backend_mavros.yaml
│   └── gemini_mcp.json
├── contracts/
│   ├── fields/
│   │   ├── vehicle.connected.yaml
│   │   ├── vehicle.mode.yaml
│   │   ├── vehicle.armed.yaml
│   │   ├── pose.local.yaml
│   │   ├── velocity.local.yaml
│   │   ├── estimator.health.yaml
│   │   ├── failsafe.state.yaml
│   │   ├── battery.margin.yaml
│   │   ├── home.position.yaml
│   │   ├── home.ready.yaml
│   │   ├── tf.local_body.yaml
│   │   ├── offboard.stream.ok.yaml
│   │   ├── geofence.status.yaml
│   │   ├── nav.progress.yaml
│   │   ├── mission.spec.rev.yaml
│   │   └── tool_registry.rev.yaml
│   └── families/
│       ├── arm.yaml
│       ├── takeoff.yaml
│       ├── goto.yaml
│       ├── hold.yaml
│       ├── rtl.yaml
│       └── land.yaml
├── src/
│   ├── app.py
│   ├── mcp_server/
│   │   ├── server.py
│   │   ├── tool_schemas.py
│   │   └── tools.py
│   ├── contracts/
│   │   ├── models.py
│   │   ├── loader.py
│   │   └── compiler.py
│   ├── runtime/
│   │   ├── action_state.py
│   │   ├── event_bus.py
│   │   ├── materializer.py
│   │   ├── governor.py
│   │   ├── executor.py
│   │   ├── obligations.py
│   │   ├── blockers.py
│   │   ├── lease.py
│   │   ├── offboard_stream.py
│   │   └── serializer.py
│   ├── storage/
│   │   ├── sqlite_store.py
│   │   ├── snapshots.py
│   │   ├── ledger.py
│   │   └── artifacts.py
│   ├── backend/
│   │   ├── base.py
│   │   ├── rosbridge_client.py
│   │   ├── mavros_backend.py
│   │   ├── global_fix_provider.py
│   │   └── ros_mcp_debug_adapter.py
│   ├── replay/
│   │   ├── recorder.py
│   │   ├── replayer.py
│   │   ├── fault_injector.py
│   │   ├── oracle.py
│   │   └── metrics.py
│   └── common/
│       ├── types.py
│       ├── time.py
│       └── utils.py
└── tests/
    ├── unit/
    ├── integration/
    └── replay/
```

---

## 2.5 Core data models

### 1) `FieldSpec`

필수 필드

* `id`
* `class`: `kinematic | event_discrete | versioned_artifact | derived_quality`
* `owner`: `backend | sidecar`
* `value_type` 또는 `value_schema`
* `source`
* `authority`
* `derivation` (optional)
* `revision_rule`
* `freshness`
* `invalidators`
* `serialization_policy`
* `status` (optional; provisional slot 여부)

### 2) `FamilyContract`

필수 필드

* `family`
* `risk_class`
* `argument_schema`
* `guard_slots`
* `support_slots`
* `confirm_slots`
* `diagnostic_slots`
* `predicates`
* `backend_mapping`
* `effects`
* `obligation_templates`
* `safe_hold_mapping`

### 3) `EvidenceEntry`

필수 필드

* `slot_id`
* `value_json`
* `quality_json`
* `authority_source`
* `received_mono_ns`
* `received_wall_time`
* `source_header_stamp` (optional)
* `revision`
* `frame_id`
* `valid_state`
* `lineage_event_id`
* `reason_codes`

`valid_state` enum 추천:

* `VALID`
* `MISSING`
* `STALE`
* `INVALIDATED`
* `DISAGREED`
* `UNCONFIRMED`

### 4) `ActionRecord`

필수 필드

* `action_id`
* `family`
* `requested_mono_ns`
* `state`
* `ack_strength`
* `backend_request_json`
* `backend_response_json`
* `confirm_evidence_json`
* `failure_reason_codes`
* `related_obligation_ids`

`state` enum 추천:

* `REQUESTED`
* `ACKED_WEAK`
* `ACKED_STRONG`
* `CONFIRMED`
* `FAILED`
* `EXPIRED`
* `ABORTED`

### 5) `ObligationRecord`

필수 필드

* `obligation_id`
* `family`
* `kind`
* `status`
* `created_mono_ns`
* `updated_mono_ns`
* `open_on_action_state`
* `close_conditions`
* `failure_conditions`
* `related_action_id`
* `notes`

`status` enum 추천:

* `OPEN`
* `CONFIRMED`
* `FAILED`
* `EXPIRED`
* `CANCELLED`

### 6) `LeaseToken`

필수 필드

* `lease_id`
* `family`
* `issued_mono_ns`
* `expires_mono_ns`
* `critical_slot_revisions`
* `arg_hash`
* `nonce`
* `signature`

lease는 sidecar 내부 secret으로 signed token 형태 권장. `arg_hash`는 반드시 `canonical_args` 기준으로 계산한다.

### 7) `Blocker`

필수 필드

* `slot_id`
* `kind`
* `severity`
* `message`
* `refreshable`
* `refresh_hint`
* `safe_action`
* `evidence_summary`

---

## 2.6 Field ontology v0.2

초기 구현용 field set은 아래로 고정한다.

| Slot | Class | Primary source / owner | Notes |
| --- | --- | --- | --- |
| `vehicle.connected` | event_discrete | `/mavros/state.connected` | heartbeat root slot |
| `vehicle.mode` | event_discrete | `/mavros/state.mode` | `SetMode` confirm evidence |
| `vehicle.armed` | event_discrete | `/mavros/state.armed` | arm / land confirm evidence |
| `pose.local` | kinematic | `/mavros/local_position/odom` | fallback `/mavros/local_position/pose` |
| `velocity.local` | kinematic | `/mavros/local_position/velocity_local` | hold / land / nav confirmation |
| `estimator.health` | derived_quality | `/mavros/estimator_status` | score + veto flags |
| `failsafe.state` | event_discrete | heuristic from `/mavros/statustext/recv` + corroborators | provisional slot |
| `battery.margin` | derived_quality | `/mavros/battery` + config reserve | mission reserve |
| `home.position` | versioned_artifact | `/mavros/home_position/home` | `RTL` readiness source |
| `home.ready` | derived_quality | derived from `home.position`, `vehicle.connected` | replaces old `home_pose` mismatch |
| `tf.local_body` | versioned_artifact | frame ids from `/mavros/local_position/odom` | frame contract |
| `offboard.stream.ok` | derived_quality | sidecar-owned `OffboardStreamWorker` monitor | `GOTO` precondition only |
| `geofence.status` | versioned_artifact | sidecar-owned `artifacts/geofence.yaml` + `pose.local` + target | not PX4-owned |
| `nav.progress` | derived_quality | sidecar-derived from active family + pose + velocity + mode | arrival / progress state |
| `mission.spec.rev` | versioned_artifact | sidecar-owned mission store | revision-based invalidation |
| `tool_registry.rev` | versioned_artifact | sidecar-owned schema hash + capability probe | MCP surface revision |

### Slot-specific freeze notes

* `pose.local.primary_source = /mavros/local_position/odom`
* `failsafe.state`는 **weakest slot in v0.2** 로 취급하며, blocker generation과 `SAFE_HOLD`에는 사용할 수 있지만 hard safety claim의 단독 근거로 쓰지 않는다.
* `offboard.stream.ok`는 `publish_rate_hz`, `last_publish_age_ms`, `warmup_elapsed_ms`로만 계산한다.
* `geofence.status`는 file-backed artifact revision이 바뀌면 즉시 invalidation 대상이다.
* 위 slot source freeze는 MAVROS ROS2 `sys_status`, `local_position`, `home_position`, `global_position` plugin surface를 기준으로 한다. [12][13][14][15]

### Default slot constants

```yaml
freshness:
  pose_local_ttl_ms: {low: 500, medium: 300, high: 200}
  velocity_local_ttl_ms: {low: 500, medium: 300, high: 200}
  estimator_health_ttl_ms: {low: 1000, medium: 500, high: 500}
  battery_margin_ttl_ms: {low: 5000, medium: 2000, high: 2000}
  pose_local_lease_ms: 80
  velocity_local_lease_ms: 80
  estimator_health_lease_ms: 100

thresholds:
  arrival_distance_m: 0.5
  arrival_speed_mps: 0.3
  hold_speed_mps: 0.3
  takeoff_alt_tolerance_m: 0.3
  takeoff_min_alt_gain_m: 0.5
  landed_speed_mps: 0.2
  battery_reserve_fraction: 0.20

offboard:
  stream_rate_hz: 20.0
  warmup_sec: 1.0
  min_ok_rate_hz: 5.0
  max_last_publish_age_ms: 250
```

---

## 2.7 Family contracts v0.2

### `ARM`

* backend primitive: `/mavros/cmd/arming` `CommandBool`
* guard: `vehicle.connected`, `vehicle.mode`, `failsafe.state`
* confirm: `vehicle.armed`
* obligation: `ARM_PENDING`

### `TAKEOFF`

* backend primitive: `/mavros/cmd/takeoff` `CommandTOL`
* request fill: current global fix + current yaw는 server fill
* guard: `vehicle.connected`, `vehicle.mode`, `vehicle.armed`, `pose.local`, `estimator.health`, `failsafe.state`
* support: `battery.margin`
* confirm: `pose.local`, `velocity.local`
* obligation: `TAKEOFF_PENDING`
* note: `OFFBOARD`를 요구하지 않는다

### `GOTO`

* backend primitive: `OffboardStreamWorker` + `/mavros/set_mode` `OFFBOARD`
* guard: `vehicle.connected`, `vehicle.armed`, `pose.local`, `estimator.health`, `geofence.status`, `tf.local_body`, `failsafe.state`, `offboard.stream.ok`
* support: `battery.margin`
* confirm: `vehicle.mode`, `nav.progress`
* obligation: `NAV_PENDING`
* note: `OFFBOARD`를 사용하는 유일한 family

### `HOLD`

* backend primitive: `/mavros/set_mode` `AUTO.LOITER`
* guard: `vehicle.connected`, `vehicle.mode`, `vehicle.armed`
* confirm: `vehicle.mode`, `nav.progress`
* obligation: `HOLD_PENDING`

### `RTL`

* backend primitive: `/mavros/set_mode` `AUTO.RTL`
* guard: `vehicle.connected`, `vehicle.armed`, `home.ready`
* confirm: `vehicle.mode`
* obligation: `RTL_PENDING`
* note: terminal family로 취급하되 background monitor는 계속 유지

### `LAND`

* backend primitive: `/mavros/set_mode` `AUTO.LAND`
* guard: `vehicle.connected`, `vehicle.mode`, `vehicle.armed`, `pose.local`, `estimator.health`
* confirm: `vehicle.armed`, `velocity.local`
* obligation: `LAND_PENDING`

### Backend mapping freeze

| Family | Primitive | Ack | Confirm | Ack strength |
| --- | --- | --- | --- | --- |
| `ARM` | `/mavros/cmd/arming` | `response.success == true` | `/mavros/state.armed == true` | strong |
| `TAKEOFF` | `/mavros/cmd/takeoff` | `response.success == true` | `extended_state` airborne + altitude threshold | strong |
| `GOTO` | stream + `/mavros/set_mode OFFBOARD` | `mode_sent == true` | `/mavros/state.mode == OFFBOARD` + arrival | weak |
| `HOLD` | `/mavros/set_mode AUTO.LOITER` | `mode_sent == true` | mode + low speed | weak |
| `RTL` | `/mavros/set_mode AUTO.RTL` | `mode_sent == true` | `/mavros/state.mode == AUTO.RTL` | weak |
| `LAND` | `/mavros/set_mode AUTO.LAND` | `mode_sent == true` | landed + disarmed | weak |

---

## 2.8 Contract authoring rules

1. **scalar가 아니라 atomic slot으로 authoring할 것**
   `pose.local.x`, `pose.local.y` 분리 금지. `pose.local`로 묶는다.
2. **모든 slot에 authority policy 또는 derivation ownership을 둘 것**
   source 없는 slot 금지.
3. **모든 slot에 invalidators를 둘 것**
   시간 경과만으로 stale 되는 구조 금지.
4. **모든 family는 `guard/support/confirm/diagnostic` 네 role을 분리할 것**
5. **모든 predicate는 machine-readable reason code를 반환할 것**
   단순 boolean만 반환 금지.
6. **모든 effect는 obligation template를 생성할 것**
   ack와 confirm을 분리한다.
7. **모든 time comparison은 monotonic clock 기준**
   wall time은 audit/logging 전용.
8. **frame contract 없는 pose argument 금지**
   target pose에는 반드시 `frame_id` 포함.
9. **`failsafe.state`는 heuristic slot로 명시할 것**
   hard-safety proof의 단독 근거로 쓰지 않는다.
10. **`GOTO`만 `OFFBOARD`를 사용한다**
    다른 family에 `offboard.stream.ok`를 guard로 억지 재사용하지 않는다.
11. **bounded backend helper는 허용한다**
    `OffboardStreamWorker`는 backend helper이지 generic low-level control loop가 아니다.
12. **lease hash는 canonicalized args 기준으로 계산한다**
    pre-normalized input으로 hash를 만들지 않는다.

---

## 2.9 Example YAML contracts

### `contracts/fields/pose.local.yaml`

```yaml
id: pose.local
class: kinematic
owner: backend
value_type: pose_atomic
source:
  primary:
    topic: /mavros/local_position/odom
    type: nav_msgs/msg/Odometry
    fields:
      position: pose.pose.position
      orientation: pose.pose.orientation
      covariance: pose.covariance
      frame_id: header.frame_id
      child_frame_id: child_frame_id
  fallback:
    topic: /mavros/local_position/pose
    type: geometry_msgs/msg/PoseStamped
    fields:
      position: pose.position
      orientation: pose.orientation
      frame_id: header.frame_id
authority:
  order: [odom, pose]
revision_rule: increment_on_every_accepted_sample
freshness:
  ttl_ms:
    low: 500
    medium: 300
    high: 200
  lease_ms: 80
invalidators:
  - any_motion_family_requested
  - estimator_reset_detected
  - vehicle_reconnect
serialization_policy:
  include:
    - value
    - covariance
    - frame_id
    - authority_source
    - observed_age_ms
    - revision
```

### `contracts/families/goto.yaml`

```yaml
family: GOTO
risk_class: high
argument_schema:
  type: object
  required: [target_pose]
guard_slots:
  - vehicle.connected
  - vehicle.armed
  - pose.local
  - estimator.health
  - geofence.status
  - tf.local_body
  - failsafe.state
  - offboard.stream.ok
support_slots:
  - battery.margin
confirm_slots:
  - vehicle.mode
  - nav.progress
diagnostic_slots:
  - velocity.local
predicates:
  - id: connected_ok
    expr: vehicle.connected == true
  - id: armed_ok
    expr: vehicle.armed == true
  - id: pose_valid
    expr: pose.local.valid_state == "VALID"
  - id: est_ok
    expr: estimator.health.score >= 0.8
  - id: geofence_ok
    expr: geofence.status.target_inside == true
  - id: offboard_stream_ok
    expr: offboard.stream.ok == true
  - id: failsafe_clear
    expr: failsafe.state.active != true
backend_mapping:
  kind: composite
  steps:
    - start_offboard_stream:
        topic: /mavros/setpoint_position/local
        rate_hz: 20
        warmup_sec: 1.0
    - set_mode:
        name: /mavros/set_mode
        type: mavros_msgs/srv/SetMode
        request:
          base_mode: 0
          custom_mode: OFFBOARD
effects:
  invalidates:
    - pose.local
    - nav.progress
obligation_templates:
  - id: NAV_PENDING
    open_on: ACKED_WEAK
    close_when:
      - vehicle.mode == "OFFBOARD"
      - nav.progress.phase == "ARRIVED"
    fail_when:
      - OFFBOARD_lost_before_arrival
      - no_progress_within_sec: 3.0
safe_hold_mapping:
  backend_action: HOLD
```

---

## 2.10 Compiler responsibilities

`compiler.py`가 해야 할 일은 명확해야 한다.

### Input

* all `FieldSpec`
* all `FamilyContract`

### Output

* `CompiledViewSpec` per family

### `CompiledViewSpec` 필수 내용

* `required_slots`
* `role_partition`
* `predicate_plan`
* `resolver_plan`
* `derivation_plan`
* `blocker_templates`
* `refresh_plan`
* `commit_guard_slots`
* `obligation_templates`
* `backend_action_plan`
* `serializer_plan`

### Compiler validation

* unknown slot reference 검사
* circular derived-slot dependency 검사
* missing invalidator 검사
* missing authority policy 검사
* missing owner 검사
* role overlap 검사
* predicate parse 검사
* family argument schema 검사
* `OFFBOARD`-only policy 위반 검사
* provisional slot usage policy 검사
* canonicalization rule completeness 검사

---

## 2.11 Runtime algorithms

### A. Event ingestion

`event_bus.py`는 다음 normalized event type을 지원해야 한다.

* `SENSOR_OBS`
* `BACKEND_REQUEST`
* `BACKEND_ACK`
* `BACKEND_CONFIRM`
* `OPERATOR_OVERRIDE`
* `CONFIG_REVISION`
* `INVALIDATOR`
* `TIMER_TICK`
* `DEBUG_PROBE`

각 event는 최소 다음 필드를 가진다.

* `event_id`
* `event_type`
* `source`
* `received_mono_ns`
* `received_wall_time`
* `source_header_stamp` (optional)
* `payload_json`

### B. Materialization

`materializer.py` 동작 순서

1. event 수신
2. sidecar local monotonic clock으로 ingress timestamp 부여
3. 영향 받는 slots 계산
4. resolver 실행
5. authority arbitration 또는 derivation 실행
6. revision 증가
7. `EvidenceEntry` 갱신
8. dependent derived slots 재계산
9. `ActionRecord` / `ObligationRecord` 갱신
10. ledger append
11. snapshot cache update

### C. Validity evaluation

`governor.py`는 slot 단위 validity를 먼저 평가하고, 그 뒤 family predicate를 평가해야 한다.

권장 순서

1. slot 존재 여부
2. invalidator 존재 여부
3. freshness
4. authority disagreement
5. provisional slot policy 적용
6. family-specific predicate
7. blocker 생성
8. verdict 생성

### D. Action-state progression

runtime은 action state를 아래처럼 평가한다.

```yaml
CommandBool_or_CommandTOL:
  REQUESTED -> ACKED_STRONG -> CONFIRMED
  REQUESTED -> FAILED
  ACKED_STRONG -> EXPIRED

SetMode:
  REQUESTED -> ACKED_WEAK -> CONFIRMED
  REQUESTED -> FAILED
  ACKED_WEAK -> FAILED
  ACKED_WEAK -> EXPIRED
```

추가 규칙:

* `GOTO`는 `OffboardStreamWorker` warmup 성공 후에만 `SetMode` request를 보낸다.
* `SetMode.mode_sent == true`는 adoption 보장이 아니므로 절대 `CONFIRMED`로 승격하지 않는다.
* `action.execute_guarded`는 pre-dispatch 실패 시 `ABORTED`를 반환할 수 있다.

### E. Verdict generation

권장 로직

1. critical blockers 없음 → `ACT`
2. blockers 모두 refreshable → `REFRESH`
3. unresolved blocker + family risk high → `SAFE_HOLD`
4. 그 외 → `REFUSE`

### F. Commit-time guard

`executor.py` 실행 직전 절차

1. lease signature 검증
2. lease expiration 확인
3. `canonical_args` hash 검증
4. critical slot revision이 lease와 동일한지 확인
5. critical slot validity 재평가
6. predicates 재평가
7. pass 시 backend primitive 실행
8. ack 수신 시 action state 및 obligation open
9. fail 시 structured blocker 반환

### G. Obligation lifecycle

obligation close는 time만으로 하지 말고 **sensor confirmation**과 **state transition evidence**를 같이 봐야 한다.

| Obligation | Open on | Close when | Fail when |
| --- | --- | --- | --- |
| `ARM_PENDING` | `ARM` enters `ACKED_STRONG` | `/mavros/state.armed == true` for 0.3s | not confirmed within 2.0s |
| `TAKEOFF_PENDING` | `TAKEOFF` enters `ACKED_STRONG` | `extended_state` airborne and `pose.local.z >= target_z - 0.3` for 0.5s | altitude gain < 0.5m within 5.0s, not confirmed within 20.0s, disarmed before confirm |
| `NAV_PENDING` | `GOTO` enters `ACKED_WEAK` | mode == `OFFBOARD`, distance <= 0.5m, speed <= 0.3m/s for 0.5s | OFFBOARD lost before arrival, no progress within 3.0s, timeout `max(10.0, 2.0 * planned_distance_m + 5.0)` |
| `HOLD_PENDING` | `HOLD` enters `ACKED_WEAK` | mode == `AUTO.LOITER` and speed <= 0.3m/s for 1.0s | not confirmed within 2.0s |
| `RTL_PENDING` | `RTL` enters `ACKED_WEAK` | mode == `AUTO.RTL` | not confirmed within 2.0s |
| `LAND_PENDING` | `LAND` enters `ACKED_WEAK` | landed state `ON_GROUND` for 1.0s and disarm within 5.0s | not confirmed within 30.0s |

`RTL`은 terminal family로 취급하되, `family_success_on_mode_entry = true`와 `continue_background_monitoring = true`를 동시에 둔다.

---

## 2.12 Artifact ownership / revision policy

artifact owner를 분리해야 invalidation graph가 실체를 가진다.

```yaml
artifacts:
  geofence:
    owner: SidecarArtifactManager
    storage: artifacts/geofence.yaml
    revision:
      kind: monotonically_increasing_int
      bump_on:
        - file_checksum_change
        - admin_update_api_commit
    emits_event: CONFIG_REVISION

  mission_spec:
    owner: MissionSpecStore
    storage: sqlite.artifacts
    revision:
      kind: monotonically_increasing_int
      bump_on:
        - normalized_patch_commit
    emits_event: CONFIG_REVISION

  tool_registry:
    owner: MCPToolRegistry
    storage: in_memory + sqlite.artifacts
    revision:
      kind: monotonically_increasing_int
      bump_on:
        - sidecar_startup_schema_hash_change
        - backend_capability_probe_hash_change
        - debug_adapter_capability_change
    emits_event: CONFIG_REVISION
```

정책:

* `geofence.status`는 PX4가 아니라 sidecar-owned artifact다.
* `mission.spec.rev`는 operator-approved normalized patch commit에서만 bump된다.
* `tool_registry.rev`는 README 문구가 아니라 runtime capability probe 결과를 반영한다.

---

## 2.13 Time semantics

```yaml
time_policy:
  authority_clock:
    kind: sidecar_local_monotonic_clock
    api: time.monotonic_ns

  ros_header_stamp_policy:
    role: observational_metadata_only
    never_used_for:
      - lease_expiry
      - freshness_age
      - timeout_deadline
      - event_ordering

  ingress_timestamp:
    recv_mono_ns: assigned_on_sidecar_receive
    recv_wall_time: assigned_for_audit_only
    source_header_stamp: stored_if_present

  replay_policy:
    preserve: recv_mono_delta
    ignore_for_scheduling: source_header_stamp
```

Lease / TTL / timeout / ordering은 전부 sidecar monotonic clock 기준으로 평가한다. ROS `header.stamp`는 sensor metadata로만 저장한다.

---

## 2.14 Argument precedence / canonicalization

```yaml
arg_policy:
  precedence_high_to_low:
    - server_controlled
    - explicit_user
    - contract_fill
    - backend_default

  explicit_user_wins:
    - target_pose.position
    - target_altitude
    - hold_duration

  server_controlled_wins:
    - frame_id
    - takeoff.current_geo_reference
    - goto.stream_rate_hz
    - safe_hold_mode
    - backend_timeout_values

  canonicalization:
    GOTO:
      - accept_frames: [map]
      - if_user_frame_not_map:
          - transform_to_map_if_tf_available
          - else_blocker: missing_frame_transform
      - normalize_numeric_precision: 1e-3
    TAKEOFF:
      - convert_target_altitude_to_absolute_local_target_z_using_pose.local
      - fill_current_geo_from_/mavros/global_position/global
    HOLD:
      - no_external_args
    RTL:
      - no_external_args
    LAND:
      - no_external_args

  conflict_policy:
    when_explicit_user_conflicts_with_server_controlled:
      action: BLOCK
      blocker_kind: arg_conflict

  hashing:
    arg_hash_input: canonical_args_json_sorted
```

정책:

* `control_view.get`는 `proposed_args`를 입력으로 받지만, 출력은 항상 `canonical_args`다.
* `LeaseToken.arg_hash`는 canonicalization 이후 결과로 계산한다.
* `frame_id`는 user override 대상이 아니라 server-controlled field다.

---

## 2.15 MCP tool surface

normal mode에서 Gemini CLI에 노출할 tool은 최소 5개로 고정한다.

| Tool | Purpose |
| --- | --- |
| `control_view.get` | family별 current view 조회 |
| `control_view.refresh` | 필요한 slots targeted refresh |
| `action.execute_guarded` | lease 기반 guarded execution |
| `control.explain_blockers` | blocker explanation 전용 |
| `ledger.tail` | 최근 causal slice 조회 |

### `control_view.get`

**Input**

* `family`
* `proposed_args`

**Output**

* `family`
* `verdict`
* `canonical_args`
* `critical_slots`
* `support_slots`
* `blockers`
* `open_obligations`
* `lease_token` (only if `ACT`)
* `lease_expires_in_ms`

### `control_view.refresh`

**Input**

* `family` or explicit `slots`

**Output**

* refreshed slots summary
* unresolved blockers
* new verdict

### `action.execute_guarded`

**Input**

* `family`
* `canonical_args`
* `lease_token`

**Output**

* `status`: `REQUESTED | ACKED_WEAK | ACKED_STRONG | CONFIRMED | FAILED | EXPIRED | ABORTED`
* `action_id`
* `opened_obligation_ids`
* `abort_reason` if any

### `control.explain_blockers`

**Input**

* `family`
* `proposed_args`

**Output**

* blocker list
* refresh hints
* suggested safe action

### `ledger.tail`

**Input**

* `since_mono_ns` or `last_n`

**Output**

* recent events
* recent action states
* open obligations
* artifact revisions

모든 MCP tool은 반드시 `structuredContent` + strict `outputSchema`를 사용해야 한다. verbose natural-language text는 짧은 summary 한 줄만 넣고, 실제 state는 JSON으로 반환해야 한다. [6]

---

## 2.16 ROS / backend adapter design

`backend/base.py`에 추상 interface를 정의한다.

### Required methods

* `get_current_snapshot(slot_ids)`
* `refresh_slot(slot_id)`
* `get_global_fix()`
* `set_mode(mode)`
* `arm()`
* `takeoff(target_altitude, geo_reference)`
* `goto(target_pose, canonical_args)`
* `hold()`
* `rtl()`
* `land()`

### Required helper components

* `OffboardStreamWorker.start(target_pose, rate_hz, warmup_sec)`
* `OffboardStreamWorker.update_target(target_pose)`
* `OffboardStreamWorker.stop()`
* `GlobalFixProvider.current_fix()`

### Recommended backends

1. `MavrosBackend`
   PX4 state/action을 MAVROS topics/services에 매핑한다.

2. `RosMcpDebugAdapter`
   read-only introspection용. raw topic/service/action browsing 비교 검증용이다.

### Concrete MAVROS mapping

* `ARM`
  * service: `/mavros/cmd/arming`
  * type: `mavros_msgs/srv/CommandBool`
* `TAKEOFF`
  * service: `/mavros/cmd/takeoff`
  * type: `mavros_msgs/srv/CommandTOL`
  * request fill: current global fix + current yaw
* `GOTO`
  * helper: publish `/mavros/setpoint_position/local`
  * service: `/mavros/set_mode`
  * mode: `OFFBOARD`
* `HOLD`
  * service: `/mavros/set_mode`
  * mode: `AUTO.LOITER`
* `RTL`
  * service: `/mavros/set_mode`
  * mode: `AUTO.RTL`
* `LAND`
  * service: `/mavros/set_mode`
  * mode: `AUTO.LAND`

### Adapter rules

* backend는 LLM-friendly string이 아니라 **typed Python objects** 반환
* 모든 backend action은 timeout 필수
* `SetMode` response는 `ACKED_WEAK`로만 해석
* action success와 effect confirm을 절대 혼동하지 말 것
* `TAKEOFF`에 필요한 global fix가 없으면 refreshable blocker를 반환할 것

### ROS-MCP debug adapter patch

```yaml
ros_mcp_debug_adapter:
  role: read_only_out_of_band_introspection
  process_model: separate_process
  path_criticality: non_critical
  pinning:
    strategy: exact_git_commit_or_release_tag
  startup_probe:
    required_services:
      - /rosapi/services
      - /rosapi/topics
      - /rosapi/service_type
    optional_action_services:
      - /rosapi/action_servers
      - /rosapi/interfaces
      - /rosapi/action_goal_details
      - /rosapi/action_result_details
      - /rosapi/action_feedback_details
  capability_flags:
    actions_supported: runtime_probe_only
    never_infer_from_readme: true
```

---

## 2.17 Storage design

SQLite schema는 아래 5테이블이면 충분하다.

### `events`

* append-only
* 모든 normalized event 저장

### `evidence_current`

* slot별 latest materialized entry
* 빠른 view 생성용

### `obligations`

* open/closed transition 관리

### `actions`

* executor가 요청한 family action 기록
* request / ack / confirm / fail / abort 추적

### `artifacts`

* mission spec, geofence, tool registry revision 저장

### 필수 인덱스

* `events(received_mono_ns)`
* `evidence_current(slot_id)`
* `obligations(status, family)`
* `actions(action_id, state)`
* `artifacts(artifact_name, revision)`

권장 옵션:

* SQLite WAL mode
* periodic snapshot compaction
* JSON column for payload

---

## 2.18 Replay / evaluation harness

이 부분은 꼭 구현해야 논문화가 쉬워진다.

### SITL scope freeze

```yaml
simulator: PX4 SITL + Gazebo
px4_target: gz_x500
test_missions:
  - takeoff_hold_land
  - square_goto
  - goto_rtl
oracle:
  input:
    - full_normalized_event_history
    - gazebo_ground_truth_pose
    - gazebo_ground_truth_velocity
  visibility: evaluation_only
```

### Recorder

`recorder.py`가 기록할 것

* raw ROS observations
* normalized events
* family decisions
* verdicts
* `canonical_args`
* lease issuance
* commit aborts
* action-state transitions
* opened/closed obligations
* backend acks/results

### Replayer

`replayer.py` 기능

* recorded trace를 원래 timing으로 재생
* fast-forward replay
* single-step replay
* fault injection hook
* policy swap (`B1`, `B2`, `B3`) 지원
* slot ablation 지원

### Fault injector

최소 fault set

* pose message delay
* estimator reset event
* vehicle reconnect
* operator mode override
* geofence revision update
* tool registry revision bump
* ack-without-confirm
* OFFBOARD warmup failure
* OFFBOARD stream loss
* no-progress during `GOTO`
* stale transform
* battery reserve drop

### Metrics module

반드시 계산할 것

* `interface_mismatch_rate`
* `unsafe_act_rate`
* `false_refuse_rate`
* `unnecessary_refresh_rate`
* `stale_commit_abort_rate`
* `weak_ack_without_confirm_rate`
* `stale_action_rate`
* `premature_transition_rate`
* `obligation_closure_accuracy`
* `recovery_success_rate`
* `mission_success_rate`
* `mission_success_under_token_budget`
* `mission_success_under_time_budget`
* `cumulative_prompt_tokens`
* `prompt_tokens_per_successful_control_decision`
* `compression_count`
* `decision_latency_ms`
* `fault_recovery_success_rate`
* `post_fault_token_spend`

---

## 2.19 Gemini CLI integration

### Normal mode

* Gemini CLI에는 sidecar MCP server만 연결
* raw `ros-mcp-server` tools는 비노출 또는 read-only debug profile에만 연결

### Debug mode

* sidecar + `ros-mcp-server` 둘 다 연결
* 단, prompt에서 “raw tools는 inspection only” policy 강제
* debug adapter capability는 startup probe 결과에 따라 동적으로 줄일 수 있음

### Why

Gemini CLI는 현재 hierarchical context, `/compress`, auto-saved sessions를 제공하지만, roadmap도 linear history와 manual state anchoring을 한계로 인정한다. 따라서 end-to-end demo에서는 chat continuity 기능이 아니라, sidecar가 제공하는 typed `Control View`를 primary state substrate로 써야 한다. [1]

---

## 2.20 Tests that must exist

### Unit tests

* contract loader validation
* predicate evaluator
* freshness evaluator
* authority arbitration
* blocker generation
* lease validation
* action-state transition logic
* obligation state machine
* argument canonicalization
* heuristic failsafe parser

### Integration tests

* `ARM -> TAKEOFF -> HOLD -> LAND`
* `ARM -> TAKEOFF -> GOTO -> HOLD -> LAND`
* `ARM -> TAKEOFF -> GOTO -> RTL`
* mode flip during `GOTO`
* estimator reset before commit
* geofence update between decision and execution
* tool registry revision bump
* ack without confirm
* OFFBOARD warmup failure
* OFFBOARD stream loss
* vehicle reconnect

### Replay tests

* slot ablation replay
* `B2` vs `B3` replay
* stale pose replay
* transform mismatch replay
* no-progress replay
* stale-commit abort replay

---

## 2.21 Milestone plan

### M0. Contract skeleton

* field YAML v0.2
* family YAML v0.2
* compiler validation

### M1. State materialization

* MAVROS adapter
* event bus
* SQLite ledger
* `control_view.get`

### M2. Governor + executor

* validity logic
* blockers
* lease
* arg canonicalization
* commit-time guard
* `action.execute_guarded`

### M3. Action states + obligations

* `ACKED_WEAK` / `ACKED_STRONG` distinction
* obligation engine
* transition closure
* `ledger.tail`

### M4. Backend helpers

* `OffboardStreamWorker`
* `GlobalFixProvider`
* debug capability probe

### M5. Evaluation harness

* recorder
* replayer
* fault injector
* oracle
* metrics

### M6. Gemini CLI demo

* MCP config
* scripted mission runs
* token/latency logging

---

## 2.22 Residual risks and future upgrades

다음 항목은 이번 문서 업데이트 후에도 의도적으로 남겨 둔 리스크 또는 future work다.

1. `failsafe.state`는 여전히 heuristic slot이다.
   blocker generation과 `SAFE_HOLD`에는 쓰지만, standalone hard-safety proof로 사용하지 않는다.
2. Ubuntu 24.04 / ROS 2 Jazzy는 engineering target이다.
   exact version pin과 CI image lock 없이는 재현성이 흔들릴 수 있다.
3. `TAKEOFF`는 current global fix와 current yaw server fill에 의존한다.
   해당 data가 없으면 refreshable blocker로 다뤄야 한다.
4. geofence는 file-backed artifact로 freeze했지만, external admin API 설계는 이번 범위 밖이다.
5. 향후 stronger PX4-native failsafe source나 direct PX4 DDS tap을 붙이면 heuristic safety slot을 보강할 수 있다.

---

## 2.23 Definition of done

AI Agent 구현 완료 기준은 아래로 둔다.

1. 6개 family contract가 load되고 compile된다.
2. sidecar가 `control_view.get`, `refresh`, `execute_guarded`를 stable하게 제공한다.
3. raw ROS state가 transcript 없이 `Control View`로 materialize된다.
4. `canonical_args`가 precedence + normalization + server fill을 반영해 결정된다.
5. `REQUESTED`, `ACKED_WEAK`, `ACKED_STRONG`, `CONFIRMED`, `FAILED`, `EXPIRED`가 실제 runtime에서 구분된다.
6. stale state에서 commit-time abort가 실제로 동작한다.
7. open obligation이 premature transition을 막는다.
8. PX4 SITL `gz_x500` 기준 nominal missions와 fault injection replay가 돌아간다.
9. Gemini CLI가 normal mode에서 raw ROS browsing 없이 nominal supervisory mission을 수행 가능하다.

[1]: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md "Gemini CLI context and session model"
[2]: https://arxiv.org/html/2603.07670v1 "Conversational memory architecture discussion"
[3]: https://arxiv.org/html/2603.11768v1 "Memory governance and stability"
[4]: https://arxiv.org/html/2603.17831v1 "Embodied planning and lightweight belief interfaces"
[5]: https://arxiv.org/html/2504.21716v1 "Embodied robot memory overload and coordination"
[6]: https://modelcontextprotocol.io/specification/2025-06-18/server/tools "Model Context Protocol tools and structured content"
[7]: https://github.com/robotmcp/ros-mcp-server "ros-mcp-server repository"
[8]: https://docs.px4.io/main/en/flight_modes/offboard "PX4 Offboard mode requirements"
[9]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/src/plugins/command.cpp "MAVROS command plugin"
[10]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros_msgs/srv/SetMode.srv "MAVROS SetMode service"
[11]: https://docs.px4.io/main/en/sim_gazebo_gz/ "PX4 Gazebo GZ simulation"
[12]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/src/plugins/sys_status.cpp "MAVROS sys_status plugin"
[13]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/src/plugins/local_position.cpp "MAVROS local_position plugin"
[14]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/src/plugins/home_position.cpp "MAVROS home_position plugin"
[15]: https://raw.githubusercontent.com/mavlink/mavros/ros2/mavros/src/plugins/global_position.cpp "MAVROS global_position plugin"
