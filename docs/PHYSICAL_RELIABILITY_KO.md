# 물리 사건 빈도·생존 조건부 기간확률

`reliability_exchange`는 기존 Mat의 `reliability.gamma_poisson`과
`reliability.weibull_interval` 두 모델을 `MaterialJob`으로 호출한다. 원 소유자가
사후 강도 모멘트와 수명 구간 계산을 담당하고, 이 패키지는 사건·모집단·노출·
나이·시계·근거와 위험 입력의 의미를 보존한다. 새 추정기나 FTA·ETA·독립 gate를
추가하지 않는다.

이 묶음의 입력과 수치 기준은 독립 작성한 가상 사례다. 실제 배관 사고·노출
자료로 보정하거나 현장 빈도·수명을 검증한 결과가 아니다. 물리적 기능 상실,
봉쇄 상실, 사람의 요구당 실패와 사고 결과를 각각의 사건 정의로 유지한다.

## 재현과 호출 경계

저장소 루트에서 다음 명령을 실행한다. 기존 Mat는 별도 설치하거나 검증
스크립트의 `--materials`에 소스 루트를 제공한다. `PATH_TO_MAT`는 사용자
환경의 해당 프로젝트 루트다.

```powershell
python -m pytest -q tests/test_reliability_exchange.py
python scripts/check_physical_reliability.py --materials PATH_TO_MAT
```

첫 명령은 소유 엔진 없이 계약·위임·무결성·의미 보존을 검사한다. 두 번째는
실제 Mat 모델을 호출하고 실행 전후 소스 트리 해시를 대조한다. 결과는
[physical_reliability_results.json](../research/physical_reliability_results.json)에
집계한다. 원 요청 전체나 사고 관측 행을 공개 요약에 넣지 않는다.

세 진입점은 다음과 같다.

| Python API | 입력 | 반환 |
|---|---|---|
| `evaluate_physical_reliability(request, context, client=None)` | `PhysicalReliabilityRequest`, `RunContext`, 선택적 기존 `MaterialClient` | 원 요청·context·상태·값·단위·의미·전체 native receipt와 `record_digest` |
| `physical_frequency(record, mapping)` | 계산 기록과 `PhysicalFrequencyMapping` | 검토된 연간 평균 물리 사건 빈도의 `RiskFrequency`를 담은 binding 기록 |
| `physical_period_probability(record, mapping)` | 계산 기록과 `WeibullPeriodMapping` | 생존 조건부 `mission_probability`의 `RiskProbability`를 담은 binding 기록 |

반환값은 JSON으로 직렬화할 수 있는 dict다. `frequency`와 `probability` 필드도
각 위험 계약을 JSON 모드로 직렬화한 dict이며, 보류된 binding에서는 `None`이다.
기존 위험 계약 객체가 필요하면 각각 `RiskFrequency.model_validate_json(...)`,
`RiskProbability.model_validate_json(...)`으로 복원한다. 원 엔진을 교체하는
`client`도 원 스키마·버전·응답·문맥을 확인하는 `MaterialJob` 계약을 충족해야 한다.

## 공통 요청과 JSON

`PhysicalReliabilityRequest`의 공통 필드는 다음과 같다.

| 필드 | 의미와 조건 |
|---|---|
| `method` | `reliability.gamma_poisson` 또는 `reliability.weibull_interval` |
| `asset_id`, `location_id` | 평가 자산과 위치 식별자. 자산은 context와 같아야 한다. |
| `clock_id` | 평가 시계 식별자. context와 같아야 하며 기간 매핑에도 그대로 남는다. |
| `physical_scope_id` | 적용 부재·집단·물리 범위의 식별자. 두 위험 매핑의 범위와 같아야 한다. |
| `event_definition` | 무엇을 물리 사건/첫 고장으로 셌는지 정의한다. 일반 기능 고장과 실제 누설을 혼용하지 않는다. |
| `population_definition` | 공통 조건·집단·관측 대상을 정의한다. 다른 운전·정비·검출 조건의 단순 합산을 정당화하지 않는다. |
| `parameters` | 선택한 원 모델의 입력 dict. 필수 값의 미상 또는 누락은 보류한다. |
| `applicability_reviews` | 아래 방법별 검토 키와 bool 값. 해당 방법의 모든 검토가 필요하다. |
| `evidence_ids` | 중복 없는 한 개 이상의 근거 ID. 모두 `RunContext.evidence`에 있어야 한다. |

