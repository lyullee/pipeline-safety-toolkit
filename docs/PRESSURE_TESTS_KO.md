# 압력 시험·가스 재고 변화 연결

`pressure_tests`는 기존 Mat의 시험 하중·이상기체 팽창 일·압력감소 재고 변화·
진공 압력상승4모델을 호출한다. 구성식과 원 스키마를 재작성하지 않고 기존
MaterialJob의 버전·단위·자산·시계·근거·상태·receipt를 유지한다. 시험 하중의
계산 성공은 시험 합격, 강도 적합 또는 공법 승인을 뜻하지 않는다.

## 재현과 Python API

```powershell
python scripts/acquire_data.py --only pe100_pressure_decay --workers 1 --maximum-seconds 60
python scripts/check_pressure_tests.py --materials PATH_TO_MAT
python -m pytest -q tests/test_pressure_tests.py
```

원 Mat는 별도 설치하거나 위 검증 명령에 소스 경로를 준다. 기본 계산 예제는
선택 물성 라이브러리 없이 선언한 압축성 계수를 사용한다. 물성 계산이 필요한
시나리오는 기존 thermo 선택 의존성 CoolProp을 사용한다.

```python
import runpy
from pipeline_integrity.pressure_tests import evaluate_pressure_test, evaluate_gas_decay_series

requests, frames, specification, context = runpy.run_path("examples/pressure_tests.py")["make_cases"]()
load = evaluate_pressure_test(requests["inspection.pressure_test_load"], context)
inventory = evaluate_gas_decay_series(frames, specification, context)
print(load["status"], inventory["values"]["total_net_moles_out"])
```

계수·허용치·체적·상태는 작성한 가상 기준이다. 실제 데이터에는 사용자가
검토한 원 근거와 조건을 입력한다. 상세 요청은 `PressureTestRequest(method,
parameters, asset_id, location_id, clock_id, applicability_reviews, evidence_ids)`다.
방법별 검토 키는 `REVIEWS[method]`로 조회한다. 미검토와 미지 파라미터는
원 엔진 import 전에 needs_data이며 다른 방법의 검토 키는 오류다.

| method | 원 입력의 핵심 | 출력·적용 한계 |
|---|---|---|
| inspection.pressure_test_load | design_pressure_Pa, declared_test_factor, allowable_at_test_Pa/design, fluid_density_kg_m3, elevation_below_gauge_m | 선언한 게이지 설계/시험 기준·허용치 비·아래쪽 고도차의 국소 하중 |
| inspection.gas_expansion_energy | 초기/최종 절대압력, initial_volume_m3, heat_capacity_ratio | 이상·가역·단열·일정 gamma 팽창 일과 최종 체적 |
| inspection.gas_pressure_decay | rigid_volume_m3, 두 절대 P/T/Z, duration_s, 기준 P/T/Z | 부호 있는 순 몰 유출률과 명시한 기준 상태의 체적 유출률 |
| inspection.vacuum_rise | rigid_volume_m3, 두 절대압력, duration_s, background_throughput_Pa_m3_s, constant_temperature_declared | 등온 강체의 전체 가스 부하와 배경 보정 값 |

시험 배율과 허용치는 호출자가 검토한 규칙이다. 고도는 게이지 아래의 양수
깊이를 뜻한다. 규격 배율·허용치 표를 내장하지 않았다. 팽창 일은 폭발 에너지,
실제 급격한 파열의 피해량 또는 주변 대기에 전달되는 유효 에너지와 다르다.
진공 부하는 외부 누설 외에 탈기/방출도 포함할 수 있다. 음의 배경 보정 값을
0으로 잘라 합격값으로 만들지 않는다.

## 압력·온도 이력

`GasDecaySpec`는 기존 SignalFrame의 pressure_channel/temperature_channel,
압력 기준, 필요 시 `ambient_pa_abs`, 고립 강체 체적과 기준 P/T/Z를 받는다.
압력이 gauge이면 대기압이 필수다. differential을 절대 상태로 사용할 수 없다.
임의의1기압·표준 리터·체적·온도를 기본값으로 넣지 않는다. 온도와 압력 단위는
기존 단위 서비스로 K와Pa로 변환한다.

대표 기체 온도와 고립·강체·고정 조성은 각각
`gas_temperature_representative_reviewed`, `isolated_rigid_fixed_composition_reviewed`로
검토한다. 검토의 True는 호출자의 선언이며 독립적인 현장 자격을 부여하지 않는다.
체적 변화·열 구배·투과·탈기·기체 흡수/용해 또는 유입이 미해결이면 순 재고
변화로 누설 경로의 유량을 식별할 수 없다.

