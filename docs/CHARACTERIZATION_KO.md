# 시험편 재료 특성의 원 모델 연결

`characterization.evaluate_characterization`은 시험편에 연결된 네 가지 재료
계산을 기존 Mat 엔진에 요청한다. 원 모델의 식·교차점 탐색·반복 변형 관계를
복제하지 않고, 시험편·재료 상태·위치·원 좌표·단위·검토 근거와 원
`MaterialJob`/receipt를 남긴다. 계산 성공은 시험 절차 승인이나 재료 모집단의
물성 검증을 뜻하지 않는다.

## 요청 계약과 단위

`CharacterizationRequest`의 필수 정보는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `method` | 아래 네 원 모델 중 하나 |
| `asset_id`, `specimen_id` | 연결 자산과 실제 또는 명시한 합성 시험편 |
| `material_condition_id` | 온도·가공·노출·채취 방향 등을 구분할 재료 상태 식별자 |
| `location_id` | 표점부·시편 위치 또는 평가 재료점 |
| `clock_id` | 원 시간 또는 정적 관측 순서의 명시적 식별자 |
| `parameters` | 원 모델의 입력 dict; 전체 또는 개별 값 미상은 `None` |
| `input_units` | `PARAMETER_UNITS[method]`와 정확히 같은 단위·분율·bool 기준 |
| `applicability_reviews` | 방법에 맞는 검토 키와 실제 검토 여부 |
| `review_evidence_ids` | 각 검토 키를 뒷받침하는 선택 근거 ID |
| `evidence_ids` | context에 실제 존재하는 중복 없는 근거 ID tuple |
| `maximum_branch_samples` | offset 분기의 최대 표본 수; 기본 100,000, 허용 설정 3~1,000,000 |

자산·시계는 `RunContext`와 같아야 한다. 검토 근거는 `evidence_ids`에,
선택 근거는 `context.evidence`에 존재해야 한다. 단위 문자열은 부분 집합이나
동의어를 허용하는 변환 지시가 아니다. 예를 들어 mm를 `extension_m`에 넣고
자동 환산을 기대하면 안 된다. 분율 0.2와 백분율 20을 구분한다.
`uniform_deformation_declared`의 기준 문자열은 `"bool"`이고 실제 값은
Python `True`/`False` 또는 JSON `true`/`false`여야 한다. 숫자 0·1이나
문자열로 선언을 대신하지 않는다.

시험편·상태 식별자는 근거를 추적하는 라벨이다. 이름만 같다는 이유로
다른 시험의 단면·신율·기준 상태가 일치했다고 자동 판단하지 않는다.
`clock_id`가 있다고 해서 불완전한 원 시계가 교정되거나 정적 점 자료가
시간 이력으로 바뀌지도 않는다.

## 네 원 모델의 입력과 해석

### `materials.tensile_point`

| 파라미터 | 기준 | 지원 의미 |
|---|---|---|
| `force_N` | N, 0 이상 | 인장 방향으로 검토한 힘; 선언한 0은 유지 |
| `initial_area_m2` | m², 양수 | 해당 시험편의 초기 단면적 |
| `extension_m` | m, 0 이상 | 원 표점 길이에 대응하는 연장량 |
| `gauge_length_m` | m, 양수 | 초기 표점 길이 |
| `uniform_deformation_declared` | 실제 bool | 균일 변형으로 true 변환을 해석할 수 있는지의 명시 선언 |

결과는 `engineering_stress_Pa`, `engineering_strain`이다. 균일 변형을
참으로 선언한 경우에만 `true_stress_Pa`, `true_strain`도 계산한다.
`False`는 알려진 비균일/미선언 상태이며 공학 응력·변형률 계산을 보류하지
않는다. 이 경우 true 결과 키를 만들지 않는다. `None`은 선언 자체가
미상인 값이므로 `needs_data`다.