JSON에서는 `evidence_ids`를 배열로 표현하고 Python에서는 tuple을 사용한다.
알 수 없는 공통 필드와 다른 방법의 검토 키는 오류다. JSON의 `null`은 미상이며
0으로 치환하지 않는다. 원 숫자의 유효 범위와 필수/선택 스키마는
`MaterialClient.describe(method)`에서도 조회할 수 있다.

다음은 독립 작성한 gamma–Poisson 요청의 완전한 JSON 예다. 같은 자산·시계와
`authored` 근거를 가진 context를 별도로 전달해야 한다.

```json
{
  "method": "reliability.gamma_poisson",
  "asset_id": "authored_component",
  "location_id": "authored_segment",
  "clock_id": "scenario_elapsed_s",
  "physical_scope_id": "authored_component_population",
  "event_definition": "First physical loss of the declared component function",
  "population_definition": "Authored homogeneous physical components under unchanged conditions",
  "parameters": {
    "prior_shape": 2.0,
    "prior_exposure_s": 31557600.0,
    "confirmed_events": 3,
    "exposure_s": 315576000.0,
    "complete_ascertainment": true
  },
  "applicability_reviews": {
    "common_physical_event_population_and_constant_intensity": true,
    "confirmed_counts_complete_ascertainment_and_exposure": true,
    "prior_shape_and_exposure_basis": true
  },
  "evidence_ids": ["authored"]
}
```

JSON을 읽는 API는 `PhysicalReliabilityRequest.model_validate_json(document)`다.
다음 예는 Mat 없이 실행되며 미상 노출이 계산 전에 보류되는 것을 확인한다.

```python
import runpy

from pipeline_integrity.reliability_exchange import (
    PhysicalReliabilityRequest,
    evaluate_physical_reliability,
)

make_cases = runpy.run_path("examples/physical_reliability.py")["make_cases"]
cases, _, _, context = make_cases()
document = cases["reliability.gamma_poisson"].model_dump_json()
restored = PhysicalReliabilityRequest.model_validate_json(document)
unknown = restored.model_copy(update={"parameters": restored.parameters | {"exposure_s": None}})
held = evaluate_physical_reliability(unknown, context)
assert held["status"] == "needs_data" and held["native_receipt"] is None
print(held["status"], held["issues"])
```

## 사건 수·노출과 gamma–Poisson

| 원 입력 | 단위·범위 | 검토할 내용 |
|---|---|---|
| `prior_shape` | 무차원, 양수 | 사전분포 shape의 근거와 사건 정의 |
| `prior_exposure_s` | s, 양수 | 사전분포 rate 파라미터의 시간 기준. 관측한 새 노출과 구분한다. |
| `confirmed_events` | 사건 수, 0 이상의 정수 | 확정 사건과 중복·재발·집단의 집계 규칙. bool은 사건 수가 아니다. |
| `exposure_s` | s, 양수 | 해당 집단의 실제 위험 노출. 여러 부재의 노출을 합쳤다면 그 분모를 명시한다. |
| `complete_ascertainment` | bool | 사건의 완전 포착을 검토했는지 선언한다. `True`가 아니면 보류한다. |

필요한 검토 키는 `common_physical_event_population_and_constant_intensity`,
`confirmed_counts_complete_ascertainment_and_exposure`,
`prior_shape_and_exposure_basis`다. 집단과 공통 사건 정의, 일정 강도 가정,
확정 건수·노출·완전 포착, 사전분포의 기준을 검토한다. 누락·미검출이 있는
집계에는 별도 관측 모델이 필요하며 미상 사건 수를 0으로 취급하지 않는다.

원 출력은 다음과 같다.

| 출력 | 단위 | 의미 |
|---|---|---|
| `posterior_shape` | 1 | 명시한 사전분포와 확정 사건을 반영한 사후 shape |
| `posterior_exposure_s` | s | 사전 rate 파라미터와 새 관측 노출을 반영한 값 |
| `mean_frequency_per_s` | 1/s | 사후 사건 강도의 평균 |
| `variance_frequency_per_s2` | 1/s² | 사건 강도에 대한 사후 분산 |

0건 관측은 0의 사후 평균을 뜻하지 않는다. 양의 사전분포는 그대로 유지되며
새로운 노출이 사후분포를 갱신한다. 예제에서는 shape 2·사전 노출 1 Julian year와
10년 노출을 선언했다. 3건이면 사후 shape는 5, 환산 평균은 5/11건/년이다.
같은 노출에서 0건이면 사후 shape는 2이고 환산 평균은 2/11건/년이다.
이 숫자는 가상 입력의 기준값이며 배관 사고 빈도 자료가 아니다.

