# 제작·체결·두께 예산의 원 재료 모델 연결

`fabrication_exchange`는 기존 Mat의 `fabrication.bend_strain`,
`fabrication.bolt_preload`, `fabrication.thickness_budget`를 호출한다. 이 패키지는
입력의 SI 단위, 자산·위치·재료 상태·시계, 방법별 검토와 근거를 확인한다.
계산은 원 `MaterialJob` 경로로 실행하고 원 응답과 receipt를 보존한다.

굽힘의 기하학적 변형률, 선언한 토크 계수에 따른 축력·명목 인장응력, 선언한
손실의 두께 예산이 현재 계산 범위다. 강도, 내압, 접합부 강성, 기밀성, 작업
성공 확률과 규격 승인은 별도 근거가 필요하다. 예제의 치수·계수·허용값은
독립 작성한 합성 입력이며 실제 체결 또는 제작의 기본값으로 제공하지 않는다.

## 실행과 API

저장소 루트에서 실행한다. 완전한 입력을 계산할 때는 원 `matintegrity`가
import 가능해야 한다. 검증 스크립트는 `PATH_TO_MAT`에 원 프로젝트 루트를 받는다.
필수 입력이나 검토가 미상이면 원 모델을 import하거나 호출하지 않고 보류한다.

```powershell
python -m pytest tests/test_fabrication_exchange.py -q
python scripts/check_fabrication.py --materials PATH_TO_MAT
```

```python
import runpy

from pipeline_integrity.fabrication_exchange import evaluate_fabrication

cases, context = runpy.run_path("examples/fabrication_methods.py")["make_cases"]()
results = {method: evaluate_fabrication(request, context) for method, request in cases.items()}
for method, result in results.items():
    result.check_integrity()
    print(method, result.status, result.requirement_outcome)
```

위 예제의 세 계산은 모두 `computed`다. 굽힘 예제의 선언 요구는
`not_satisfied`이고 볼트와 두께 예제의 선언 요구는 `satisfied`다. 이 결과는
계산 수행 여부와 사용자 요구 충족 여부가 다름을 보여준다.

공개 호출은 다음과 같다.

```text
evaluate_fabrication(request, context, *, client=None, pinned_job=None)
```

`request`는 `FabricationRequest`, `context`는 `RunContext`다.
선택 인자 `client`는 원 레지스트리의 설명과 엄격한 JSON 계산 경로를 제공하는
재료 client다. `pinned_job`은 이전에 검토한 `MaterialJob`을 재사용할 때 지정한다.
반환형은 `FabricationResult`이며 JSON 요약은 `result.model_dump(mode="json")`으로
얻는다.

`FabricationRequest`의 공통 필드는 다음과 같다.

| 필드 | 역할 |
|---|---|
| `method` | 세 원 모델 중 하나를 선택한다. |
| `asset_id`, `location_id` | 실제 자산과 계산 위치·체결부를 구분한다. |
| `clock_id` | 원 기록 또는 명시한 snapshot의 좌표 기준을 선언한다. |
| `material_condition_id` | 호출자가 확인한 재료·체결·제작 상태 식별자를 보존한다. |
| `parameters` | 원 스키마의 scalar 입력 또는 미상인 `None`을 전달한다. 전체가 미상이면 `None`을 쓴다. |
| `input_units` | 입력별 정확한 SI 단위 문자열을 선언한다. 자동 변환하지 않는다. |
| `applicability_reviews` | 해당 방법의 적용 검토를 기록한다. |
| `review_evidence_ids` | 검토 키를 실제 근거 ID에 연결한다. |
| `parameter_evidence_ids` | 알려진 각 수치 입력을 실제 근거 ID에 연결한다. |
| `evidence_ids` | 요청의 기본 근거 ID를 중복 없이 선언한다. |
| `requirements` | 선택적인 사용자 요구와 판정 근거를 선언한다. |

자산과 시계는 context와 같아야 한다. 위치는 원 재료 context의 `location_id`로
전달한다. 근거 목록과 검토·파라미터·요구에 연결한 모든 근거 ID는
`context.evidence`에 실제로 있어야 한다. context에서 같은 ID를 반복하면
거부한다. 식별자나 검토 플래그를 쓰는 행위만으로 실제 재료 상태나 계수의
적합성을 확인한 것으로 해석하지 않는다.

## 입력 단위와 각 원 계산의 범위

정확한 입력 단위는 `SI_INPUT_UNITS[method]`에서 조회할 수 있다.
현재 허용하는 단위 문자열과 원 파라미터는 다음과 같다.

