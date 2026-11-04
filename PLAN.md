# dualflow_3 실험 계획 (초안, 2026-11-04 연구미팅 피드백 반영)

> 코드와의 대응: 조건 B/G/N/NG = `protocol.CONDITIONS`, ε = `proposal.epsilon`, 후보 공간 = `environment.py`,
> 시드 task 24개 = `tasks.SEED_TASKS`, 실행 = `experiments/run.py`, 집계 = `experiments/analyze.py`.

## 0. 왜 v2를 그대로 못 쓰는가

미팅의 핵심 지적: "Planner가 d(자연어)만 주니까 문제인데, 그냥 d와 ARS(action, resource, scope)를
같이 주면 되지 않나? 그러면 뭐가 문제냐."

v2 코드에 이미 그 답이 있다. `experiments/canonical/structured_intent_control.py`
(`results/canonical/derived/structured_intent_control_output.txt`):

| 전달 방식 | unsafe | transmitted ≠ intended |
|---|---|---|
| d만 (No Verification) | 310/640 | 135/640 |
| ARS 전달 + Authorization만 | **0/160** | **0/160** |

즉 **현재 벤치마크(액션 4개, 파일 2개, 수신자 1~2개, Planner가 C_task를 전부 봄)에서는 ARS를
전달하면 오류가 0**이다. 문제가 d의 모호성에서 왔기 때문에 ARS를 주는 순간 문제가 사라진다 —
교수님이 말한 "모순". 그래서 v3는 문제 정의부터 바꾼다.

## 1. 새 문제 정의

- 아키텍처: Planner는 Executor에게 **d + ARS**를 함께 준다 (이게 기본 전제. 기여 1).
- 문제: Planner가 ARS를 **분해하는 것 자체가 불완전**하다 → 오류 ε. 그리고 멀티스텝 위임에서
  step k의 ARS가 step k−1의 결과에 의존하므로 **ε가 증폭**된다.
- 목표: ε를 줄이는 프로세스(Planner–Executor 간 주고받기) + 그래도 남는 ε를 막는
  두 검증기(Semantic Verifier, Authorization Verifier)로 최종 실행을 게이트.

v2에서 ε>0의 증거는 이미 있다: `experiments/schema_free/` F1의 `crm_wrong_recipient` —
d와 6개 contract 전부 잘못된 수신자(marketing_partner)를 냈고, 원인은 Planner의 컨텍스트 해석
("partner"라는 단어 큐). 이런 **공유 오류**는 Executor가 되물어도 못 잡고, 검증기가 필요하다.

### ε의 정의 (측정 가능하게)

세 개의 ARS 튜플:

| 기호 | 뜻 | 누가 만드나 |
|---|---|---|
| o* | 의도된 작업 (ground truth, 평가 전용) | 실험자가 고정 |
| ARS_P | Planner가 사용자 요청에서 분해해 전달한 튜플 | Planner LLM |
| ARS_E | Executor가 d + ARS_P를 받아 최종 실행하려는 튜플 | Executor LLM |

- ε_P = [ARS_P ≠ o*] : 분해 오류 (Planner 책임)
- ε_E = [ARS_E ≠ ARS_P] : 전달·해석 오류 (Executor 책임)
- ε = [ARS_E ≠ o*] : 최종 오류. 필드 단위로도 센다 (action/resource/scope 중 틀린 개수 0–3).
- 멀티스텝: 체인 길이 L에서 step별 ε_k와 누적 unsafe.

## 2. v2 세팅에서 ε=0이었던 이유 → 세팅을 바꾸는 세 가지 조작

이 셋이 v3의 실험 축이다. **Phase 0 파일럿에서 ε_P>0가 실제로 나오는지 먼저 확인**하고,
안 나오면 세팅을 조정한다 (ε=0이면 논문의 문제가 없는 것).

1. **후보 공간 확대.** 액션 4 → 8~10개 (summarize / extract / anonymize / archive_copy /
   send_external / delete / overwrite …, 의미가 비슷한 쌍 포함). 파일 2 → 6~8개 (이름이 비슷한
   것 포함: q3_raw / q3_redacted / q3_draft / q3_final …). 수신자 2 → 4개 (설명이 겹치는 것).
   스코프에 교수님 비유처럼 기간·부서 차원을 넣을지는 선택 (예: `period`, `section`).
2. **Planner가 C_task를 통째로 보지 않는다.** 사용자 요청(자연어) + 비공개 컨텍스트에서 **스스로**
   d와 ARS를 만든다 (v2 runtime의 `PlannerAgent.delegate` 활용). C_task/o*는 평가 전용.
   → `experiments/schema_free/tasks.py`의 24 tasks(sales/crm/ops/reports) 구조를 그대로 재사용 가능.
3. **멀티스텝 체인.** L = 1, 2, 3 (예: read → anonymize → send_external). step k의 resource는
   step k−1의 산출물. ε 증폭 측정용.