`mean_frequency_per_s`는 미래 구간에 한 번 이상 발생할 확률과 다르다.
평균 강도에 기간을 곱한 값은 일정 강도·노출 가정 아래의 기대 사건 수다.
이는 1을 넘을 수 있다. 강도의 사후분포를 적분한 예측 발생확률은 0~1의
다른 통계량이고, 사후 평균을 고정 강도로 대입한 Poisson 확률과도 일반적으로
다르다. 현재 원 두 모델과 이 어댑터는 gamma 사후 예측 발생확률을 계산하지 않는다.
`posterior_predictive_occurrence_probability_computed=False`를 유지한다.

이 모듈은 관측 분포에서 사전분포를 자동 적합하거나 미검출 보정·구간별 변동
강도·공통 원인·수리·재발 과정을 추정하지 않는다. 그런 가정이 필요한 경우
새 관측·모델 근거와 소유 엔진의 명시적 지원을 확보해야 한다.

## 평균 강도의 명시적 연 환산

`PhysicalFrequencyMapping`은 다음 필드를 받는다.

| 필드 | 내용 |
|---|---|
| `event_id`, `physical_scope_id` | 내보낼 사건과 원 물리 범위. 범위는 원 요청과 같아야 한다. |
| `condition_id`, `conditioning` | 환산 결과가 적용되는 조건의 ID와 설명 |
| `endpoint` | `event_occurrence` 또는 `loss_of_containment` |
| `seconds_per_year`, `year_basis_id` | 양의 초/년 값과 그 기준 식별자. 기본 연 길이는 없다. |
| `expected_source_digest` | 검토한 원 계산의 64자리 SHA-256 `record_digest` |
| `event_scope_endpoint_and_condition_reviewed` | 사건·범위·물리 endpoint·조건을 검토했는지 선언 |
| `constant_intensity_projection_reviewed` | 관측 집단의 강도를 대상 노출에 투영할 수 있는지 검토 |
| `year_basis_reviewed` | 명시한 연 기준과 단위 환산을 검토 |
| `evidence_ids` | context에 존재하는 중복 없는 매핑 근거 |

`physical_frequency`는 원 단위가 `1/s`인 평균 강도에 선언한 초/년을 곱한다.
365일 기준은 31,536,000초이고 365.25일 Julian year는 31,557,600초다.
어느 값을 사용할지는 근거와 함께 선언하며 식별자 문자열만으로 코드가
달력·운영 노출의 타당성을 판정하지 않는다. 일정 강도에 대한 검토가 미래의
운전·정비·검출 조건 변화까지 자동 보장하는 것도 아니다.

`loss_of_containment`는 실제로 그 사건을 세고 그 노출을 확보한 경우에만
검토할 수 있다. 일반 기능 고장이나 수동 대응 실패의 명칭만 바꿔 누설 빈도로
내보내지 않는다. 빈도 계약에는 `consumed_event_ids`, 원 근거와 전체 원 계산이
남는다. `observed_annual_frequency`와 `field_validation_claim`은 거짓이며,
표시값은 사후 평균 강도의 환산 결과다.

## Weibull의 나이와 생존 조건

| 원 입력 | 단위·범위 | 의미 |
|---|---|---|
| `shape` | 1, 양수 | 검토한 수명분포의 형상 파라미터 |
| `scale_s` | s, 양수 | 같은 수명분포의 시간 척도 |
| `current_age_s` | s, 0 이상 | 대상이 첫 고장 없이 생존한 현재 나이 |
| `horizon_s` | s, 양수 | 그 시점 이후의 평가 기간 |

필수 검토 키는 `first_failure_population_shape_scale_and_age_origin`,
`survival_condition_and_interval_support`다. 원 수명분포의 모집단과 파라미터·
나이 원점, 평가 시작의 생존 조건과 적용 기간을 검토해야 한다. 이 API는
주어진 shape·scale로 평가하며 고장·검열 관측으로 이를 적합하지 않는다.

출력 `probability_given_survival`은 시작 나이까지 첫 고장이 없었다는 조건에서
이후 구간 안의 첫 물리 고장 확률이다. `probability_semantics`의 원 값
`conditional_first_failure_in_period`를 보존한다. `integrated_hazard`는 해당
구간의 누적 hazard이며 `equivalent_hazard_per_s`는 이를 구간 길이로 나눈 값이다.
이 값을 반복 사건 강도나 gamma 사후 평균과 같은 통계량으로 사용하지 않는다.