| method | 입력과 단위 | 주요 출력 |
|---|---|---|
| `fabrication.bend_strain` | `outside_diameter_m`: `m`; `centerline_bend_radius_m`: `m` | `outer_fiber_strain`: `1` |
| `fabrication.bolt_preload` | `torque_N_m`: `N*m`; `nominal_diameter_m`: `m`; `nut_factor`: `1`; `tensile_area_m2`: `m2` | `preload_N`: `N`; `nominal_tensile_stress_Pa`: `Pa` |
| `fabrication.thickness_budget` | `nominal_thickness_m`, `manufacturing_minus_tolerance_m`, `corrosion_allowance_m`, `required_structural_thickness_m`, 선택적인 `measured_postfabrication_thickness_m`: `m`; `forming_loss_fraction`: `1` | 공급·성형·구조 두께와 두 경로의 여유: `m` |

길이·면적을 mm·mm²로 보유한 자료는 검토된 변환과 원 단위를 별도로 기록하고
SI로 변환한 뒤 전달한다. `input_units`에 `mm`를 넣어 환산을 요청할 수 없다.
숫자 문자열, boolean을 수치로 쓴 입력, NaN·무한대, 원 스키마에 없는 키는
거부한다. 물리적 양수·비음수·비율 범위는 완전한 입력을 받을 때 원 스키마의
엄격한 JSON 검증으로 다시 확인한다.

굽힘 입력은 외경과 중심선 굽힘 반경을 구분한다. 원 결과는 기하학적
외측 섬유 변형률이며 타원화, 실제 박육화, 중립축 이동, 잔류응력과 성형
자격을 계산하지 않는다. 반경이 외경의 절반 이하인 경계는 원 모델이
`out_of_scope`로 반환한다. 굽힘 변형률에서 성형 손실률을 자동 생성하지 않는다.

볼트의 `nut_factor`는 해당 나사·좌면·윤활·체결 조건의 검토된 토크 계수다.
자료의 마찰계수 한 개를 이 필드에 대신 넣지 않는다. `tensile_area_m2`는
검토된 유효 인장면적이며 호칭지름만으로 내장 표에서 가져오지 않는다.
토크가 알려져도 두 입력이 미상이면 축력과 응력 계산을 보류한다. 원 모델의
축력·명목응력은 마찰 산포, 접합부 강성, 가스켓 압축, 풀림·이완과 밀폐 성능의
검증을 포함하지 않는다.

## 방법별 검토와 근거 연결

검토 키는 `REVIEWS[method]`로 조회한다. 각 키가 `True`이고 그 키의 근거가
`review_evidence_ids`에 있어야 계산한다. 다음 키를 임의의 일반 승인 하나로
대체할 수 없다.

| method | 검토 키와 확인할 내용 |
|---|---|
| 굽힘 | `outside_diameter_and_centerline_radius`: 외경·중심선 반경과 측정 기준. `kinematic_strain_scope_excludes_thinning_ovalization`: 기하학적 지표의 적용 범위와 제외 효과. |
| 볼트 | `torque_is_applied_tightening_torque`: 원 토크의 의미·방향·체결 구간. `nut_factor_matches_thread_bearing_lubrication`: 계수의 조건 대응. `effective_tensile_area_basis`: 인장면적의 기하·산정·근거. |
| 두께 | `manufacturing_then_forming_loss_basis`: 제조 허용차와 성형 손실의 기준·적용 순서. `future_corrosion_and_required_wall_basis`: 미래 부식 여유와 요구 구조 두께의 독립 근거. `measured_wall_excludes_repeat_manufacturing_and_forming_loss`: 제작 후 실측값 경로의 손실 중복 방지. |

검토 근거와 파라미터 근거는 역할이 다르다. 예를 들어 원 토크 파일을
`parameter_evidence_ids["torque_N_m"]`에 연결해도 마찰·좌면·윤활 상태에 맞는
`nut_factor`의 적합성 검토가 완료되는 것은 아니다. 하나의 근거가 여러 역할을
실제로 뒷받침하면 같은 ID에 연결할 수 있지만, 연결의 해석은 호출자가 검토한다.
원자료 DOI·파일 해시·조건·단위·변환·검토 문서는 context 근거에 남긴다.

## 미상과 0, 두께 손실의 서로 다른 경로

`parameters=None`, 필수 키 누락, 필수 값의 `None`, 알려진 값의 단위·근거 누락,
방법 검토 미완료는 `needs_data`다. 이 경우 `native_receipt`는 `None`이고
`values`는 비어 있다. 알려지지 않은 토크를 0으로 채우거나 nut factor·면적·
허용차·성형 손실·부식 여유를 예제에서 가져오지 않는다.

실제로 선언한 `torque_N_m=0.0`은 원 모델이 허용하는 수치 입력이다. 완전한
나머지 조건으로 계산하면 축력과 명목응력이 모두 0인 `computed` 결과가 나온다.
예제의 최소 축력 요구는 이때 충족되지 않는다. 이 값은 접합부의 기밀성이나
작업 성공 여부를 나타내지 않는다.