원 순서·시계·채널·단위·valid를 유지하며 중간 결측이나 maximum_interval_s를
넘는 공백을 지우거나 끝점만으로 성공 처리하지 않는다. 모든 인접 구간과 전체
끝점의 원 모델을 호출하고, 구간 순 몰 유출의 적분과 전체 재고 차이를 비교한다.
순 유출이 음수인 구간도 보존한다. 실패/보류 구간이 있으면 전체 값을 발표하지
않는다. 입력 해시와 실패 인덱스·전체 원 receipt로 다시 확인할 수 있다.

## 물성 공급자와 R 기준

`compressibility_source="declared_constant"`에는 선언한 운전 Z와 기준 Z를 준다.
`"provider"`에는 fluid/backend를 제공한다. 조성 미상의 propane–butane을 순물질로
대체하지 않는다. `CoolPropMolarProvider`는 기존 CheckedProvider/CoolPropProvider의
상태·phase 검사를 재사용하고 CoolProp의 Dmolar를 추가 조회한다. 공급자가 명시한 비기체상은
out_of_scope이며 거부한 상태도 보존한다. 물성 해석 불능은 오류로 반환한다.

원 Mat의 R은8.31446261815324 J/(mol K)다. 물성 모델에 사용된 기체상수 관례와
충돌하지 않도록 molar density에서 `Z_native=P/(rho_molar*R_native*T)`를 만들어
원 입력에 사용한다. 이는 명시한 R 기준으로 표현한 계수이며 공급자의 Z를
그대로 복사한 값이라고 주장하지 않는다. 공급자/판·P/T·상·몰밀도·사용한 R/Z를
기록하고, 실제 native 몰 재고가 공급자 몰밀도×체적과 같은지 검증한다.

사용자 공급자는 `resolve(StateRequest)->MolarGasState`를 구현해 provider=로
교체할 수 있다. 정확한 fluid/backend/P/T, 양의 몰밀도, 상과 공급자 정보를
반환해야 한다. 다른 유체나 상태로 변경하면 오류다. 조회 변수는
[CoolProp High-Level API](https://coolprop.org/coolprop/HighLevelAPI.html)를 따른다.

## 공개 PE100RC 자료의 확인 결과

Miloš Mičian·Radoslav Koňár의
[Zenodo 자료](https://doi.org/10.5281/zenodo.20582921)는2026-06-07 공개 v1이다.
[DOI 권리 등록](https://api.datacite.org/dois/10.5281/zenodo.20582921)의 CC BY 4.0과
공급자MD5를 확인해78,721바이트 workbook1개를 취득했다. 원본과 변환 행은
배포에서 제외하고 카탈로그·취득 코드·집계·권리/체크섬을 제공한다.

파일에는5가스×2모세관 조건의10이력, 각각0~900초의31행이 있다. 공급자 설명과
달리 파일에서는 무결함 기준 이력이나 별도 주변온도 채널을 확인할 수 없었다.
하나의 보고 온도 열이 대표 기체 온도인지도 파일만으로 확정하지 않는다.
체적·대기압·PE의 체적 변형·PB 혼합비를 추정해 채우지 않는다.

`load_pe100_pressure_decay`는 원 checksum·sheet/헤더·기체별 두 열 쌍·원 행·
온도/게이지압과 시계를 유지한다.10직접 재고 계산은 미지 조건으로 보류했다.
별도 가정0.01m³·101325Pa 대기압·대표 gas T·강체를 선언하면단일 지정 유체 모델4종(N2·기준 Air·Ar·CO2)의
8시나리오는 계산되며 PB2개는 조성 미상으로 계속 보류한다. 이 가정 계산은
실제 PE 시험의 누설 유량·용적·합격 여부를 검증한 것이 아니다. 공기 조건의
Air는 선언한 기준 물성 모델이며 실제 공기의 습도/조성을 측정한 것이 아니다.

23개 새 자동 검사와 실제4원모델·12개 독립 작성 기준을 확인했다. 냉각만으로
압력이 감소하지만 몰수는 같은 사례, 음의 순 유출/배경 보정, 국소 고도 하중,
전체 이력/끝점 재고 일치와 공급자 상태 거부를 포함한다. 공개 이력의 음의
순 유출5구간도 남겼다. 원 소스는 실행 전후 동일했고 결과/해시는
`research/pressure_tests_results.json`, 전체 이력/receipt는 로컬 artifacts에 있다.

다음 확장은 계측 불확실성/공분산·용적/변형·기체 온도 대표성·변하는 대기압·
조성/탈기/투과의 분리, 원 규칙 공급자의 시험 승인과 실제 현장 검증이다.