## 3. 비교 조건 — 2×2로 단순화

| | 게이트 없음 | 게이트 있음 (Semantic + Authorization Verifier) |
|---|---|---|
| **협상 없음** (d+ARS 원샷 전달) | **B** — 교수님이 말한 "심플 해법". 기준선 | **G** — v2 DualFlow를 ARS 전달 위에 올린 것 |
| **협상 있음** (ε-최소화 루프, 최대 R라운드) | **N** | **NG** — 제안 방법 |

추가로 **A0: d만 전달** (v2 재현)은 참고용으로만 한 번 돌린다. Consistency-based Gate 비교는
v2에서 이미 끝났으므로 뺀다.

- **협상 루프**: Executor가 d와 ARS_P를 대조해 불일치하거나 미결정인 **필드**를 지목해 되묻고
  (`propose_or_ask`의 facet 출력 재사용), Planner가 해당 필드만 확정. 일치하면 종료. R = 2.
- **Semantic Verifier**: v2 Semantic Flow와 동일 원리 — ARS_E가 Planner의 독립 재진술과
  일치하는가. 재진술 횟수 N은 v2의 20 대신 5로 줄이고, N 민감도는 부차 분석으로.
  (공유 오류를 잡으려면 재진술에 d가 아니라 **사용자 요청+컨텍스트**를 보여야 함 — v2 F2의
  E1/E2 구분과 같은 논점. v3 본 실험은 E1 한 가지로 고정.)
- **Authorization Verifier**: v2 그대로 (π, σ 결정적 검사).

## 4. 측정

- v2의 4 outcome 유지: Unsafe / Misaligned / False Rejection / Correct (합 = 전체).
- task_completed (o*가 실제 실행됐는가) — 막기만 하고 못 끝내면 성공이 아님.
- ε_P, ε_E, ε (튜플·필드 단위), 조건별·step별.
- 비용: LLM 호출 수, 토큰, 협상 라운드 수.
- 멀티스텝: L에 따른 ε 곡선 (증폭 여부).

## 5. 가설

- H1 (전제): 확대된 세팅에서 B의 ε_P > 0, unsafe > 0. → **Phase 0에서 먼저 확인**.
- H2: B에서 ε는 L에 따라 단조 증가한다 (증폭).
- H3: 협상(N)은 ε_E는 줄이지만 ε_P(공유 오류)는 줄이지 못한다 → 게이트가 필요한 이유.
- H4: NG는 unsafe를 0에 가깝게 하면서 task_completed를 G보다 높인다 (게이트만 있으면 False
  Rejection으로 끝나는 케이스를 협상이 살린다).

## 6. 단계

| Phase | 내용 | 규모 (대략) |
|---|---|---|
| 0 파일럿 | 1 도메인, L=1, 조건 B만. ε_P>0 확인. 후보 공간 크기 튜닝 | ~100 호출 |
| 1 단일스텝 | 4 도메인 × 24 task × 2×2 조건, L=1. 모델 1개 (Planner gpt-4o-mini, Executor gpt-4.1) | 수천 호출 |
| 2 멀티스텝 | L=2,3 체인 task 추가 (도메인당 3~4개), 2×2 | 수천 호출 |
| 3 모델 비교 | Executor 4모델 (v2와 동일), NG와 B만 | |

Phase 1 전에 PREREGISTRATION.md 고정 (v2 관행 유지).

## 7. 단순화 체크리스트 (교수님 요청)

- 용어: Semantic Flow → **Semantic Verifier**, Authorization Flow → **Authorization Verifier**.
  "Flow"는 버림. ARS는 그림·표에 action / resource / scope로 풀어 쓴다.
- 그림 1장: `Planner —(d, ARS)→ Executor ⇄ (되묻기 R회) → [Semantic V] ∧ [Authorization V] → 실행`.
  입력이 어디서 오는지(사용자 요청, 컨텍스트, π/σ) 화살표로 한눈에.
- 재진술 N=20 최빈값 → N=5 기본, 20은 민감도.
- 비교군 5개 → 2×2 + 참고 1개.
- v2 코드 재사용: `flows.py`(두 검증기), `runtime.py`(episode·sandbox·recovery), `roles.py`
  (`delegate`, `propose_or_ask`, `restate`), `outcomes.py`, `schema_free/tasks.py`.
  새로 쓰는 것: 확대된 runtime context, 체인 task, 협상 recovery policy, ε 계산.

## 8. 결정이 필요한 것

1. 사용자 요청 자연어를 새로 쓸지, schema_free 24 task를 그대로 쓸지 (후자 추천: 이미 라벨 있음).
2. 스코프 확장 여부 (internal/external만 vs 기간·부서 추가).
3. 협상 라운드 R (2 추천) 와 재진술 N (5 추천).
4. 멀티스텝에서 "이전 step 결과"를 실제 sandbox 파일로 줄지, 텍스트로만 줄지.
