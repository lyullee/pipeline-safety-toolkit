# 기체 차압계와 실가스 기준 체적

`gas_meter`는 기존 `CoolPropProvider`의 HEOS 상태와 Fluids1.3.1의 실제 차압계
solver·계수·팽창 보정 API를 연결한다. 일정한 계수 쌍 또는 원 라이브러리 상관식을
하나의 요청에서 선택한다. 원관·노즐·벤투리·콘·웨지의 서로 다른 2차 치수 의미를
보존하며 표준 계수나 표 문장을 이 저장소에 복사하지 않는다.
기존 `instrument.liquid_dp_meter`도 적용 가능한 원형 치수와 계수 범위에서
비압축성 한계의 별도 대조에 재사용한다.

이 결과는 연구용 유량 추정이다. 표준 이름은 원 라이브러리의 selector이며
ISO 적합성, 검정·거래 정확도, 설치 적합성이나 실제 계기의 독립 교정을 뜻하지 않는다.
현재 입력에는 압력 탭 위치·형상·상관식 또는 교정 계수의 적용 범위를 검토한
근거가 필요하다. 측정 자료가 부족하면 계수·조성·기준 상태를 추측하지 않는다.

## 설치와 실행

추가 의존성 변경 없이 기존 Fluids1.3.1·CoolProp8.0.0을 사용해 확인했다.
필수 핵심 설치에 두 라이브러리를 강제하지 않으며 미검토·미상 입력은
두 선택 라이브러리를 import하기 전에 `needs_data`로 반환한다.

```powershell
python -m pip install -c requirements-tested.txt ".[engineering,thermo,dev]"
python examples/gas_meter.py
python scripts/check_gas_meter.py
python -m pytest tests/test_gas_meter.py -q
```

```python
from examples.gas_meter import make_case
from pipeline_integrity.gas_meter import evaluate_gas_meter, verify_gas_meter_record

request, context = make_case()
record = evaluate_gas_meter(request, context)
assert record["status"] == "needs_data"
assert record["native_called"] is False
assert verify_gas_meter_record(record, request, context)["valid"]
```

`make_case(reviewed=True)`는 작성한 수치 기준의 검토를 뜻하며 실제 계기의
검토 선언을 자동 작성하는 도구로 쓰지 않는다. `available_gas_meter_types()`는
현재 원 라이브러리의 selector와 소스 pin을 명시적으로 조회한다.

## Python 요청과 입력 의미

`GasMeterRequest`는 자산·계기·위치·사건·시계, 관측 `time_s`와 오프셋,
명목상 양의 방향 포트 둘과 실제 upstream/downstream 포트를 받는다.
`GasMeterGeometry`의 치수는 m, 압력은 **절대 Pa**, 온도는 K다.
게이지 압력 변환은 원 대기압 근거를 가진 상위 자료 변환에서 수행한다.

| 입력 | 의미 |
|---|---|
| `inner_pipe_diameter_m` | 압력 탭 부근 관의 내부 지름 |
| `secondary_dimension_m` | `circular_throat_diameter`, `cone_end_diameter`, `wedge_flow_height` 중 명시한 치수 |
| `native_meter_type` | 실제 Fluids selector, 임의의 형상 변환을 하지 않음 |
| `taps`, `tap_position`, `tap_description` | 원 계기의 실제 탭 선택과 위치 근거 |
| `upstream_pressure_pa_abs`, `downstream_pressure_pa_abs` | 지정한 두 탭의 절대압력, 어느 쪽도 자동 추정하지 않음 |
| `upstream_temperature_K` | 실제 upstream 관측 온도, downstream 온도가 아님 |
| `components` | `GasComponent(fluid, mole_fraction)`의 명시 조성, 자동 정규화 없음 |
| `reference` | `GasVolumeReference`의 실제 기준 압력·온도·조성 보존 체적 기준과 근거 |
| `support` | 검토한 β·Re·upstream P/T 범위와 ΔP/P·upstream Mach 상한 |

