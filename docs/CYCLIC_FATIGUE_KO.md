# 반복 하중의 진폭·경로 일·변형률 수명 교환

`pipeline_integrity.cyclic_fatigue`는 원 재료 엔진의 `fatigue.goodman`,
`fatigue.hysteresis_path`, `fatigue.strain_life`를 호출한다. 이쪽에는 재료식,
수치 근찾기, 경로 적분 또는 rainflow 구현을 두지 않았다. 원 모델의 실제
입력 스키마를 `MaterialJob`으로 고정하고 `MaterialReceipt`를 확인한다.
사용자가 제공한 재료 조건과 근거를 유지하는 연구용 계산 경계다.

기존 `fatigue_exchange`의 압력 이력·rainflow·손상 워크플로와
`characterization`의 `materials.ramberg_osgood`는 그대로 재사용한다.
Ramberg–Osgood 응답, Goodman 보정, 변형률 수명을 임의로 직렬 연결하지
않는다. 같은 평균응력 효과를 서로 다른 단계에서 다시 반영하지 않도록
후속 연결에서는 효과의 포함 여부를 별도로 검토해야 한다.

## 호출과 처리 상태

진입점은 `evaluate_cyclic_fatigue(request, context, *, client=None,
pinned_job=None, owner_source_root=None)`다. `request`는
`CyclicFatigueRequest`, `context`는 `RunContext`여야 한다.
`client=None`이면 필요한 검토를 통과한 뒤에 원 `MaterialClient`를 만든다.
따라서 원 프로젝트가 설치되지 않은 환경에서도 미확정 요청은 로컬에서
`needs_data`로 보존할 수 있다. 공급자를 교체하려면 실제 스키마를 반환하는
`describe`와 기존 원 결과 계약을 반환하는 `evaluate`를 제공한다.

`pinned_job`은 검토 후 저장한 `MaterialJob`이다. 모델 ID, 입력, 자산,
위치, 시계와 근거 목록이 요청과 같아야 하며 실행 시 live 모델 버전,
입력 스키마 digest, 출력 단위와 확률 의미를 다시 대조한다.
`owner_source_root`는 선택적인 원 소스 고정에만 사용하는 로컬 경로다.
요청의 `owner_source_tree_sha256`이 있으면 이 경로가 실제 로드된 원
package와 evaluator 및 공급된 registry를 포함해야 한다. 공통 `material_source_guard`가
실행 전후 `src` 아래 모든 Python 파일의 상대
이름과 바이트를 정렬해 얻은 SHA-256을 요청값과 비교한다. 결과에는
개인 경로를 저장하지 않는다. 고정하지 않은 호출은 소스 불변 검증을
수행했다고 주장하지 않는다. 소스가 바뀌면 새 프로세스에서 새 고정값을
검토하여 재실행한다.

`computed`는 원 계산이 수행됐다는 뜻이다. `needs_data`는 검토·값·의미가
미확정인 상태이며 원 호출 전에는 `native_receipt=None`이다.
`out_of_scope`는 원 모델의 지원 범위를 벗어난 계산이다. 타입·단위·시계·
무결성 위반은 예외로 구분한다. 0을 결측으로 바꾸지 않고 NaN/Inf를
미상 `null`로 바꾸지도 않는다. Python 입력을 `json_value`로 검사한 뒤
엄격한 JSON 재검증과 원 `run_json`을 사용한다. 숫자 자리에 boolean을
넣거나 boolean 선언에 0/1을 넣으면 거부된다.

## 요청의 모든 필드