기계 크로스헤드 이동과 표점 연장량을 같은 값으로 가정하지 않는다. 넥킹
뒤의 국부 단면·다축 응력·응력 삼축성이나 표점 밖 변형을 이 단순 점 변환이
해결하지 않는다. 원 힘·연장량의 음수를 절댓값으로 바꿔 인장 모델에 넣지 않는다.

### `materials.ductility_retention`

`baseline_elongation_fraction`은 양수인 기준 신율 분율이고,
`exposed_elongation_fraction`은 0 이상인 비교 신율 분율이다. 결과는
`ductility_retention_ratio`와 `relative_ductility_loss`다. 비교 신율이 더 크면
보존비가 1을 넘고 손실이 음수가 될 수 있으며 이를 자르지 않는다.

두 신율은 같은 정의와 대응 조건을 가져야 한다. 균일 신율·파단 신율·단면
수축률을 서로 대신하지 않는다. 시험편 형상·표점 길이·온도·변형률 속도·
채취 방향·노출 정의와 모집단의 대응이 없으면 적절한 baseline을 임의로
선정하지 않는다. 강종 이름이나 하나의 인장 곡선으로 노출 전후 짝을 만들지 않는다.

### `materials.offset_yield`

`engineering_strain`은 무차원 비음 벡터,
`engineering_stress_Pa`는 Pa 기준 비음 벡터다. 길이가 같고 적어도 3점이어야
하며 변형률은 원 선택 순서에서 엄격히 증가해야 한다.
`elastic_modulus_Pa`와 `offset_strain`은 모두 양수로 별도 근거가 필요하다.
예를 들어 0.2% offset의 분율 입력은 `0.002`다. 모델이 탄성계수를
자동 적합하거나 offset을 내장 규격값으로 공급하지 않는다.

원 엔진은 입력 분기에서 offset 교차를 포함하는 첫 인접 쌍을 사용하고,
`offset_yield_stress_Pa`, `intersection_strain`, `bracket_indices`를 반환한다.
시작점이 이미 교차 뒤이거나 기록 분기에 교차가 없으면 `needs_data`다.
기록 범위 밖 교차를 외삽하지 않는다.

반복·역전 변형률, 가열/제하·반전 구간을 지워 새 단조 곡선을 만들지 않는다.
원 스트레인 순서를 정렬하거나 같은 변형률을 평균하지 않는다. 명시적인
원 연속 분기 선택은 전체 원자료와 선택 경계·규칙을 함께 보존해야 한다.
표본 예산 초과는 입력 오류이며 자동 절단하지 않는다. 이 입력은 변형률
분기이지, 그 자체로 유효한 단조 시계의 시간 이력은 아니다.

### `materials.ramberg_osgood`

| 파라미터 | 기준 | 의미 |
|---|---|---|
| `stress_amplitude_Pa` | Pa, 0 이상 | 단축 반복 응력 진폭 |
| `elastic_modulus_Pa` | Pa, 양수 | 같은 재료 상태의 탄성계수 |
| `cyclic_strength_coefficient_Pa` | Pa, 양수 | 해당 반복 시험 모집단에서 보정된 계수 |
| `cyclic_strain_hardening_exponent` | 무차원, 양수 | 같은 보정의 반복 변형경화 지수 |

결과는 `elastic_strain_amplitude`, `plastic_strain_amplitude`,
`total_strain_amplitude`다. 진폭을 범위나 최대 응력으로 바꾸지 않는다.
안정화된 반복 응답의 계수를 단조 인장곡선의 경화 계수로 대체하지 않으며,
단조 항복값·인장 강도·경도에서 반복 계수를 자동 추정하지 않는다.
지지 응력 범위·재료 상태·온도·이력과 보정 모집단을 별도로 검토한다.
이 모델 하나는 히스테리시스 궤적·평균 응력 보정·누적 손상·수명을 계산하지 않는다.

## 검토 키와 미상 처리

