# 단일 매설관의 정상 열 경계

`pipeline_integrity.buried_thermal`은 긴 직선 단일 관의 층·접촉·토양 경계를
등온 평면에 연결하는 정상 열저항 구성 요소다. `ht` 1.2.0의 실제
`R_cylinder`, `S_isothermal_pipe_to_plane`를 사용하고, 유동 모드는 기존
`models.thermal.cooling`을 재사용한다. 정상 상태의 열 유출과 제한된 단상
유동 출구 온도를 계산한다. 토양의 시간 변화·수분 이동·여러 관의 상호작용을
풀지 않으므로 전체 토양 연성 범위는 `partial`이다. 등록 계산 모델 24개를
새 모델 수로 늘리지 않는다.

기술 근거는 [ht 공식 conduction 문서](https://ht.readthedocs.io/en/latest/ht.conduction.html)다.
공식 API가 사용하는 원통 내·외경, 길이와 관 중심에서 등온 평면까지의 거리를
호출 경계에서 명시한다. 형상 인자는 길이 단위이며 원통 저항은 K/W다.
이 문서의 예제 숫자와 검증 조건은 독립 작성한 가상 값이다. 재료 물성·접촉 저항은
이용 권한과 적용 조건을 확인한 사용자 입력으로 받는다.

## 적용 범위와 상태

관은 전 구간에서 평면 아래에 있어야 한다. 관 축에 수직한 국소 열 경로를
사용하므로 충분한 길이/직경비와 축방향 온도 변화의 허용 범위를 caller가
검토해야 한다. 토양은 균일하고 열전도도가 일정하며 표면 평면의 온도가 주어진다.
방사형 재료층·내부 film·층 앞 접촉·관 외측 토양 접촉을 직렬 열 경로로 결합한다.
실제 지표의 대류·복사·강우·계절 경계나 이방성·지하수 흐름을 이 평면과 같다고
자동 판단하지 않는다.

- `uniform_fluid_temperature`: 구간 유체 온도를 일정한 경계로 선언한다.
  열은 W로 계산하지만 유체 유량·출구 온도·유체 에너지 수지는 정의하지 않는다.
- `steady_plug_flow`: 일정한 유량과 cp의 단상 정상 유동이다. 기존 cooling으로
  유체 출구를 계산하고 평균 열 교환의 층 온도를 구한다. 평균 층 온도가
  모든 축 위치의 국소 온도라는 뜻은 아니다. 물성 지원 범위는 입·출구의
  각 층·토양 국소 온도까지 검사한다.

`computed`는 명시한 범위 안에서 정상 경계를 계산했다는 뜻이다.
`needs_data`는 미검토·근거 누락·미상 물성이나 접촉 조건이다.
`out_of_scope`는 평면 관통, 길이/직경비·NTU 지원 이탈, 물성 온도 지원 이탈,
유량 0의 정체 문제 등이다. 기하학적 범위 이탈은 ht 호출 전에 보류하며,
계산 후 발견한 물성 지원 이탈은 수치 진단을 보존하면서 out_of_scope로 반환한다.
음수·bool 수치·NaN·Inf·잘못된 단위·입력/근거 불일치·지원하지 않는 버전은 오류다.

## API

`evaluate_buried_thermal(request: BuriedThermalRequest, context: RunContext) -> dict`와
`verify_buried_thermal_record(record)`를 사용한다. 일반 dict 입력을 자동으로
추정하지 않는다. frozen/strict 계약으로 만들고 실행 직전에 다시 검증한다.
`model_copy`로 바꾼 입력도 이 재검증을 거친다. context의 asset/clock은 요청과
같아야 하고 선택한 모든 근거가 context.evidence에 있어야 한다.
location은 별도 요청 위치로 보존한다. 시계는 한 정상 조건을 식별하는 기준이며
그 자체로 시간 이력이나 기간 평균을 구성하지 않는다.

`BuriedThermalRequest`의 모든 필드는 다음과 같다.

| 필드 | 의미·단위 |
|---|---|
| `asset_id`, `location_id`, `clock_id` | 설비·구간 위치·정상 조건 시계 식별 |
| `mode` | 위의 두 모드 |
| `inner_radius_m` | 관 내측 유체 film 반경, 양수 m |
| `length_m` | 계산 구간 길이, 양수 m |
| `depth_to_pipe_center_m` | 등온 평면에서 관 중심까지 깊이, 양수 m; 마지막 층 외반경보다 커야 함 |
| `layers` | 안에서 밖 순서의 1~64개 ThermalPipeLayer; 자동 정렬·겹침 보정 없음 |
| `fluid_inlet_temperature_K` | 일정 유체 온도 또는 유동 입구 온도, K; null은 미상 |
| `surface_plane_temperature_K` | 등온 평면 경계, K; null은 미상 |
| `soil_conductivity_W_m_K` | 선언 토양 열전도도, W/(m·K), 양수 또는 null |
| `soil_contact_resistance_K_m_W` | 마지막 층 바깥과 토양 사이의 길이당 접촉 열저항, K·m/W; 0과 null을 구분 |
| `inner_film_coefficient_W_m2_K` | 내부 film 열전달계수, W/(m²·K), 양수 또는 null |
| `soil_temperature_support_K` | 선언 토양 물성의 검토 온도 하한/상한, K; null은 미상 |
| `fluid_temperature_support_K` | 유체 상/일정 물성의 검토 온도 하한/상한, K; null은 미상 |
| `minimum_supported_slenderness` | 검토한 최소 L/(마지막 층 외경), 무차원; null은 미상 |
| `mass_flow_kg_s` | 유동 모드의 양의 질량 유량, kg/s; 기본 null, 0은 정체 모델 범위 이탈 |
| `specific_heat_J_kg_K` | 유동 모드의 양의 일정 cp, J/(kg·K); 기본 null |
| `maximum_supported_axial_NTU` | 유동 모드의 검토한 최대 conductance/(m·cp), 무차원; 기본 null |
| `soil_evidence_id`, `film_evidence_id` | 선택한 토양·film 조건 근거; null은 미상 |
| `fluid_property_evidence_id` | 유동 모드의 유량/cp/단상 지원 근거; 기본 null |
| `applicability_reviews` | 아래 네 검토 bool; 기본 빈 값은 미검토 |
| `review_evidence_ids` | 각 true 검토의 선택 근거 ID |
| `evidence_ids` | context에서 선택한 중복 없는 근거 ID; 최소 1개 |
| `input_units` | 모듈 INPUT_UNITS와 정확히 같은 SI 단위 사전 |
| `expected_ht_version` | 검토한 라이브러리 버전; 예제는 1.2.0, 기본 null은 버전 일치 강제 생략 |

검토 키는 `isothermal_plane_and_homogeneous_soil`,
`single_long_straight_pipe_and_local_radial_approximation`,
`constant_properties_and_single_phase_temperature_support`,
`film_and_contact_boundary_conditions`다. 각 true에는 근거가 필요하다.
예제의 최소 길이비 20이나 최대 NTU 1을 현장 기본값으로 사용하지 않는다.

`ThermalPipeLayer`에는 `id`, `material_condition_id`, `outer_radius_m`,
`conductivity_W_m_K`, `upstream_contact_resistance_K_m_W`,
`temperature_support_K`, `evidence_id`가 있다. 외반경은 직전 층 외반경보다
커야 한다. 첫 층의 내부 반경은 요청의 inner_radius_m이다. 열전도도·접촉·온도
지원이 null이면 계산하지 않는다. 온도 지원 구간은 순서가 맞아야 한다.
재료 식별자는 강종 이름만으로 물성을 가져오는 공급자가 아니다.

길이당 접촉 열저항 R′는 온도차를 단위 길이 열률 q′로 나눈 값이다.
단위는 K·m/W이고, 길이 L 구간의 전체 접촉 열저항은 R′/L, K/W가 된다.
면적당 접촉저항 m²·K/W를 그대로 이 필드에 넣지 않는다. 그 변환에는 접촉
기하학·면적의 별도 근거가 필요하다. 0은 접촉 온도차가 없는 선언이고 null은
접촉 조건 미상이다. 층 앞 접촉은 해당 층 내부 바로 앞에, soil_contact는
마지막 층 외부와 토양 사이에 한 번만 적용한다.

## 출력과 검증

반환 schema는 `pipeline_integrity.buried_thermal.v1`이다. request/context,
status/issues, values/output_units, calculation, implementation_sha256과
record_digest를 보존한다. calculation은 요청/context digest, 원 값/단위/상태,
backend의 ht 버전·함수명·함수 소스 digest·기존 cooling 소스 digest와 자체 digest를
담는다. 호출 전 보류는 calculation=null이고 값/단위는 빈 사전이다.

주요 값은 signed `heat_from_fluid_to_soil_W`, `heat_per_length_W_m`,
`total_resistance_K_W`, `conductance_W_K`, `soil_shape_factor_m`,
`soil_resistance_K_W`, `soil_contact_resistance_K_W`,
`inner_film_resistance_K_W`, 유체 inlet/mean/outlet K, axial_NTU,
layer_results, soil_inner_temperature_K, surface_plane_temperature_K,
temperature_drop_residual_K, fluid_energy_balance_residual_W다.
층 결과에는 layer/material/evidence ID, 접촉·전도 저항 K/W와
upstream/inner/outer 온도 K를 보존한다.
`axial_endpoint_temperatures`는 유동 모드의 inlet/outlet 각각에 대해 유체 온도,
층별 내·외 온도와 토양 내부 온도를 K로 담는다. 일정 물성에서 입출구 사이
각 국소 온도는 유체 온도에 따라 단조로 변하므로 양 끝을 지원 검사에 쓴다.
이 배열에서 별도의 열량을 만들어 전체 열과 다시 더하지 않는다. uniform 모드는 빈 배열이다.

열의 양수 방향은 유체에서 토양이다. 지표가 더 뜨거우면 열은 음수이고 유체가
가열된다. 절댓값으로 바꾸지 않는다. 온도 drop 잔차는 모든 직렬 경로를 거쳐
복원한 평면 온도와 선언 평면 온도의 차이다. 유동 에너지 잔차는 총 열과
유량·cp·입출구 온도차의 차이로 W다. 이는 정상 에너지 확인이며 시간 적분의
토양 축열·초기조건·시간 격자 수렴을 검증한 것이 아니다.

세 자격 flag `transient_soil_or_moisture_coupling_modelled`,
`multiple_pipe_interaction_modelled`, `field_or_standard_qualification`은 항상 false다.
record 검증은 digest뿐 아니라 요청/근거 binding·투영된 값/단위/상태와
backend pin의 의미도 확인해야 한다. 해시는 무결성 근거이며 서명이나
측정의 정확성에 대한 독립 보증은 아니다.
현재 검증기는 동일 adapter 파일과 설치된 ht 버전·함수 소스가 있어야 계산된
record를 검증한다. 미계산 record는 ht를 불러오지 않고 보류 계약을 확인한다.
다른 구현으로 결과를 옮길 때 기존 pin을 지우거나 덮어쓰는 대신 원 환경을
보존하거나 새 버전으로 재계산한다.

## 독립 작성 수치 기준

재현은 작성한 27사례(계산 11·자료 보류 7·지원 이탈 9), 실제 계산 body 17개,
수치·수지·상태·메타데이터 기준 합계 126개를 확인한다. 별도로 잘못된 입력 10개와
저장 기록 오염 7개를 거부하고 집중 pytest 42개를 실행했다. 이 합계 전체를 독립
물리 검증 기준 수나 측정 자료 수로 해석하지 않는다.

검증 예제는 내반경 0.1 m, 첫 외반경 0.2 m, 길이 10 m,
깊이 0.2·cosh(1) m, 층 k=ln(2) W/(m·K), film h=10 W/(m²·K),
토양 k=1 W/(m·K)와 접촉 0을 선언한다. 이 선택에서 film/층/토양의 세 저항이
모두 1/(20π) K/W가 된다. 320 K 유체와 300 K 평면은 400π/3 W,
첫 층의 내·외부 313.333333…/306.666667… K라는 독립 산술 기준을 준다.
이 값들은 실제 토양이나 재료 권장치가 아니다.

유동은 m=0.1 kg/s, cp=1,000 J/(kg·K)로 선언했다. 독립 기준 NTU는 π/15,
출구는 300+20·exp(−π/15) K, 열은 100·(320−출구) W다.
층/soil 접촉 각각 10/(20π) K·m/W를 추가한 별도 사례에서는 총 저항이
5/(20π) K/W, 열이 80π W이고 층 내·외부 312/308 K, 토양 내부 304 K다.
두 층·길이 두 배·더 깊은 중심·토양 전도도 두 배는 같은 작성 조건의
독립 저항 조합으로 확인한다. 이 확인은 ht API 수치 재현과 adapter 계약 검사다.
토양 물성의 독립 측정·보정·예측 정확도 검증으로 집계하지 않는다.

미검토·null 조건은 ht import 전에 보류되는지 확인한다. 유량 0과 ΔT=0,
음의 열률, 부족한 온도/NTU/길이 지원, missing evidence, bool/NaN/Inf,
잘못된 버전과 수정된 receipt도 검사한다. 상세 수치·요청·결과는
artifacts/buried_thermal에만 두고 research 요약은 집계·단위·권리·의존 함수와
자체 파일 해시·한계만 담는다. 성공 수와 기준 수는 실행 생성된
buried_thermal_results.json을 따른다.

## 재현과 교체

```powershell
python -m pip install ht==1.2.0
python scripts/check_buried_thermal.py
python -m pytest -q tests/test_buried_thermal.py
python -m ruff check src/pipeline_integrity/buried_thermal.py tests/test_buried_thermal.py examples/buried_thermal.py scripts/check_buried_thermal.py
python -m ruff format --check src/pipeline_integrity/buried_thermal.py tests/test_buried_thermal.py examples/buried_thermal.py scripts/check_buried_thermal.py
```

이 Python 블록은 검증 스크립트가 실제 실행한다.

```python
import math
import runpy
from pipeline_integrity.buried_thermal import evaluate_buried_thermal, verify_buried_thermal_record

requests, context = runpy.run_path("examples/buried_thermal.py")["make_cases"]()
record = evaluate_buried_thermal(requests["uniform"], context)
verify_buried_thermal_record(record)
assert record["status"] == "computed"
assert math.isclose(record["values"]["heat_from_fluid_to_soil_W"], 400 * math.pi / 3, rel_tol=1e-12)
held = requests["uniform"].model_copy(update={"applicability_reviews": {}})
assert evaluate_buried_thermal(held, context)["status"] == "needs_data"
```

재료층·토양·film 공급자를 바꾸려면 새로운 선언 값·온도 지원·근거·검토를
넣은 새 요청을 만든다. 기존 요청·결과 digest는 유지하며 덮어쓰지 않는다.
ht를 바꿀 때는 expected_ht_version, 실제 함수 source와 독립 수치 기준을
다시 확인한다. 현재 함수는 임의 backend 주입 인터페이스가 아니다.
다른 물리 엔진이 필요하면 해당 소유 프로젝트의 정식 API로 연결하고 이
정상 열저항을 임의의 시간/수분 연성 solver로 승격하지 않는다.

## 발견·실패와 외부 확장 요청

독립 검토에서 유동의 평균 층 온도만으로 물성 지원을 검사하면 입구 국소
이탈을 놓치는 사례를 재현했다. 예제 flow의 층 지원 상한 313 K는 평균
내부 312.029650… K를 포함하지만 입구 국소 내부 313.333333… K를 포함하지
않는다. 토양에도 같은 문제가 생길 수 있다. 입·출구 국소 경계까지 지원을
검사해야 하며, 계산 평균을 현장 최고/최저 온도로 사용하지 않는다.
또한 backend 버전을 바꾸고 외부 digest를 다시 계산한 receipt가 검증을
통과하는 초기 경로를 발견했다. 검증은 검토한 버전/함수/소스 pin을 비교해야 한다.
이 반례들은 생산 코드를 임의로 수정하지 않고 소유자에게 전달했으며,
수정 후 재현 스크립트의 지원 이탈·오염 거부 조건으로 유지한다.
최종 producer는 입·출구 층/토양 경계를 추가했고 평균 출력·총 열은 그대로
유지했다. 저장 검증기는 현재 adapter/ht와 검토한 함수·버전·소스 해시를
대조한다. 두 문제의 최소 반례는 이제 지원 이탈/거부로 확인된다.

후속 외부 요청은 시간에 따른 토양/관 축열, 수분·포화도/동결·강우·지하수,
상태 의존 열물성, 비균일 지표 경계, 복수관 상호작용과 유체 과도의 정식
경계 계약이다. 소유 엔진은 asset/location/clock과 시각·부호, 구간별 열률 W,
내·외부 온도 K, 분리된 정상/축열 에너지, 물성 상태·지원·불확실성,
공간/시간 격자 잔차를 함께 반환해야 한다. 실제 소용량 토양 측정자료는
이번 작업에서 확보·적용하지 않았으며 synthetic software 검증만 수행했다.
독립 현장 열전도도·온도 이력·지표/수분 조건을 확보해 학습/검증 조건을
분리하기 전에는 soil calibration이나 표준 적합성을 주장하지 않는다.
