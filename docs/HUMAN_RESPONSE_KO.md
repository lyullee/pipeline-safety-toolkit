# 인적 반응 시간과 복구의 확률 연결

## 정보 오류와 반응 오류의 쌍 관측

`InformationResponseTrial`은 한 기회에서 관측한 정보 오류와 반응 오류를
각각 bool 또는 null로 보존한다. `InformationResponseStudy`에 사건 두 ID,
조건·기회 정의·모집단·관측 묶음 단위와 근거를 지정하고
`information_response_joint_counts(study, context)`를 호출한다. 반환되는
`JointStateCountTable`은 기존 `joint_states_from_counts`와
`fault_tree_request_from_joint_counts`에 연결한다. 정보 오류와 행동 오류의
상관을 독립 가정의 곱으로 바꾸지 않고 동일 시행의 조합으로 집계한다.

기본 `require_complete`는 null 결과가 하나라도 있으면 거부한다.
`reviewed_complete_cases`는 명시한 결측 검토 근거 하에서 완전 쌍만 사용하며
제외 시행 ID와 전체 원 입력을 source payload에 보존한다. 시행 ID 중복,
동일 사건 ID, 근거 누락과 완전 쌍이 없는 자료는 거부한다. 공개 요약에는
관측 원행을 넣지 말고 해당 payload는 원자료와 함께 별도 관리한다.
관측 비율은 인과효과·미관측 모집단 HEP·현장 전이 자격으로 해석하지 않는다.
점추정 쌍 집계 회귀 3개는 누리온 실행 대기다. 다음 절의 cluster bootstrap은
state별 점추정 구간만 제공한다. 결측 편향, 정보 품질의 인과효과와 실제 모집단 전이는
별도 분석이 남아 있다.

`pipeline_integrity.response_exchange`는 기존 `human-reliability-toolkit`의 시간·복구
계산과 이 패키지의 위험 교환 API를 연결한다. 원 엔진의 `LognormalTime`,
`WeibullTime`, `KaplanMeierTime`, `apply_deadline`, `response_bins`, `apply_recovery`를
호출한다. 여기서 분포 추정식·생존 계산·인적오류 계산 엔진을 복제하지 않는다.
원 엔진은 별도로 설치한다. 핵심 패키지의 계약 클래스는 원 엔진 없이도 읽을 수 있다.

## 사용

`examples/human_response.py`에는 독립 작성한 전체 입력 예제가 있다. 원 HRA의
`Estimate`와 `HumanFailureEvent`, 이 패키지의 `HumanResponseRequest` 및 `RunContext`를
함께 준비한다.

```python
from pipeline_integrity.response_exchange import HumanResponseClient

client = HumanResponseClient()
result = client.evaluate(initial_estimate, final_event, request, context)
if result.status == "exchange_checked":
    risk_input = result.risk_probability()
    assert client.verify_result(result)["valid"]
```

`verify_result`는 저장된 단일 deadline 또는 recovery 결과의 초기 owner 추정, 대상 사건,
요청, context를 다시 검증하고 현재 HRA owner API로 계산을 재생한다. 정상 digest만 맞는
변경 결과도 owner 재생과 다르면 거부한다. `verify_deadline_scenario_result`는 별도
scenario-set 집계 결과에 사용한다. 재생 성공은 동일 입력/현재 owner 응답 재현이며 현장
자격이나 원 관측의 진실성을 보증하지 않는다. 회귀 함수 1개는 추가 작성했으며 누리온
검증 전이다.

`final_event`는 최종 평가 대상이다. 초기 추정값은 이 사건에 시간 또는 복구 효과를
아직 반영하지 않은 중간 값이며 사건 ID와 conditioning은 일치해야 한다. 시간
평가에서는 최종 사건의 `includes_timing=True`, `includes_recovery=False`를 요구한다.
최종 실패 정의는 오반응과 기한 내 올바른 반응 미완료를 포괄해야 한다. 텍스트의
공학적 타당성은 분석자가 `applicability_rationale`와 근거로 검토한다.