두께 예산의 호칭 경로는 제조 음의 허용차를 먼저 적용하고, 그 결과에 선언한
성형 손실 비율을 적용한 뒤 미래 부식 여유를 차감한다. 제조·성형·미래 부식의
각 항목은 서로 구분한 손실 근거가 필요하다. 이미 포함된 손실을 다른 이름으로
다시 넣지 않는다. 요구 구조 두께는 검토된 외부 요구 입력이며 이 API가 압력·
재질·규격에서 도출하지 않는다.

`measured_postfabrication_thickness_m`는 제작 후의 별도 실측 두께다. 원 모델은
이 실측 경로에 제조 허용차와 성형 손실을 다시 적용하지 않고 선언한 미래
부식 여유만 차감한다. 실측이 `None`이거나 선택 키가 없으면 호칭 경로는
계산할 수 있고 `measured_structural_thickness_m`, `measured_margin_m`는
`None`으로 남는다. 이 선택 입력의 부재는 실측값 0을 뜻하지 않는다.

합성 예제는 호칭 10 mm, 제조 음의 허용차 1 mm, 성형 손실 10%, 미래 부식
여유 1 mm를 선언해 구조 두께 7.1 mm를 얻는다. 별도의 제작 후 실측 8.5 mm는
미래 여유만 차감해 7.5 mm가 된다. 요구 구조 두께 7 mm에 대한 두 여유는
각각 0.1 mm와 0.5 mm다. 이 수치는 손실 경로의 수학 대조용 입력이다.

제조 허용차·성형·미래 여유가 양의 구조 벽을 소진하면 원 모델은
`out_of_scope`와 빈 결과를 반환한다. 부족 두께를 0으로 잘라 `computed`로
바꾸지 않는다. 실측 경로의 음의 여유 등 원 결과에 포함된 값도 그대로
보존하며 요구 비교에서 별도로 해석한다.

## 계산 상태와 선언 요구의 판정

`status`는 원 계산의 `computed`, `needs_data`, `out_of_scope`를 보존한다.
`FabricationRequirement`는 `id`, `output_key`, `unit`, `relation`, `threshold`,
`evidence_id`, `interpretation_reviewed`를 받는다. `relation`은 `le` 또는
`ge`이며 경계의 등호를 포함한다. 출력과 단위는 선택한 원 수량과 정확히
맞아야 한다.

검토된 요구는 원 수량과 호출자가 제공한 한계를 비교한다. 계산이 미완료이거나
요구 해석이 미검토이거나 해당 원 값이 `None`이면 그 요구는 `unknown`이다.
`requirement_records`는 각 요구·원 값·판정을 보존한다. 전체 판정은 알려진
미충족 요구가 하나라도 있으면 `not_satisfied`, 그렇지 않고 미상이 있으면
`unknown`, 모든 요구가 충족되면 `satisfied`다. 요구를 선언하지 않았으면
`not_requested`다.

계산 완료와 요구 미충족이 함께 나올 수 있다. 예제의 굽힘 변형률 0.1은
정상 계산되지만 선언한 상한 0.08을 초과한다. 요구 해석의 미검토도 계산 상태를
자동 변경하지 않는다. 원 receipt의 수량·상태·주석은 외부 요구 비교로 덮어쓰지
않는다. `issues`에는 원 모델의 설명성 주석도 들어갈 수 있으므로 목록의
비어 있음만으로 계산 또는 요구의 성공을 판단하지 않는다.

## 원 스키마 pin과 receipt 재사용

첫 계산은 live 레지스트리에서 원 모델 버전, 입력 스키마 digest, 출력 단위와
확률 의미를 얻어 job을 만든다. `evaluate_material_job`은 strict JSON 경로로
원 계산을 실행하고 모델·입력·자산·위치·시계·context digest와 원 입력 receipt를
확인한다. 세 지표의 확률 의미는 비어 있어야 한다.

```python
from pipeline_integrity.material_exchange import MaterialJob

request = cases["fabrication.bolt_preload"]
first = results[request.method]
pin = MaterialJob.model_validate_json(first.native_receipt.job.model_dump_json())
repeated = evaluate_fabrication(request, context, pinned_job=pin)
repeated.check_integrity()
assert repeated.values == first.values
```

재사용하는 job의 방법·입력·자산·위치·시계·근거 집합은 현재 요청과 같아야 한다.
그 뒤 원 레지스트리의 버전·스키마·단위·확률 의미를 job pin과 비교한다.
변경을 발견하면 거부하며 이전 pin을 자동 갱신하지 않는다. pin에 대한 새
검토와 필요하면 새 job 생성은 별도의 명시적 절차다.