`native_correlation`은 `differential_pressure_meter_solver`에서 유량을 구하고
그 유량의 `differential_pressure_meter_C_epsilon`을 다시 평가한다. 점성·밀도와
Re의 결합을 유지한다. `specified_pair`는 사용자가 검토한
`specified_discharge_coefficient`와 `specified_expansibility_factor`를
`flow_meter_discharge`에 직접 공급한다. 두 계수 중 하나를 기본값으로 채우지 않는다.
두 모드 모두 원 라이브러리의 β 변환과 관계식을 사용한다.

실제 탭 selector가 없는 orifice 상관식은 보류하며 원 라이브러리의 기본
corner 탭을 추정해 쓰지 않는다. 각 selector의 원 지원 여부를 실제 호출에서
확인하고 오류도 원 입력과 함께 남긴다. Hollingshead 보간은 현재 라이브러리의
실제 knot 축을 검사해 범위를 벗어나는 외삽을 거부한다. 다른 상관식의 현장
적용 범위를 한 가지 표로 추정하거나 표준의 전체 요구 사항으로 주장하지 않는다.

조성은 HEOS 유체 이름 또는 명시한 성분 몰분율로 전달한다. 중복 이름과
합계가1이 아닌 입력을 고치지 않는다. 실제 backend에서 같은 성분으로 해석하는
Nitrogen/N2 같은 별칭 중복도 거부한다. `same_composition_dry` 선언에 양의
Water/H2O 또는 R718 같은 실제 물 별칭을 넣을 수 없다. wet 상태는 실제 조성과
상 판정이 지원될 때만 동일 조성으로 다룬다.

`geometry`, 조성·체적 기준, P/T, 관측 시각, 기준 상태와 적용 범위는
미상 `None`으로 남길 수 있다. 이때 전체 요청은 `needs_data`이며 수치 계산은 없다.
NaN/무한대는 자료 부족이 아니라 invalid 입력이다. 복제한 요청도 Python 값에서
다시 검증하여 NaN을 null로 바꾸지 않는다.

## 상태와 압축성의 구분

HEOS가 upstream과 기준 상태의 밀도·점성·음속·Cp·Cv·상과 압축성 계수 Z를
계산한다. 일반 gas 및 임계압력 아래의 `supercritical_gas`를 구분해 보존한다.
액체·두 상·임계압력과 임계온도를 모두 넘은 상태를 이 경계의 기체 상관식에
자동 투입하지 않는다. 상을 강제로 지정하지 않으며 실패한 물성 요청도 기록한다.

`local_isentropic_rho_a2_over_P`는 **ρa²/P**라는 국소 실가스 등엔트로피 지수를
사용한다. 이는 일반적으로 Cp/Cv와 다르다. `reviewed_cp_cv_approximation`은
검토한 근사 선택이며 두 값과 실제 native에 전달한 지수를 모두 남긴다.
이 선택이 차압계 팽창 상관식을 완전한 실가스 노즐 해석으로 바꾸지는 않는다.

현재 ΔP/P의 최대 지원값0.25와 upstream Mach 최대0.3은 이 연구 경계의
공학적 범위 제한이다. 표준 조항·제조사 인증 한계로 해석하지 않는다. 사용자가
그보다 엄격한 `support`를 주면 그대로 적용한다. 측정한 탭 압력비·upstream Mach와
기록한 이상기체 임계 압력비는 진단이다. 탭 압력을 실제 throat나 vena contracta
압력으로 바꾸지 않으며 이것만으로 실가스 choking 부재를 증명하지 않는다.
정상·비폐색 조건과 계기의 적용성을 검토한 별도 근거가 필요하다.

명목 방향과 반대인 흐름을 자동 절댓값 처리하지 않는다. 원 상관식의 역방향
재사용은 보류한다. 역방향 전용 계수 쌍과 실제 형상 검토·근거를 명시한 경우만
실제 upstream 상태로 계산하고 명목 방향 기준 음의 유량을 반환한다.
ΔP0의 지정 계수 관계는 정확한0 한계가 있다. Re 의존 원 계수는 Re0에서
정의되지 않아 이를 정상 교정 결과로 계산하지 않고 별도로 보류한다.

## 실제 체적과 기준 체적