`HumanResponseClient.cognitive_estimate(event, CognitiveResponse, context)`로 원 엔진의
`cognitive_chain`을 호출해 초기 추정값을 만들 수도 있다. 단계별 확률은 모든 앞
단계가 성공했다는 조건에서의 실패 확률이다. 서로 다른 단계 ID·순서·근거·모집단을
명시한다. 원 엔진이 반환한 첫 실패 구간과 전체 성공의 질량 합을 검사하고, 원
결과와 context 연결을 보존한다. 이 값을 다른 context에 옮겨 시간 효과를 적용하지
않는다. 이는 사용자 근거에 따른 조건부 분해이며 공식 IDHEAS의 정량 구현을 뜻하지 않는다.

## 시계와 기한

`ResponseWindow`는 같은 `clock_id`에서 단서를 사용할 수 있게 된 시각
`cue_time_s`와 공정 한계 시각 `limit_time_s`를 받는다. 둘 다 context 원점부터의
경과 초다. 사용 가능한 시간은 두 시각의 차이이며 양수여야 한다. 원 HRA 사건의
`deadline_s`도 이 **단서 이후의 기간**과 일치해야 한다. 시작 시각 5초·한계 시각
15초는 10초의 기한이다. 두 절대 시각을 모두 100초 이동해도 확률은 같다.

여기서는 공정 한계를 계산하거나 신호에서 단서 발생을 추정하지 않는다. 실제
물리 계산 또는 계측으로 얻은 한계는 해당 모델의 적용 범위·단위·시계와 함께
검토하여 넣는다. 관측하지 않은 한계를 알려진 값으로 가정하지 않는다.

일정 최적화의 예상 완료 시각은 인적 반응 시간의 분포가 아니다. 이 API는
계획 결과를 분포로 자동 변환하지 않으며 HRA TaskStep의 기한도 추측해 옮기지 않는다.

## 시간 모델과 관측 중단

`DeadlineEffect.time_model`에는 다음 중 하나를 선택한다.

| 모델 | 사용자 입력 | 의미 |
|---|---|---|
| LognormalResponse | median_s, log_sigma | 검토한 조건부 완료 시간 분포 |
| WeibullResponse | scale_s, shape | 검토한 조건부 완료 시간 분포 |
| EmpiricalResponse | trial ID, duration_s, outcome, 근거 | 원 HRA Kaplan–Meier의 우측 중단 처리 |

시간 모집단은 `given_no_initial_failure`다. `population_definition`에는 이 조건에서
무엇의 완료를 관측했는지 적는다. 단순히 모든 반응 시간을 섞은 자료를 넣으면
초기 실패와 시간이 중복될 수 있다. 파라미터와 모집단의 현장 적합성은 자동
확정되지 않는다. 이 경계에서 파라미터의 불확실성이나 신뢰구간을 전파하지 않는다.

경험 관측의 `correct_completion`은 완료 사건이고 `right_censored`는 그 시각까지
완료 여부를 더 관측하지 못한 우측 중단이다. 중단 시각을 완료 시각으로 바꾸지
않는다. 중단 가정은 `censoring_review_evidence_id`로 검토한다. 같은 시각의 중단과
완료에 대한 원 엔진의 위험 집합 처리를 유지한다.

`wrong_response`와 `unknown`이 있으면 `needs_data`이며 확률을 내보내지 않는다.
오반응은 일반적인 우측 중단으로 취급할 수 없다. 미확정 꼬리 밖을 요청하면
원 엔진의 거부 이유를 보존하고 결과를 보류한다. 기한 안의 확률을 계산할 수
있어도 별도로 요청한 bin이 미확정 꼬리 밖이면 전체 요청을 보류한다.

`bin_boundaries_s`는 단서 이후 경과 초의 오름차순 양수다. 원 엔진의 완료 구간과
미완료 분기를 그대로 보존하고 전체 질량 합이 1인지 검사한다. 첫 구간은 시각
0에서의 완료도 포함하며 후속 구간은 이전 상한 초과·현재 상한 이하에 해당한다.
마지막 미완료 분기에는 초기 실패도 포함되므로 이를 별도 실패 분기로 다시 더하지 않는다.

## 검토된 기한 변동 시나리오