`FabricationResult.check_integrity()`는 요청·context·요구·결과 digest와 원
`MaterialReceipt`를 함께 확인하고, 바깥 결과의 값·단위·상태가 원 payload와
같은지 확인한다. JSON으로 저장하고 엄격하게 다시 읽은 결과도 검사할 수 있다.
무결성 검사는 저장 후의 변경을 확인하는 소프트웨어 계약이며 실제 계수의
보정이나 근거 문서의 승인 권한을 대신하지 않는다. 실제 관측이 든 전체 입력과
receipt는 원자료 배포 경계에 따라 로컬에 보관하고 공개에는 적절한 집계와
해시·출처를 남긴다.

## 검증 증거와 실제 자료의 역할

`research/fabrication_results.json`은 원 Mat 세 모델 v1.0.0의 실제 호출,
독립 작성 기준 11개, 0 토크, 굽힘 지원 경계, 벽 소진, 미상 보류와 스키마 pin
거부의 실행 결과를 담는다. 세 모델의 job·receipt digest와 원 소스 트리의
실행 전후 해시 일치를 기록한다. Git commit을 확인할 수 없는 환경에서는
commit을 추정하지 않고 소스 해시를 사용한다.

전용 `tests/test_fabrication_exchange.py`의 41개 검사는 검토·근거·SI 경계,
미상과 0, 원 모델 호출, 요구와 계산의 분리, 원 context·schema·receipt의
변조 거부를 확인했다. 이 검사와 숫자 대조는 수학·소프트웨어 연계 증거다.
독립 현장 시험으로 체결 축력, 박육화 또는 구조 안전을 검증한 결과는 아니다.

실제 체결 자료의 토크·축력·회전각은 먼저 각 관측의 원 단위·행·시편/체결 ID·
공급자 파일 해시와 조건을 유지해야 한다. 각도축을 경과 시간으로 바꾸지 않는다.
측정한 축력은 원 참조 관측이며, 이것만으로 토크 계수나 유효 인장면적을
자동 역산해 본 계산의 검토 입력으로 승격하지 않는다. 자료의 마찰계수는
nut factor와 구분한다. 독립적으로 검토한 두 입력이 없으면 실제 토크를
보유했어도 이 API의 축력·응력 계산은 `needs_data`다.

굽힘 실험을 사용할 때는 외경·반경·국부 박육화·타원화·재료 상태의 관측을
구분한다. 두께 자료는 호칭·제조 허용차·제작 전후 측정·미래 부식 여유를
구분한다. 현재 합성 기준을 실제 자료에 대한 보정 또는 현장 자격으로
표현하지 않는다. 실제 자료의 취득·해시·조건·독립 검증 상태는 별도 자료 기록과
함께 갱신해야 한다.

## 후속 Mat 확장 요청의 계약

원 수학과 소유권은 Mat에 유지한다. 확장은
`EXTENSION_REQUESTS_KO.md`의 PI-X04 체계에 맞춰 입력·출력·근거·완료 조건을
명시한 요청으로 전달한다. 다음 정보가 준비되기 전에는 현재 지표를 더 넓은
물리 성능으로 변환하지 않는다.

| 대상 | 필요한 확장 입력·결과·검증 조건 |
|---|---|
| 체결 계수와 축력 | 시편·나사·좌면·윤활·기기·보정·동기화·체결 가지·산포·유효 인장면적·관측 축력의 정의를 보존한다. 보정/평가를 체결 또는 시편 단위로 분리하고 계수 추정과 독립 축력 검증을 구분한다. |
| 체결부 기계 성능 | 접합부/볼트 강성, 가스켓, 이완·풀림, 외력·열하중, 재료 강도와 지원 범위를 선언한다. 밀폐 시험의 압력 기준·유체·온도·누설 검출 한계와 합격 근거를 별도로 받는다. |
| 성형과 박육화 | 실제 단면·국부 두께·타원화·곡률·재료 상태와 성형 이력을 연결한다. 기하학적 변형률과 실측 손실률을 혼용하지 않고 원 시편·위치·오차·지원 범위로 검증한다. |
| 두께/압력/부식 연계 | 현재 측정과 과거·미래 손실의 포함 범위, 절대/게이지 압력 기준, 요구 구조 두께의 공급자·판본·적용 범위를 명시한다. 동일 손실의 중복 차감과 이미 실측에 반영된 제조 손실의 재적용을 거부한다. |
| 요구 판정 공급자 | 사용자 허가·판본·적용 범위·요구 단위와 비교 규칙을 제공한다. 숫자 요구의 만족과 절차/규격 승인·현장 자격·확률을 각각 구분한 결과 계약을 유지한다. |

확장 완료는 관련 원 엔진의 실제 호출, 독립 수치 기준, 잘못된 context·단위·
손실 중복·지원 이탈·미상 입력의 거부 또는 보류, 원 receipt 재현으로 확인한다.
현장 검증이나 확률 모델의 근거가 확보되지 않은 부분은 별도 미완료 상태로
남긴다.