| 방법 | 모두 필요한 `REVIEWS[method]` 키 |
|---|---|
| tensile point | `original_area_and_gauge_extension`, `tension_sign_and_deformation_interpretation` |
| ductility retention | `matched_specimen_conditions_and_elongation_definition` |
| offset yield | `explicit_monotonic_loading_branch`, `elastic_modulus_and_offset_basis` |
| Ramberg–Osgood | `cyclic_amplitude_and_calibrated_material_condition`, `parameter_and_stress_support` |

플래그가 참이어도 근거 ID가 없으면 보류한다. 필수 키 누락, 전체 입력 `None`,
벡터나 중첩 입력의 `None`도 `needs_data`이며 `native_receipt=None`이다.
결측을 0으로 채우거나 결측 행을 제거해 원 모델을 호출하지 않는다.
명시한 0과 `False`는 미상으로 취급하지 않는다.

잘못된 자산·시계·단위·근거·방법별 검토 키, 비유한 값, 원 모델의 형상·부호·
벡터 오류는 입력 오류다. 복제된 Pydantic 요청도 재검증하므로 `model_copy`
업데이트로 검증을 우회할 수 없다. 결과의 `status`, `issues`, 원 receipt를
함께 읽는다. `computed`는 전체 재료 승인이나 현장 모집단 검증의 판정이 아니다.

## Python·JSON 사용과 원 모델 고정

다음 예제는 핵심 설치만으로 실행된다. 입력 미상은 원 엔진을 가져오기 전에
보류하며, JSON roundtrip 뒤에도 시계와 bool/SI 계약을 유지한다.

```python
import json
from pipeline_integrity.characterization import (
    CharacterizationRequest,
    PARAMETER_UNITS,
    evaluate_characterization,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext

context = RunContext(
    asset_id="declared_specimen_asset",
    scenario_id="unknown_test_inputs",
    clock_id="source_order_without_invented_elapsed_time",
    origin="user_declared",
    evidence=(
        EvidenceRef(
            id="notes",
            citation="Caller-declared incomplete specimen record",
            origin="user_declared",
            license_id="MIT",
        ),
    ),
)
request = CharacterizationRequest(
    method="materials.tensile_point",
    asset_id=context.asset_id,
    specimen_id="declared_specimen",
    material_condition_id="condition_unresolved",
    location_id="gauge",
    clock_id=context.clock_id,
    parameters=None,
    input_units=PARAMETER_UNITS["materials.tensile_point"],
    evidence_ids=("notes",),
)
payload = json.loads(request.model_dump_json())
assert payload["parameters"] is None
restored = CharacterizationRequest.model_validate_json(json.dumps(payload))
held = evaluate_characterization(restored, context)
assert held["status"] == "needs_data" and held["native_receipt"] is None
```

완전한 독립 작성 기준은 `examples/characterization.py`의 `make_cases()`로 생성한다.
기존 Mat를 설치한 환경에서 다음을 실행한다. 예제의 기하·힘·분기·반복 계수와
참인 검토 플래그는 합성 기준이며 실제 자료에 그대로 옮기지 않는다.

```python
import runpy
from pipeline_integrity.characterization import evaluate_characterization

requests, context = runpy.run_path("examples/characterization.py")["make_cases"]()
results = {method: evaluate_characterization(req, context) for method, req in requests.items()}
assert all(record["status"] == "computed" for record in results.values())
assert results["materials.ductility_retention"]["values"]["relative_ductility_loss"] == -0.25
```

검토한 원 입력 스키마·버전·출력 단위까지 고정하려면 `MaterialJob`을 먼저
만들고 `pinned_job`에 전달한다. 다음 블록도 기존 Mat가 필요하다.

```python
import runpy
from pipeline_integrity.characterization import evaluate_characterization
from pipeline_integrity.material_exchange import prepare_material_job

requests, context = runpy.run_path("examples/characterization.py")["make_cases"]()
request = requests["materials.tensile_point"]
job = prepare_material_job(
    request.method,
    request.parameters,
    context,
    location_id=request.location_id,
    evidence_ids=request.evidence_ids,
)
record = evaluate_characterization(request, context, pinned_job=job)
assert record["status"] == "computed"
assert record["native_receipt"]["job"]["input_schema_digest"] == job.input_schema_digest
```