| 필드 | 값·기본값 | 의미와 처리 |
|---|---|---|
| `method` | 위의 원 모델 ID 세 가지 | 선택한 모델의 입력·검토 집합을 결정 |
| `asset_id` | 비어 있지 않은 문자열 | context 자산과 정확히 일치 |
| `specimen_id` | 비어 있지 않은 문자열 | 원 시편 또는 계산 재료점 식별자; 행 번호로 독립 시편을 만들지 않음 |
| `material_condition_id` | 비어 있지 않은 문자열 | 재질·열처리·방향·환경 등의 검토된 조건 식별자 |
| `population_id` | 문자열 또는 `None` | 실험 모집단/작성 모집단; 미상은 계산 보류 |
| `parameter_support_id` | 문자열 또는 `None` | 계수와 입력 지원 범위의 근거; 미상은 계산 보류 |
| `load_history_id` | 비어 있지 않은 문자열 | 동일 하중·경로 기록의 계보 |
| `location_id` | 비어 있지 않은 문자열 | 원 모델에 전달할 위치 |
| `clock_id` | 비어 있지 않은 문자열 | context 시계와 정확히 일치; cycles를 초로 바꾸지 않음 |
| `stress_measure` | `engineering`, `true` 또는 `None` | 실제 응력 정의; 자동 변환 없음 |
| `strain_measure` | `engineering`, `logarithmic`, `not_used` 또는 `None` | Goodman은 `not_used`, 나머지는 실제 변형률 정의 |
| `response_regime` | `cyclic`, `monotonic` 또는 `None` | `monotonic`과 미상은 cyclic 적용 보류 |
| `stress_sign_convention` | `tension_positive` 또는 `None` | 인장 양수 부호; 절대값으로 수리하지 않음 |
| `amplitude_definition` | `half_range` 또는 `None` | 진폭은 최대–최소 범위의 절반; 범위와 혼용하지 않음 |
| `cycle_definition` | `two_reversals` 또는 `None` | 한 cycle의 두 reversal 정의 |
| `parameters` | 원 파라미터 dict 또는 `None` | 아래의 필수 키가 없거나 중첩 `None`이면 보류 |
| `input_units` | `PARAMETER_UNITS[method]`와 같은 dict | SI·분율·cycle·boolean 단위를 정확히 선언 |
| `applicability_reviews` | 기본 `{}` | 아래 방법별 키의 엄격한 bool; 생략은 False |
| `review_evidence_ids` | 기본 `{}` | 검토 키별 근거 ID; True와 근거가 모두 필요 |
| `evidence_ids` | 중복 없는 tuple, 1개 이상 | context의 고유 근거에 모두 존재해야 함 |
| `path_sample_ids` | 기본 `()` | 경로 방법은 모든 stress 행과 같은 길이의 고유 ID; 원 순서 유지 |
| `sample_time_s` | 기본 `None` | 경로와 정렬된 경과 초 tuple; 비음수·비감소, 반복 시각 보존; 원소 미상은 보류 |
| `observation_period_s` | 기본 `None` | 같은 시계의 시작·끝 초; 끝≥시작, 공급된 표본 시각을 모두 포함 |
| `maximum_path_samples` | 기본 100000, 허용 3~1000000 | stress/strain 배열의 상한; 초과하면 자르지 않고 거부 |
| `owner_source_tree_sha256` | 기본 `None` | 선택적인 64자리 소문자 SHA-256 원 소스 고정 |

`sample_time_s`가 없으면 표본 순서로 경로 일만 계산한다. 표본 간격이나
주파수를 발명하지 않는다. 반복된 시각도 원 관측으로 남기며 시간
미분으로 적분하지 않는다. 기간 메타데이터가 있어도 cycle 수명이나
경로 일을 기간 고장확률·운전 잔여 초로 변환하지 않는다.

## 원 모델별 입력·검토·출력