예제의 shape 2·scale 100초·현재 나이 50초·이후 50초는 누적 hazard 0.75,
확률 약 0.52763345다. 나이 0에서 같은 50초를 평가하면 약 0.22119922다.
기존의 생존을 조건에 포함했기 때문에 단순히 새 50초 수명분포로 나이를
초기화할 수 없다. 수리 후 나이 초기화·부분 복구·경쟁 고장·동시 불가용도는
현재 모델의 추가 기능으로 추정하지 않는다.

`WeibullPeriodMapping`에는 `event_id`, `physical_scope_id`, `endpoint`,
`condition_id`, `conditioning`, `period_id`, `period_definition`, `period_start_s`,
`period_finish_s`, `age_origin_s`, `expected_source_digest`, 두 검토 플래그와
`evidence_ids`를 제공한다. endpoint는 `first_physical_failure` 또는
`first_loss_of_containment`다. 검토 플래그는 `event_scope_and_period_reviewed`와
`survival_and_age_origin_reviewed`다.

시각은 원 context의 `clock_id`에서 경과 초다. `period_finish_s`는 시작보다
커야 하며 `age_origin_s`는 시작보다 늦을 수 없다. 이미 나이 든 설비를 시계
0에서 다루기 위해 나이 원점은 음의 유한한 값일 수도 있다.
`period_start_s - age_origin_s`가 원 `current_age_s`,
`period_finish_s - period_start_s`가 원 `horizon_s`와 같아야 한다.
숫자 대조에는 상대 1e-12·절대 1e-9초 허용오차를 적용한다. 모든 시각을 같은
양만큼 이동하면 나이와 기간확률은 그대로다.

## 원 receipt·pin과 실제 Mat 호출

기존 Mat가 import 가능한 환경에서 다음 예를 실행한다. 첫 `make_cases()`가
반환하는 두 매핑 템플릿에는 원 계산 해시가 아직 없다. 가상 사례를 계산하고
해당 원 기록을 검토한 뒤 `expected_source_digest`를 연결한다. 실제 자료에서는
예제의 검토 선언과 근거를 적용 조건에 맞게 새로 작성해야 한다.

```python
import runpy
import sys

from pipeline_integrity.reliability_exchange import (
    evaluate_physical_reliability,
    physical_frequency,
    physical_period_probability,
)

sys.dont_write_bytecode = True
make_cases = runpy.run_path("examples/physical_reliability.py")["make_cases"]
cases, frequency, period, context = make_cases()
count = evaluate_physical_reliability(cases["reliability.gamma_poisson"], context)
life = evaluate_physical_reliability(cases["reliability.weibull_interval"], context)
frequency = frequency.model_copy(update={"expected_source_digest": count["record_digest"]})
period = period.model_copy(update={"expected_source_digest": life["record_digest"]})
annual = physical_frequency(count, frequency)
mission = physical_period_probability(life, period)
assert annual["status"] == mission["status"] == "computed"
print(annual["frequency"]["annual_frequency_per_year"])
print(mission["probability"]["measure"], mission["probability"]["probability"])
```

`evaluate_physical_reliability`의 schema는
`pipeline_integrity.physical_reliability.v1`이다. `request`, `context`, `values`,
`output_units`, `probability_semantics`, `native_receipt`, `status`, `issues`,
`statistic`, 구현 해시와 `record_digest`를 보존한다. 원 모델은 strict JSON
경로로 호출한다. `native_receipt`에는 실제 원 모델 버전·스키마 해시·단위·
확률 의미·입력·context·원 payload와 각각의 무결성 정보가 있다.

두 binding의 schema는 `pipeline_integrity.physical_frequency_binding.v1`,
`pipeline_integrity.physical_period_binding.v1`이다. `source_record_digest`,
`mapping`, 상태·문제 목록, 위험 계약과 binding 자체의 `record_digest`를 담는다.
내보낸 위험 계약의 `source_payload`에는 전체 `prediction`과 `mapping`이
남으며 `source_digest`와 기존 위험 계약의 `binding_digest`도 유지된다.

pin이 미상이면 `needs_data`이고 다른 기록을 가리키면 오류다. 계산 값·단위·
의미·문맥과 원 `MaterialReceipt`를 다시 비교하므로 바깥 기록의 해시만 다시
계산해 원 receipt와 다른 값을 통과시킬 수 없다. 해시와 검토 플래그는 출처와
선언을 추적하는 수단이며 입력 사실의 진실성이나 승인 서명을 자동 보증하지 않는다.

## HRA·기간 위험과의 연결 한계