고정 job의 모델·자산·위치·시계·입력·근거가 요청과 다르거나 원 모델의
스키마·버전·단위·확률 의미가 바뀌면 기존 고정을 자동 갱신하지 않고 거부한다.
이 고정은 모델 계약의 고정이며 원 시험의 교정이나 파일의 공급자 체크섬
검증을 대신하지 않는다. 시험편/상태 라벨과 물리 근거의 정합도 호출자가
별도로 확인해야 한다.

결과는 plain JSON dict이며 요청·context·원 receipt와 `record_digest`를 담는다.
`context.origin`의 실제 관측 출처와 계산 결과를 구분한다. 현재 계산 결과의
`output_origin`은 `simulation`이고 `material_or_test_acceptance`,
`field_or_population_validation`,
`cyclic_parameters_fitted_from_monotonic_data`는 모두 거짓이다.

## 재현 증거와 확장 범위

```powershell
python scripts/check_characterization.py --materials PATH_TO_MAT
python -m pytest tests/test_characterization.py -q
```

전용 계약 검사는 기존 17개에 비유한 입력 재검증 3개를 더한 20개다.
[원 모델 실행 결과](../research/characterization_results.json)는 네 원 모델의
합성 기준을 11개 독립 산술 기준과 대조한다. 균일/비균일 변형의 true 출력
구분, 0 하중, 음의 연성 손실, offset 교차 없음과 비단조 분기 거부도 확인한다.
이는 시험편 측정이나 모집단의 독립 검증 수가 아니다. 공개 구리 인장 적용은
[COPPER_TENSILE_KO](COPPER_TENSILE_KO.md)를 따른다.

후속에는 대응된 baseline/노출 시험의 신율 정의·기하·조건·표본 계보,
넥킹 뒤 국부 단면/변형의 관측과 true 변환 지지 근거, 그리고 실제 안정화
반복 시험에서 보정하고 별도 모집단에 평가한 계수가 필요하다. 원 시계의
반복·역전 수정은 장치 원 기록·교정 근거와 수정 전후 행 매핑·불확실성을
갖춘 별도 프로토콜로 다뤄야 한다.

Mat 확장이 필요하면 원 시험편/단위/분기/지지 범위와 실패 재현 입력,
기대 상태·출력·검증 기준을 명시해 소유 프로젝트에 요청한다. 이쪽에서
offset 탐색·재료식·사후 넥킹 보정·반복 계수 추정기를 복제하지 않는다.
원자료·변환 행·외부 소유 코드는 코드 배포에 포함하지 않는다.

구체적인 후속 요청은 현재 지원과 구분해 다음처럼 제시한다.

- 넥킹 이후 해석: 관측한 국부 단면·힘·변형 측정 위치와 동기화·불확실성,
  균일 변형이 끝난 경계를 입력 근거로 받는 별도 계약이 필요하다. 그 근거가
  없으면 true 변환을 보류하고 단순 초기 단면 환산으로 복원하지 않는 검사를 요청한다.
- 반복 응답 보정: 안정화 반복 시험 ID·재료 상태·온도·진폭 정의·보정/평가
  모집단과 계수 지지 범위를 받는 소유 서비스가 필요하다. 단조 자료만 있거나
  범위를 벗어나면 보정 성공으로 표시하지 않는 사례를 인수 기준에 넣는다.
- 원 분기 지원 확대: 반복·역전 행을 포함한 전체 원 벡터와 명시한 원 행
  경계를 함께 받되 삭제·정렬 여부를 영수증에 남기는 계약을 요청한다.
  선택 범위 밖 교차의 외삽과 손상된 시계의 암묵 수정을 금지하는 기준도 포함한다.