| 방법 | 필수 파라미터 | 단위·지원 조건 |
|---|---|---|
| `fatigue.goodman` | `alternating_stress_Pa`, `mean_stress_Pa`, `ultimate_tensile_strength_Pa` | 모두 Pa; 진폭과 인장 평균은 ≥0, 강도는 >0 |
| `fatigue.hysteresis_path` | `stress_Pa`, `strain_fraction` | 같은 길이의 원 순서 배열, 각각 Pa와 무차원 분율, 3점 이상 |
| 같은 경로 모델 | `cycle_boundary_declared`, `linear_closure_declared` | 엄격한 bool; False도 유효한 원 선언 |
| 같은 경로 모델 | `maximum_endpoint_stress_difference_Pa`, `maximum_endpoint_strain_difference` | 각각 Pa와 무차원 분율, 모두 ≥0 |
| `fatigue.strain_life` | `strain_amplitude`, `elastic_modulus_Pa`, `fatigue_strength_coefficient_Pa` | 무차원 진폭 >0, E와 계수는 Pa >0 |
| 같은 수명 모델 | `fatigue_strength_exponent`, `fatigue_ductility_coefficient`, `fatigue_ductility_exponent` | 무차원; 지수 두 개 <0, 연성계수 >0 |
| 같은 수명 모델 | `minimum_valid_cycles`, `maximum_valid_cycles` | cycle 단위, 양수이고 최대가 최소보다 큼 |

검토 키와 근거는 다음과 같다. 지원 범위 근거에는 원 계수의 응력·변형률
정의, 재료상태·온도·환경, 실험 모집단과 보정 범위를 포함한다. 모델이
수치로 받지 않는 조건은 이 근거의 검토 대상이며 자동으로 검증됐다고
표시하지 않는다.

| 방법 | `applicability_reviews`와 `review_evidence_ids`의 키 |
|---|---|
| Goodman | `tensile_mean_and_half_range_amplitude`, `strength_measure_material_condition_and_support` |
| 경로 일 | `ordered_aligned_uniaxial_path_and_work_conjugacy`, `cycle_boundary_closure_and_endpoint_tolerances` |
| 변형률 수명 | `fully_reversed_constant_amplitude_and_two_reversals`, `cyclic_coefficients_population_and_calibrated_cycle_support` |

Goodman은 원 모델의 인장 평균응력 영역에 한정한다. 평균이 공급한
인장강도 이상이면 `out_of_scope`이며 음의 평균응력을 0으로 자르지
않는다. 출력 `equivalent_fully_reversed_amplitude_Pa`는 Pa 단위의
등가 완전반전 진폭이다. 수명·강도 검증·합격 판정이 아니다.

경로 일의 원 엔진은 NumPy의 순서 있는 선형 구간 적분을 사용한다.
`signed_recorded_path_work_J_m3`는 기록된 경로의 일 밀도이고
`signed_closed_cycle_work_J_m3`는 cycle 경계와 끝점 허용차를 충족했을
때만 정의된다. 정확히 닫히지 않은 끝점을 선형으로 잇는 것은
`linear_closure_declared=True`일 때뿐이다. 열량이나 재료 손상으로
해석하려면 응력·변형률의 일 켤레와 부피 기준을 별도로 검토해야 한다.
원 모델은 engineering/true 변환이나 유한변형 부피 변환을 하지 않는다.
출력 끝점 차이 `endpoint_stress_difference_Pa`,
`endpoint_strain_difference`와 boolean
`endpoints_within_declared_tolerances`도 그대로 보존한다. 경로 순서를
뒤집어 음의 일이 나와도 절대값으로 바꾸지 않는다. 닫힌 일의 `None`은
미계산이며 0이 아니다. 한 경로의 J/m³를 반복 횟수와 곱해 누적 손상으로
만들거나 부호만으로 물리 시험의 유효성을 확정하지 않는다.