Weibull 결과는 `role="equipment"`, `source_kind="derived"`,
`qualification="research"`, `measure="mission_probability"`로만 내보낸다.
요구당 실패확률이나 시점 불가용도로 바꾸지 않는다. 기존
`HRARiskClient.annualize`는 이 기간확률에 `DemandExposure`를 붙여 HEP처럼
환산하는 요청을 보류한다. gamma의 평균 물리 강도도 사람의 작업 실패확률이 아니다.

공정 사건이 어떤 요구를 만들고 사람이 어떤 조건에서 대응하는지 확인하려면
조건 ID·부모 경로·요구 정의·이미 적용한 방호와 시간/복구 효과를 별도로
연결해야 한다. 그 논리는 기존 [RISK_EXCHANGE_KO](RISK_EXCHANGE_KO.md)와
[HUMAN_RESPONSE_KO](HUMAN_RESPONSE_KO.md)의 엔진을 사용한다.

이 모듈은 [PERIOD_RISK_KO](PERIOD_RISK_KO.md)의 `PeriodEventBinding`을 자동
생성하거나 공통 원인·시간 순서·복구를 계산하지 않는다. 공통 기간으로 묶을
때에는 원 기간·나이·생존 조건·자산·물리 endpoint와 공동 조건 매핑의 근거를
유지해야 한다. 같은 해시나 사건이름만으로 독립성을 선언하지 않는다.

## 상태·검증 증거·향후 기여

필수 검토 누락, 필수 파라미터 미상/누락, 불완전 사건 포착은 소유 엔진을
불러오기 전에 `needs_data`다. 매핑 단계의 미검토·pin 미상·미계산 원본도
빈도/확률을 내보내지 않는다. 잘못된 자산·시계·근거·방법, 범위나 pin 불일치,
원 receipt 변조와 나이/기간 불일치는 오류다. 음의 건수·bool 건수·0 이하
노출/사전 shape/Weibull shape·비양의 평가 기간 등 원 입력 오류는 원 스키마가
거부한다. 소유 모델이 보류한 상태와 notes도 그대로 보존한다.

실제 원 두 모델의 9사례와 18개의 독립 작성 산술 기준, 6보류와 7개 invalid
입력 거부를 확인했다. 기준에는 사전분포를 유지한 0건, 나눈 노출의 사후 갱신,
서로 다른 연 기준, 새/기존 나이, 인접 구간의 조건부 생존 곱과 시계 이동이
포함된다. 작은 사후 분산도 영으로 대체해 통과하지 않도록 산술 비교의 절대
허용오차는 0이고 상대 허용오차는 1e-12다. 33개 자동 검사는 위임·보류·범위·
단위·pin·중첩 receipt 변조와 요구당 확률 혼용 거부를 확인한다. 원 Mat 소스
트리의 실행 전후 해시가 같으며 세부 집계와 구현/예제/스크립트 해시는 공개
연구 JSON을 따른다.

현재 이 묶음에는 실제 사고·노출·수명 관측이 없다. 기존 공개 공정 파형,
실험 고장 레이블과 반복 검사 행 수를 운영 설비의 사건 수·노출시간으로
변환하지 않는다. 실제 적용에는 설비·모집단·사건 endpoint·가동/위험 노출·
완전 포착·중복/재발·검열/퇴역·운전/정비 조건과 사전분포 근거가 필요하다.

다음 기여는 소유 경계를 유지하면서 진행한다.

- 자료 판독기는 사건 ID·부재/집단·원 단위·노출 정의·포착/검열·권리·해시를
  보존하고, 미지의 노출·미검출이나 반복 관측을 임의의 독립 시행으로 채우지 않는다.
- Mat 소유 API에는 필요한 경우 검출 불완전성·변동 강도·사후 예측·파라미터
  불확실성의 의미와 지원 조건을 버전 있는 입력/출력으로 요청한다. 평균 강도를
  예측확률로 바꾸는 기능을 이 연결부에 숨겨 추가하지 않는다.
- 공통 기간 바인딩은 기존 `period_risk`에 원 Weibull 나이·기간·조건·endpoint를
  확인하는 수신 계약을 추가하는 방식으로 검토한다. 논리 엔진을 복제하지 않는다.
- 물리 빈도와 HRA/사고 결과의 결합은 PI-X02/04/06의 기존
  [확장 요청서](EXTENSION_REQUESTS_KO.md)를 따른다. 명시한 물리 사건·요구·노출·
  대응 효과의 실제 왕복과 중복 거부가 수용 기준이며, 새로운 현장 자료와
  독립 평가 없이 보정·위험 정확성·승인을 완료로 표시하지 않는다.