질량유량은 유지하고 실제 체적은 `m/rho_upstream`, 기준 체적은
`m/rho_reference`로 구한다. 같은 조성의 P/T/Z 비와 밀도비도 대조한다.
`signed_actual_volume_flow_m3_s`와 `signed_reference_volume_flow_m3_s`는 서로
다른 상태량이다. NTP·STP·normal·standard 같은 라벨만 보고 P/T를 채우지 않는다.
wet→dry 조성 변경, 물 제거, 수분 분압의 추정은 하지 않는다.

`require_reference_volume=False`와 `reference=None`이면 기준 체적은 계산하지
않고 None으로 남긴다. 질량·실제 체적 계산이 가능해도 기준 체적0을 만들지 않는다.
`audits.basis_digest`는 조성·체적 기준·기준 상태·HEOS를 함께 식별한다.
근거와 `RunContext.metadata.gas_meter_boundary=boundary_metadata(request)`는
계기·포트·사건·오프셋과 이 기준을 결합한다. 모든 선택·형상·관측·조성·계수·
적용 범위·검토 근거 ID는 실제 `RunContext.evidence`에 있어야 한다.

## 기록 검증과 신호 연계

`evaluate_gas_meter(request, context)`는 원 물성·meter 호출 입력과 반환값,
실패 상태, 원 라이브러리 버전·소스 해시·CoolProp binary/revision, 압력 기준과
계수·체적 변환 잔차를 남긴다. 원 계산기를 복제하지 않는다.
`verify_gas_meter_record(record, request=None, context=None)`는 현재 소스와 실제
라이브러리에서 같은 요청을 다시 계산해 원 상태·수치·기준·부호·판정을 대조한다.
직렬화한 JSON도 같은 의미로 검증한다. 해시는 서명이 아니며 과거 실행이나
검토자의 승인 사실을 인증하지 않는다.

`to_signal_frame(record, context)`는 검증된 computed 기록의 부호 있는 질량 kg/s,
실제 체적 m3/s, 기준 체적 m3/s와 탭 차압 Pa를 전달한다. 시각은 관측 시간에
명시 오프셋을 한 번 더한 값이다. 미요청 기준 체적은 None/valid=False다.
측정 입력이 있어도 모델이 추정한 유량의 origin은 simulation이며 작성한
합성 사례만 synthetic이다. `to_signal_frames`는 같은 context·체적 기준에서
원 시각의 엄격한 증가를 요구하며 시간 순서를 고치거나 정렬하지 않는다.

## 검증과 확장 요청

검증은 작성한 계수 관계의 독립 스칼라식, Re 의존 관계의 별도 bracket 해,
실제 순수기체·혼합물의 질량 왕복과 P/T/Z 보정, γ와 Cp/Cv 차이, 형상별 native
호출,0·역류·결측·위상·외삽 거부와 재해시한 기록 조작을 다룬다. selector별
연결 성공은 독립 실험 교정이 아니며 같은 식을 반복한 검사를 새 정답처럼 세지 않는다.
[검증 JSON](../research/gas_meter_results.json)에 실행 범위와 파일 해시를 기록한다.

후속 불확실성 연결에는 실제 관측·기준 계기·형상·P/T·차압·조성의 공분산,
몰분율 합계 제약, 공동 교정 편향과 적용 범위를 명시해야 한다. native 상관식
선택의 모델 오차와 계수 교정 불확실성은 입력 센서 공분산과 별도다. 온도·압력
오차를 줄 때 같은 HEOS 상태를 함께 다시 계산해야 하며 밀도·음속·점성을 서로
독립인 기본 입력처럼 중복 표본화하지 않는다. 다른 저장소의 수신 API에는 이
요청/근거/basis digest와 원 시계, 실패·보류 상태를 그대로 넘기는 계약을 요청한다.
측정 오차 전파만으로 거래 검정·제조사 교정·표준 적합성이 완성되지 않는다.

## 원 구현과 이용 조건

[Fluids 공식 차압계 문서](https://fluids.readthedocs.io/fluids.flow_meter.html)와
[CoolProp 공식 상태 API](https://coolprop.org/coolprop/HighLevelAPI.html)를 확인했다.
MIT 원 구현을 호출하며 두 라이브러리의 코드·계수표·바이너리를 이 배포에
복사하지 않는다. 원 고지를 유지하고 수치 자료·실제 교정 기록의 이용 조건은
별도로 확인한다. 설명·예제·검증 조건은 이 프로젝트에서 작성했다.