변형률 수명의 원 엔진은 완전반전 일정 진폭의 결합 응력/소성 항 관계를
사용한다. SciPy `brentq`가 선언한 cycle 구간의 로그 좌표에서 해를
찾고 `cycles_to_failure`와 `reversals_to_failure`를 각각 cycle과
reversal로 반환한다. 원 정의에서 reversal은 cycle의 두 배다.
해가 선언 구간에 없으면 `out_of_scope`다. 계수를 단조 인장자료에서
추정하지 않고 평균응력·노치·환경 보정도 자동 추가하지 않는다.
이 이름의 원 출력은 특정 가정의 재료 수명 계산이며 운전 중 자산의
잔류 수명이나 확률 분포를 뜻하지 않는다.

## 사용자 데이터와 단위 교체

`examples/cyclic_fatigue.py`의 수치는 독립적으로 작성한 산술 확인용이다.
실제 재료 기본값으로 채택하지 않는다. 사용자는 검토한 파라미터,
시편/모집단/조건/지원 ID, 원 단위와 변환 근거, 근거의 origin·hash를
함께 바꾼다. 예를 들어 원 MPa 값은 명시적으로 10⁶을 곱해 Pa로 만들고
원 % 변형률은 100으로 나누어 분율로 만든다. `input_units`만 Pa로
바꾸어 원 숫자를 그대로 전달하면 단위 교체가 아니다. 단위 변환이
필요한 원본과 변환 기록은 로컬 artifacts에 보존한다.

다음 예제는 미확정 값이 원 엔진을 요구하지 않는다는 것을 확인한다.
저장소 루트에서 실행한다.

```python
import runpy
from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue

requests, context = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
request = requests["fatigue.goodman"]
held = evaluate_cyclic_fatigue(request.model_copy(update={"parameters": None}), context)
assert held["status"] == "needs_data"
assert held["native_receipt"] is None
assert held["failure_probability"] is None
```

원 엔진을 별도로 설치하거나 선택한 원 프로젝트의 `src`를 Python
모듈 경로에 지정한 다음에는 아래의 파라미터 교체와 job 재생을 실행할
수 있다. 실제 데이터에서는 검토 근거도 해당 파라미터와 함께 바꾼다.

```python
import math
import runpy
from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue
from pipeline_integrity.material_exchange import prepare_material_job

requests, context = runpy.run_path("examples/cyclic_fatigue.py")["make_cases"]()
request = requests["fatigue.goodman"]
parameters = request.parameters | {"alternating_stress_Pa": 90.0 * 1e6}
request = request.model_copy(update={"parameters": parameters})
job = prepare_material_job(
    request.method,
    parameters,
    context,
    location_id=request.location_id,
    evidence_ids=request.evidence_ids,
)
record = evaluate_cyclic_fatigue(request, context, pinned_job=job)
assert record["status"] == "computed"
assert math.isclose(record["values"]["equivalent_fully_reversed_amplitude_Pa"], 120e6)
assert record["cumulative_damage"] is None
```

## 재현과 확인된 증거

```powershell
python -m pytest -q tests/test_cyclic_fatigue.py
python -m ruff check src/pipeline_integrity/cyclic_fatigue.py tests/test_cyclic_fatigue.py examples/cyclic_fatigue.py scripts/check_cyclic_fatigue.py
python -m ruff format --check src/pipeline_integrity/cyclic_fatigue.py tests/test_cyclic_fatigue.py examples/cyclic_fatigue.py scripts/check_cyclic_fatigue.py
python scripts/check_cyclic_fatigue.py --materials PATH_TO_MAT
```

실제 세 원 모델의 16개 호출, 상태별 작성 사례와 11개 거부 경계를
실행했다. 10개 독립 산술/폐형식 기준은 Goodman의 진폭과 0,
직사각형·역순·명시 삼각형 폐합의 일, 같은 지수의 두 수명 항으로부터
얻는 알려진 cycle 해와 최소 cycle 경계를 포함한다. 최대 절대 오차는
약 7.28×10⁻¹²였다. 서로 다른 단위의 오차를 하나의 물리 정확도 지표로
해석하지 않는다. 이 확인은 원 구현과 계산 경계의 소프트웨어 검증이다.