기한이 하나의 고정값으로 적절하지 않을 때 `DeadlineScenarioSet`에 가능한
cue-to-limit 기간(초), 확률 가중치, 시나리오별 근거 및 분포 검토 근거를 넣고
`HumanResponseClient.evaluate_deadline_scenarios(initial, event, request,
scenario_set, context)`를 호출한다. `available_time_population_definition`에는
그 확률분포가 대표하는 요구/기회 모집단을 적는다. 각 행의 `probability_mass`는
양수여야 하며 합은 1이어야 한다. 함수는
각 기간에 기존 HRA 고정 기한 엔진을 호출하고 결과 확률의 가중 평균을 계산해
일반 `RiskProbability`로 연결한다. `verify_deadline_scenario_result`는 저장된
시나리오 계산과 집계를 실제 owner 호출로 재생한다.
기본 실행 예산은 256개 시나리오이며 `maximum_scenarios`로 조정할 수 있다. 경험
반응자료의 누적 행 수도 `maximum_empirical_observations`로 제한한다.

이는 사용자가 제공한 이산 분포의 직접 합산이다. 시나리오 사이를 보간하거나
분포를 추정하지 않는다. 각 행에서 `time_model`을 선택적으로 지정해 같은 공정
조건의 사용 가능 시간과 인간 반응 분포를 공동 시나리오로 묶을 수 있다. 이런 모델을
지정할 때는 `response_population_definition`도 함께 기록해야 한다. 의존성이 있다면
그 공동 분포·분류·probability_mass·근거는 분석자가 구성해야 하며 이 API가 추정하지
않는다. 미해결 시나리오가 하나라도
있으면 통합 확률은 나오지 않는다. 연속 분포, 상관관계·인과 구조, 기한 내 복구,
경쟁 실패는 이 API 범위가 아니며 PI-X06 후속 작업이다. 사례 가중치를 임의로
균등화하거나 완료 예상 시각에서 확률을 추론하지 않는다.

## 복구와 중복 반영

`RecoveryEffect`는 초기 오류가 발생했다는 조건에서 복구에 실패할 확률을 받는다.
현재 연결은 시간 제한 없는 복구만 지원한다. 최종 사건은 `includes_recovery=True`,
`includes_timing=False`, `deadline_s=None`이어야 한다. 기한이 있는 복구를 이 값으로
대체하지 않는다. 시간·복구가 이미 포함된 초기 추정값은 두 효과 중 어느 쪽으로도
재처리하지 않는다. 공동 시간·복구 모델은 PI-X06의 후속 요청이다.

## 위험 교환과 근거

최종 확률은 `HRARiskClient.bind_human_estimate`로 연결한다. 원 계산 결과는
`native_estimate`에 보존한다. 위험 입력에는 별도로 원 결과 해시·전체 요청·context
해시와 시계/모집단/적용성 근거를 덧붙여, 시간 해석이 떨어져 나가지 않게 한다.
사건·조건·요구·방호 ID를 유지하며 결과와 내보내기 전 무결성을 검사한다.
API에 이미 생성된 Python 객체를 넣어도 Python 타입을 보존해 재검증한다.

연간 횟수는 별도로 검토한 `DemandExposure`가 있을 때 기존 위험 API로 계산한다.
인적 반응 실패의 연간 기대 횟수는 물리적인 누설·파단 빈도가 아니다. 모든 새
결과는 research이며 현장 보정이나 작업 허가를 의미하지 않는다.

## 실행 근거와 이어가기

```powershell
python scripts/check_human_response.py --hra PATH_TO_HRA
python -m pytest -q tests/test_response_exchange.py
```

실제 원 HRA 실행은 `research/human_response_results.json`, 전체 입력·출력은 로컬
`artifacts/human_response`에 있다. 17개 사례에서 로그정규 중앙값, 지수분포의 한
척도 시각, 손으로 계산한 동시 중단/완료 위험 집합, 복구 확률의 양 끝을 비교한다.
예제의 초기 오류 0.2와 중앙값 시각의 완료 확률 0.5를 조합한 결과는 0.6이다.
우측 중단 예제는 4초에서 0.68, 6초에서 0.44이며 9초의 미확정 꼬리는 보류한다.
인지 두 단계의 조건부 실패 0.1·0.2는 원 엔진에서 초기 0.28이 되고, 중앙값
기한을 반영한 결과는 0.64다. 실제 FTA 입력과 기회 노출 2회/년의 연간 환산도
연결했다. 예제 0.6의 연간 기대 횟수는 1.2회/년이며 확률이나 누설 빈도가 아니다.