`research/cyclic_fatigue_results.json`에는 모델 버전·입력 스키마 hash,
원 소스 전후 SHA-256, 코드 hash와 집계·한계만 공개한다. 실제 원 실행
기록과 상세 독립 기준은 `artifacts/cyclic_fatigue/`에 둔다. 원
관측·상세 receipt·개인 절대경로는 공개 요약에 넣지 않는다.
확인된 원 소스는 76개 Python 파일이며 실행 전후 동일했다.
첫 시도에서 실행 전후 원 소스가 달라 고정 검사가 중단한 이력은
새 고정값으로 성공한 실행과 구분한다.
최종 재현은 변경 중인 원 작업폴더에서 읽기 전용으로 분리한 소스
snapshot을 사용한다. 그 tree SHA-256은
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다.
MIT 라이선스와 고지를 유지하며 snapshot 자체는 배포 경계 밖에 둔다.
과거 실행의 69파일 또는 다른 76파일 hash를 이 값으로 소급 변경하지
않는다. 실제 관측으로 세 모델의 가정을 자격화한 결과는 아직 없다.

결과의 `request`, `context`, `native_receipt`가 계산의 계보다.
`values`, `output_units`, `issues`는 원 모델 결과를 보존한다.
`record_digest`는 요청·context·출력 전체를 바인딩하고
`implementation_sha256`은 이 교환 모듈을 식별한다.
`source_pin`은 소스 고정을 켰을 때 전후 digest와 파일 수, 불변 여부를
담고 그 밖에는 `None`이다.
`output_origin=simulation`은 모델 출력의 기원이며 입력 관측의 origin은
context에 남긴다. `failure_probability`, `remaining_service_life_s`,
`cumulative_damage`는 미계산 `None`이고
`material_or_code_acceptance`, `field_or_population_validation`은 False다.

실험 자료의 시험 종료·중단·runout, 물리 시편, 모집단, 재료 조건,
원 단위와 권리가 미확정이면 예제 조건으로 채우지 않는다. 공개
자료를 발견했다는 사실과 실제 교정·독립 검증은 별도 상태다.
현재 이 모듈에서 공개 관측으로 계수를 적합하거나 현장 전이를
검증한 결과는 없다.

## 다른 세션에서 이어갈 확장 요청

재료 소유 프로젝트의 PI-X04 확장에는 입력량별 원 단위·응력/변형률
정의·조건/모집단·보정 지원·근거 digest를 원 job 자체에 바인딩하는
계약을 요청한다. 현재 `MaterialJob`은 수치 입력과 자산·위치·시계·
근거를 고정하지만 시편/모집단의 모든 의미를 원 입력 스키마가 직접
검증하지는 않는다. 원 engine이 반환하는 안정적인 모델 소스 digest와
검토된 계수 지원 계약이 생기면 디스크 트리 고정과 함께 대조한다.

원 경로 처리에는 여러 cycle의 명시 경계, 결측/중단 창,
반주기 잔여 상태와 부호·일 켤레를 보존한 streaming 계약을 요청한다.
창을 이어 붙이거나 겹치는 구간을 두 번 누적하지 않도록 원 관측
ID와 상태 계보를 유지해야 한다. 순서 있는 누적 손상이나
크리프–피로 상호작용이 필요하면 소유 엔진에 새 모델과 독립 기준을
추가한 뒤 이 경계에서 호출한다.

위험 소유 프로젝트의 PI-X02에는 결정론적 진폭·경로 일·cycle 수명과
기간 최초고장 사건을 구분하는 수신 계약을 요청한다. 고장 사건 정의,
공통 기간·생존조건·운전 이력·불확실성·독립 검증이 없는 출력은
FTA·HEP·방출 빈도에 자동 연결하지 않는다. 시계 변환과 실제 운전
횟수의 근거가 확보돼도 추가 확률 모델의 검토를 대체하지 않는다.