기존 공개 SACADA의 분리 불가능한 시간·복구 효과는 보존하고 재적용을 거부한다.
이 검사는 공개 훈련 결과를 배관 현장의 반응 확률로 보정했다는 뜻이 아니다.
실제 반응 자료는 올바른 시간 모집단·단서·실패·중단·현장 조건을 추가 확보해야 한다.

최근 방법 검토에서는 필요한 시간과 사용 가능한 시간의 변동을 모두 다루는
연구를 확인했다. 현재 원 엔진 경계는 검토한 고정 한계 시각에 대한 조건부 시간
모델이며, 어댑터는 호출자가 제공한 이산 기한 시나리오를 그대로 합산한다.
연속 분포, 공정과 사람 사이의 의존, 기한 내 복구, 경쟁 실패는 후속 범위로 남기고 PI-X06에 수용 조건을 적었다. 새로운 방법의
계수나 문장을 가져오지 않았다. [PNNL의 2024년 연구 소개](https://www.pnnl.gov/publications/estimating-contributions-human-error-probability-convolution-distribution-time)를
방법 선택의 비교 근거로 확인했다(2026-10-27).

## 쌍 관측의 표본 불확실성

`information_response_cluster_bootstrap(study, context, request)`는 네 가지
정보 오류/반응 오류 joint-state 확률 각각에 대한 점추정과 percentile 구간을 계산한다. 독립 complete cluster가 2개 미만이면 구간을 만들지 않고 거부한다.
독립 표본 단위를 `grouping_unit="trial"`로 선언하면 시행마다 재표집하고, 작업자·팀처럼
반복 관측이 묶이면 그 단위를 `grouping_unit`에 적고 모든 시행에
`sampling_cluster_id`와 cluster 정의 근거 `sampling_unit_evidence_id`를 명시한다. cluster를 복원 추출한 뒤 그 안의 완전한 오류 쌍을 함께
유지하므로 두 오류의 관측 결합을 독립 곱으로 치환하지 않는다.

```python
from pipeline_integrity.response_exchange import (
    InformationResponseBootstrapRequest,
    information_response_cluster_bootstrap,
    verify_information_response_cluster_bootstrap,
)

bootstrap = information_response_cluster_bootstrap(
    study,
    context,
    InformationResponseBootstrapRequest(
        replicates=4000,
        seed=2026,
        confidence_level=0.95,
        maximum_resampling_work=20_000_000,
    ),
)
verify_information_response_cluster_bootstrap(bootstrap, study, context)
```

결과 계약은 `trial_weighted_complete_case_joint_state_probability` 추정대상을 명시한다.
각 반복에서 cluster를 동일한 확률로 복원 추출하고, 뽑힌 시행 전체의 상태 건수를 합산해
분모로 나눈다. 따라서 이 추정량은 cluster별 비율의 단순 평균이 아니며, 크기가 다른
cluster에서는 큰 cluster가 시행 수만큼 비중을 갖는다. cluster가 표본추출 층이나 가중
설계의 일부인 경우 이 추정량이 모집단 설계에 맞는지 별도 검토해야 한다.

결과는 study·context digest, seed/요청, 독립 cluster 수, complete trial 수, 제외 시행,
NumPy 버전, RNG와 구현 SHA를 봉인한다. 상태마다 관측 건수와 구간 퇴화 표시도 기록한다.
관측 건수가 0인 상태는 모든 bootstrap 표본에서 0이므로 구간이 `[0, 0]`으로 퇴화한다.
이는 해당 상태의 불가능성이나 희귀 사건 상한을 뜻하지 않으며, 0보다 큰 위험을 배제하지
않는다. 희귀 사건 상한에는 별도 방법과 충분한 표본 설계가 필요하다. 기본 작업량은 `반복 수 × 독립 cluster 수` 2천만
이하이며 request에서 최대 5천만까지 명시할 수 있다. 각 joint-state 구간은 별도의
pointwise percentile interval이며 결과 계약의 `four_separate_pointwise_intervals`가 이를
고정한다. 네 구간을 동시에 만족하는 joint confidence region은 아니다. 점추정
JointStateCountTable은 앞 절의 기존 FTA 연결에서 따로 사용한다.

완전 사례 선택을 사용한 구간은 관측된 완전 쌍만을 대상으로 하며 결측 편향을 보정하지
않는다. cluster 정의가 잘못됐거나 표본 모집단이 실제 수요를 대표하지 않으면 구간도 그
한계를 물려받는다. 인과효과, 사람 간 이질성 모형, 공통원인 모수, 실제 HEP 보정과 현장
전이는 계산하지 않는다. 새 회귀 함수 4개는 작성했고 로컬에서는 실행하지 않았다.

## 쌍 상태 표본의 FTA 전파

`information_response_fault_tree_cluster_bootstrap`은 위 cluster 재표집을 기존
`HRARiskClient.fault_tree`로 이어 단일 Boolean top event의 점추정과 percentile 구간을
계산한다. 인수에는 study·context, 기존 `FaultNode`, 두 사건 ID에 대응하는 기존
`RiskProbability` 입력, 소유자 FTA client와 별도 작업량 예산을 준다. 입력 사건의
ID·조건·기회 정의·context·원본 source digest를 봉인하고, 각 표본에서 얻은 주변확률은
그 표본의 joint-state counts로부터 새 derived `RiskProbability`로 만든다. 따라서 표본별
주변확률과 joint 분포가 서로 맞으며, 상태별 구간 끝점을 조합하거나 독립 가정으로
대체하지 않는다.

FTA tree node 수 × 네 가능한 joint state 수 × (bootstrap 반복 수 + 관측 점추정 호출)을
`maximum_fault_tree_work`에 맞춰 제한한다. `maximum_tree_nodes`와 재표집 작업량도
별도로 제한한다. 단 하나라도 owner FTA가 withheld하면 전체 계산을 예외로 중단한다.
결과에는 tree·사건·study·context·provider·난수 seed·요청·구현 hash를 연결하고
`verify_information_response_fault_tree_cluster_bootstrap`로 재생한다. 구간은 해당
결과 receipt의 confidence level도 요청값과 같아야 하며, 요청과 어긋난 level을 receipt만 재봉인해 통과시킬 수 없다. Boolean top event 하나의 pointwise 구간이다. 여러 top event를 동시에 보장하지 않고,
FTA 구조 자체의 불확실성도 다루지 않는다. `RiskProbability` 사건 정의가 관측한 쌍의
Boolean 변수와 일치하는지는 호출자가 확인해야 한다. 결측 편향, 인과효과 또는 현장
전이는 보정하지 않는다. 회귀 두 개를 추가로 작성했으며 모두 누리온 대기다.
독립 보관하는 결과에는 시행 가중 complete-case 추정대상, grouping unit과 missing policy도
기록한다. provider가 `source_digest` SHA-256을 주면 `source_pinned`, 그렇지 않으면
`caller_declared`로 명시한다. 후자는 provider 구현 자체의 byte identity를 증명하지 않는다.
각 표본의 owner 반환 wrapper는 별도로 봉인 digest, FTA 요청과 입력 digest, context,
provider, derived top-event source lineage 및 원 native top probability가 서로 맞는지
검사한다. outer receipt를 다시 봉인해도 다른 FTA 요청의 응답을 끼워 넣으면 거부한다.
이 경계 회귀는 작성했으며 누리온 대기다.

## 결측 패턴 점검

`information_response_missingness_audit(study, context)`는 FTA나 bootstrap보다 먼저 두 응답
변수의 관측 상태를 기술적으로 점검할 때 쓴다. `complete_00/01/10/11`, 정보 오류만 미상,
반응 오류만 미상, 두 값 모두 미상의 일곱 패턴을 전체 건수로 집계한다. `grouping_unit`이
trial이 아니면 호출자가 선언한 cluster별 건수도 함께 반환하며, trial 단위에서는 대량의
가상 cluster 행을 만들지 않고 cluster 요약이 없다고 표시한다. 상태와 context, 구현 hash가
봉인되고 `verify_information_response_missingness_audit`로 재생할 수 있다.

이 보고서는 누락이 MCAR/MAR/MNAR 중 무엇인지 판단하거나, 선택 편향을 보정하거나, cluster의
독립성을 입증하지 않는다. 결측을 오류로 바꾸거나 대치하지 않는다. `reviewed_complete_cases`로
계산을 계속하더라도 이 보고서를 선택 근거 또는 HEP 추정치로 해석하면 안 된다. 세 개의
합성 회귀 함수를 추가했으며 실행은 누리온 대기다.

완전 사례만 계산하는 대신 주어진 기록에서 가능한 상태 범위를 보려면
`information_response_missing_outcome_bounds(study, context)`를 사용한다. 불완전 시행의
미상 Boolean을 관측값과 양립하는 모든 값으로 열어 두고, 전체 시행 수를 분모로 네 joint
state 비율의 하한·상한을 계산한다. 예컨대 정보 오류만 미상이면 해당 기록은 관측된 반응
값을 유지한 두 상태 각각의 상한에만 기여한다. 결측값을 대입하거나 결측 메커니즘을
가정하지 않는다.

```python
from pipeline_integrity.response_exchange import (
    information_response_missing_outcome_bounds,
    verify_information_response_missing_outcome_bounds,
)

bounds = information_response_missing_outcome_bounds(study, context)
verify_information_response_missing_outcome_bounds(bounds, study, context)
```

이 결과는 입력된 유한 기록의 부분 식별 범위다. 표본오차 구간, 선택 편향 보정, 모집단 HEP,
FTA 구조 불확실성이 아니다. 네 상태의 경계는 각각 별도로 계산되므로 경계값 조합이 하나의
동시에 달성 가능한 분포를 뜻하지 않는다. 확인 가능한 완전 사례가 없으면 폭이 넓어질 수
있으며, 이는 데이터가 허용하는 범위를 표시한다. 회귀 함수 두 개는 작성만 했고 누리온에서
실행하기 전까지 검증된 결과로 취급하지 않는다.

기존 FTA에 해당 결측 범위를 전파하려면
`information_response_fault_tree_missing_outcome_bounds`에 동일한 paired study/context,
두 사건에 맞는 `FaultNode`, `RiskProbability` 입력, 기존 `HRARiskClient` owner와 명시적
tree work budget을 전달한다. 함수는 가능한 네 Boolean 조합을 각각 owner FTA로 계산해
top-event truth table을 만들고, 각 불완전 시행에서 가능한 조합의 최소·최대 top-event 상태를
누적한다. 결과는 같은 입력과 owner를 사용해
`verify_information_response_fault_tree_missing_outcome_bounds`로 재생한다.

```python
from pipeline_integrity.response_exchange import (
    InformationResponseFaultTreeMissingOutcomeRequest,
    information_response_fault_tree_missing_outcome_bounds,
    verify_information_response_fault_tree_missing_outcome_bounds,
)

fta_bounds = information_response_fault_tree_missing_outcome_bounds(
    study,
    context,
    root,
    (information_error_event, response_error_event),
    risk_client,
    InformationResponseFaultTreeMissingOutcomeRequest(
        maximum_tree_nodes=1000,
        maximum_fault_tree_work=20_000_000,
    ),
)
verify_information_response_fault_tree_missing_outcome_bounds(
    fta_bounds, study, context, root,
    (information_error_event, response_error_event), risk_client,
)
```

경계는 입력된 시행에서 미상 결과를 어떻게 채워도 가능한 top-event 비율의 최저·최고다.
owner가 네 조합 중 하나라도 보류하거나 결정적 Boolean 결과를 주지 않으면 계산 전체를
중단한다. tree work는 네 owner 호출을 기준으로 제한한다. 이는 모집단 확률의 신뢰구간,
결측 보정 또는 FTA 구조·입력 확률의 불확실도가 아니다. 두 회귀 함수는 작성만 했고
누리온 실행 전이다. 저장된 결과는 네 입력 상태의 완전한 truth table, 관측된 top-event
건수, 모든 경계 건수/비율의 산술 일관성을 계약 수준에서 검사한다. 재생은 호출된 실제
owner 구현에 같은 tree/event/context를 다시 전달한다.

```python
from pipeline_integrity.response_exchange import (
    InformationResponseFaultTreeBootstrapRequest,
    information_response_fault_tree_cluster_bootstrap,
    verify_information_response_fault_tree_cluster_bootstrap,
)

fta_uncertainty = information_response_fault_tree_cluster_bootstrap(
    study,
    context,
    root,
    (information_error_event, response_error_event),
    risk_client,
    InformationResponseFaultTreeBootstrapRequest(
        replicates=1000,
        seed=20261029,
        maximum_tree_nodes=1000,
        maximum_fault_tree_work=20_000_000,
    ),
)
verify_information_response_fault_tree_cluster_bootstrap(
    fta_uncertainty,
    study,
    context,
    root,
    (information_error_event, response_error_event),
    risk_client,
)
```
