# 용접 지표·전기 파형·온도 이력 연결

`welding_exchange`는 기존 Mat의 용접 관련 8개 모델을 호출한다. 이 패키지는
자산·위치·재료 상태·시계·근거와 입력 단위, 이력의 결측·공백·부호를 확인하고
원 `MaterialJob`과 receipt를 보존한다. 용접 구성식이나 회귀 엔진을 복제하지
않는다. 계산한 지표는 예열 온도, 균열 확률, 허용 결함 또는 용접 절차의 자격을
부여하지 않는다.

## 재현과 Python API

저장소 루트에서 실행한다. 기존 Mat의 `matintegrity`가 import 가능한 환경이
필요하며, 검증 스크립트에는 `--materials PATH_TO_MAT`로 소스 루트를 줄 수 있다.
Tandem-GMAW 기록 읽기에는 `h5py`, EBW workbook 읽기에는 `openpyxl`이 필요하다.

```powershell
python scripts/acquire_data.py --only tandem_gmaw ebw_thermal --workers 2 --maximum-seconds 300
python scripts/check_welding.py --materials PATH_TO_MAT
python scripts/check_ebw_thermal.py --materials PATH_TO_MAT
python scripts/check_tandem_welding.py
```

```python
import runpy

from pipeline_integrity.welding_exchange import (
    evaluate_weld_thermal,
    evaluate_weld_waveform,
    evaluate_welding,
)

cases, wave, wave_spec, thermal, thermal_spec, context = runpy.run_path("examples/welding_methods.py")[
    "make_cases"
]()
results = {name: evaluate_welding(case, context) for name, case in cases.items()}
electrical = evaluate_weld_waveform(
    wave,
    wave_spec.model_copy(update={"travel_distance_m": None, "efficiency": None}),
    context,
)
cooling = evaluate_weld_thermal(thermal, thermal_spec, context)
print({name: result["status"] for name, result in results.items()})
print(electrical["status"], electrical["electrical_diagnostic"]["values"])
print(cooling["status"], cooling["native_result"]["values"])
```

예제의 전기·온도·조성·경도·형상과 판정 규칙은 독립 작성한 가상 입력이다.
예제의 검토 플래그를 실제 자료에 그대로 적용하지 않는다. 위 파형 호출은
거리와 효율이 미상인 경우 전기 적분만 남고 입열 계산은 보류되는 예다.

`WeldingRequest`는 `method`, `parameters`, `asset_id`, `location_id`, `clock_id`,
`material_condition_id`, `evidence_ids`, `applicability_reviews`를 받는다.
이력에는 `maximum_sample_interval_s`와 `maximum_samples`도 적용한다.
조성 모델은 별도의 `chemistry_basis`가 필요하다. `material_condition_id`는
호출자가 선언한 상태 식별자이며 재료 상태나 공정 적합성을 자동 확인하지 않는다.

방법별 검토 키는 `REVIEWS[method]`에서 조회한다. 해당 방법의 모든 검토가
참이어야 원 모델을 호출한다. 근거 ID는 중복 없이 `RunContext.evidence`에
존재해야 하며 요청과 context의 자산·시계가 같아야 한다. 원 모델의 버전,
입력 스키마 해시, 출력 단위·확률 의미와 원 receipt의 해시를 함께 확인한다.

## 원 모델의 입력과 결과

모든 길이·시간·질량·온도는 각각 m·s·kg·K가 원 모델의 입력 기준이다.
조성은 아래 두 모델의 기준을 구분한다. 구체적인 현행 입력 스키마와 출력
단위는 기존 `MaterialClient.describe(method)`로 조회할 수 있다.

| method | 핵심 입력 | 결과와 적용 조건 |
|---|---|---|
| `welding.arc_energy` | `voltage_V`, `current_A`, `travel_speed_m_s`, `efficiency` | 양의 대표 전압·전류·이동 속도와 0 초과 1 이하 효율로 `arc_energy_J_m`, `net_heat_input_J_m`을 계산한다. 정상 대표 전기 상태와 속도·효율의 근거가 필요하다. |
| `welding.waveform_energy` | `time_s`, `voltage_V`, `current_A`, `travel_distance_m`, `efficiency` | 같은 시각의 전압·전류 곱을 시간 적분한 `arc_energy_J`와 거리당 `net_heat_input_J_m`을 구한다. 전 이력·양의 실제 이동 거리·명시 효율이 필요하다. |
| `welding.carbon_equivalents` | `C`, `Mn`, `Cr`, `Mo`, `V`, `Ni`, `Cu`, `Si`, `B`; `chemistry_basis="mass_percent"` | 질량 백분율 기준 `CE_IIW`, `Pcm` 지표다. 모든 지정 원소의 분석값과 강종·지표 적용 범위를 검토해야 한다. 예열 조건이나 균열 확률로 변환하지 않는다. |
| `welding.cooling_interval` | `time_s`, `temperature_K`, `upper_temperature_K`, `lower_temperature_K` | 명시한 비증가 냉각 가지에서 두 온도 교차 시각과 `cooling_interval_s`를 계산한다. 두 교차를 실제 선택 구간이 포함해야 한다. |
| `welding.dilution` | `constituents`, `requested_elements`; `chemistry_basis="mass_fraction"` | 용융 질량 합, 역할별 질량분율, 요청 원소의 혼합 질량분율이다. 모재·용가재·이전 층의 실제 용융 질량과 완전 혼합 가정을 검토한다. |
| `welding.hardness_traverse` | 시험 방법·하중·유지 시간·불확실성 설명, 위치별 경도와 영역별 상한 | 측정한 영역의 최대 경도와 선언 상한을 비교한다. 경도·확장불확실성·상한의 원 입력 단위는 Pa다. 미측정 영역은 미상으로 남는다. |
| `welding.heat_treatment_history` | `time_s`, `sensor_temperature_K`, `minimum_hold_temperature_K`, `maximum_hold_temperature_K` | 모든 선택 센서가 동시에 온도 대역에 머문 구간·전체/최장 연속 유지 시간, 가열·냉각 속도와 센서 간 최대 차이를 계산한다. 센서 대표성과 선언 대역을 검토한다. |
| `welding.joint_profile` | `transverse_position_m`, `measured_surface_height_m`, `excluded_joint_half_width_m`, `fit_method` | 양쪽 모재의 선 적합으로 접합부 기준의 부호 있는 표면 높이차 m·각도차 rad와 각 측 기울기·RMSE를 구한다. 좌표 정합과 모재 지지 구간을 명시해야 한다. |

탄소당량의 질량 백분율과 희석의 0~1 질량분율을 자동 환산하지 않는다.
분석하지 않은 원소를 0으로 채우거나 강종 명칭에서 조성을 가져오지 않는다.
희석의 각 구성재는 `material_id`, `role`, `melted_mass_kg`,
`composition_mass_fraction`을 가지며 역할은 `base`, `filler`, `previous_layer`다.
한 구성재의 요청 원소가 미상이면 그 원소의 혼합 조성도 미상으로 남긴다.
와이어 공급량이나 단면 면적을 질량으로 간주하지 않는다.

경도 요청은 `method_id`, `test_force_N`, `dwell_s`,
`uncertainty_coverage_description`, `points`, `declared_region_upper_limits_Pa`를
받는다. 각 점에는 `point_id`, `position_m`, `region_id`, `hardness_Pa`,
`expanded_uncertainty_Pa`가 필요하다. 통상 경도 표기의 숫자를 단위 검토 없이
Pa 필드에 넣지 않는다. 확장불확실성의 포괄 의미와 각 영역의 판정 규칙은
호출자의 명시 근거다. 선언 상한을 내장 규격 상한으로 해석하지 않는다.

접합부 형상은 기존 Mat의 `ols`, `theil_sen`, `soft_l1` 적합을 호출한다.
`soft_l1`에는 잔차 척도 `robust_residual_scale_m`의 근거도 필요하다.
접합부 제외 반폭 안의 형상을 모재로 적합하거나, 다른 측정 위치·방향의
프로필을 정합 없이 합치지 않는다. 적합 기법 선택만으로 측정 불확실성이나
허용 각도·단차가 결정되지 않는다.

## 전기 파형의 부호·시계·입열

`evaluate_weld_waveform(frames, specification, context, client=None)`는 기존
`SignalFrame`의 전압·전류 채널을 받는다. `WeldWaveformSpec`에는 서로 다른
`voltage_channel`, `current_channel`, 최대 샘플 간격·샘플 수, 동일 관측 창의
`travel_distance_m`, `efficiency`, 전기 근거 ID와 공통 자산·시계·근거가 필요하다.
단위 서비스로 각 샘플을 V와 A로 변환하고 같은 시각의 곱을 사용한다.
평균 전압×평균 전류와 전압·전류 곱의 평균은 일반적으로 다르다.

`electrical_synchronization_and_polarity_reviewed`는 채널 동기화와 극성 검토다.
이 검토가 없으면 전기 적분을 시작하지 않는다. 검토 후에는 기존
`lifecycle_exchange.integrate_power_frames`로 부호 있는 전력을 적분해
`electrical_diagnostic`에 유입·유출 에너지 J와 관측/포괄 시간을 남긴다.
음의 전압·전류 곱을 절댓값으로 만들거나 0으로 자르지 않는다. 음의 곱이
포함되면 전기 진단은 보존하되 비음 전력을 전제로 하는 원 입열 모델은
`out_of_scope`로 보류한다.

`distance_efficiency_and_window_reviewed`와 양의 거리·0 초과 1 이하 효율이
모두 있어야 입열로 연결한다. 이 값이 미상이면 `needs_data`와 전기 진단을
반환한다. 전기 에너지를 그대로 재료에 전달된 열로 보고하지 않는다.
로봇 좌표의 이동 길이, 정격 용접 속도 또는 전기 파형만으로 용융부의 유효
거리와 열전달 효율을 추정하지 않는다.

선택 이력은 동일한 시계와 정렬된 완전한 채널을 가져야 한다. 결측·invalid
샘플이나 선언 최대 간격을 넘는 구간이 있으면 전체 전기 이력이 완전하다고
판정하지 않는다. 두 끝점만으로 중간 공백을 없애거나 별도 기록을 자동 보간해
합치지 않는다. 기본 `maximum_samples`는 100,000이며 필요한 관측 창 또는
상향한 예산을 호출자가 명시한다.

## 온도 이력·냉각 가지·동시 유지

`evaluate_weld_thermal(frames, specification, context, client=None)`는
`WeldThermalSpec`의 `kind="cooling"` 또는 `"hold"`를 사용한다.
`temperature_channels`, `lower_temperature_k`, `upper_temperature_k`, 최대 간격과
샘플 수를 선언한다. 두 온도는 양수여야 하며 cooling은 상한이 하한보다 커야
한다. `sensor_locations_temperature_and_rule_reviewed`는 센서 위치·온도 기준·
선언 열 이력 기준의 검토이며 절차 승인 플래그가 아니다.

cooling은 센서 하나와 원값이 비증가하는 구간을 명시적으로 선택한다.
원 전체 이력에 가열·재가열 또는 측정 잡음에 의한 상승이 있으면 이를 숨겨
냉각 가지로 만들지 않는다. 상승을 포함한 요청은 `out_of_scope`다. 자동 가지
검색·평활화·누적 최솟값 변환을 하지 않으며, 명시한 두 교차를 포함하지 못한
구간에서 냉각 시간을 외삽하지 않는다.

hold는 여러 센서의 같은 시각 온도를 `sensor_temperature_K`의 센서별 벡터로
원 모델에 전달한다. 선언 대역 안에 모든 센서가 함께 있는 구간을 평가하며
각 센서의 유지 시간을 따로 더하지 않는다. °C 입력은 단위 서비스로 K로
변환한다. 결측·invalid 온도, 시간 공백, 센서 위치나 대역의 미검토는
`needs_data`로 남긴다. 관측 지점의 이력은 접합부 전체의 열처리 균일성을
자동 입증하지 않는다.

## BAM Tandem-GMAW 공개 기록

Prashant Kumar Chaurasia·Cagtay Fabry·Andreas Pittner·Michael Rethmeier의
[Tandem-GMAW v2.0.0](https://doi.org/10.5281/zenodo.21824586)은 CC BY 4.0 자료다.
선택한 `experiment_list.csv`, `WID631_L1.zip`, `WID631_L2.zip`의 합계는
231,713,903바이트이며 공급자 MD5를 확인했다. 선택 범위는 한 시험편 WID631의
두 층이며 공개 자료의 전체 30 pass를 취득·적용한 것으로 집계하지 않는다.
두 층과 같은 시험편의 시간 샘플을 독립 시험으로 분리하지 않는다.

`welding_data.read_tandem_recording(archive, kind="process")`는 선택 ZIP의 공급자
체크섬을 다시 확인하고 T1의 process 또는 robot NetCDF 멤버를 읽는다.
`kind`는 `"process"` 또는 `"robot"`이며, 선택된 두 ZIP 외 파일과 변경된
단위·채널·시계·배열 형식은 거부한다. ZIP을 파일 시스템에 풀지 않는다.
반환 `WeldRecording`의 `time_s`, `values`, `valid` 배열은 읽기 전용이다.
`summary()`는 출처·해시·시계·품질 집계를 제공하며 원 샘플 배열이나 로컬
파일 경로를 포함하지 않는다.

L1/L2 전기 process 기록은 각각 408,242/288,572샘플이다. 공칭 10 kHz이지만
4 ms/1 ms 공백, 전기 채널별 NaN과 다수의 음의 전압·전류 곱이 남아 있다.
`voltage_leading`, `current_leading`, `voltage_trailing`, `current_trailing`을
각각 V·A로 유지한다. 극성을 바꾸거나 공백·결측을 제거해 입열 계산용의
완전 이력으로 바꾸지 않는다.

robot 기록은 각각 146,527/134,063샘플이고 공칭 1 kHz다. `thermo_1`, `thermo_2`는
°C이며 절대영도 미만의 유한한 T2 값은 원 배열에 유지하면서 invalid로
표시한다. `tcp_coords`, `tcp_angles`의 좌표 NaN도 보존한다. 단위가 빈 채널에
물리 단위를 추정해 붙이지 않으며 로봇 좌표를 자동으로 용접 경로로 해석하지
않는다.

`recording.frames(*, start_s=None, finish_s=None, channels=(...),
common_origin_utc=None, maximum_samples=100000)`는 `(frames, selection)`을 반환한다.
경계는 원 기록 기준 초이고 양 끝을 포함한다. `channels`는 중복 없는 명시적
tuple이어야 하며 scalar 1차원 채널만 frame으로 변환한다. 좌표 벡터는 원
배열에 남긴다. 선택 안의 샘플은 순서대로 모두 유지하고 보간·재표본화·다운
샘플링을 하지 않는다. 기본 예산을 넘으면 관측 창을 줄이거나 예산을 명시적으로
올려야 한다.

process의 nanosecond tick과 robot의 millisecond tick은 각 기록이 선언한 UTC
기원과 함께 해석한다. 두 기록을 비교할 때는 `common_origin_utc`를 명시해
같은 기원으로 옮긴다. 공통 기원은 선택한 관측보다 늦을 수 없다. 이 기능은
시각 기준을 맞출 뿐 서로 다른 샘플 주기를 자동 정렬하지 않는다. `selection`에
원 인덱스·선택 경계·공통 기원·표본 수·해시와 생략 없음이 남는다.

## EBW 공개 열 이력

Raghawendra Pratap Singh Sisodia·Piotr Sliwinski·Marek St. Weglowski의
[전자빔 용접 열 이력 v1](https://doi.org/10.17632/c7d9mt9h9x.1)은 CC BY 4.0 자료다.
원 ZIP 368,314바이트의 공급자 SHA-256을 확인했다. 다섯 workbook의 일곱
열전대 이력이며, 같은 실험의 센서와 시각 행을 독립 시험으로 분리하지 않는다.
원 파일명의 `S1300Q`와 sheet의 `S1300QL` 식별자를 모두 남기고 임의의 재료
명칭으로 통일하지 않는다.

`ebw_data.load_ebw_thermal(archive)`는 공급자의 내부 ZIP을 직접 받거나 그 파일 하나만
담은 작은 다운로드 ZIP을 받는다. 다운로드 ZIP은 최대 크기·일반 파일 여부·경로·멤버 수를
제한하고 디스크에 풀지 않는다. 내부 멤버의 정식 파일명·바이트 수·SHA-256을 검증한 뒤에만
workbook을 연다. wrapper를 쓴 경우 반환값의 `download_container`에는 wrapper 파일 해시,
내부 멤버 경로/크기/CRC 확인과 검증된 publisher SHA를 분리해 남긴다. CRC32는 ZIP 전송 중
손상 검사로만 기록하고 publisher 신원은 pinned SHA-256으로 확인한다. 다운로드 포장 ZIP의
출처 영수증을 다시 만들려면 `python scripts/ingest_ebw_download_wrapper.py`를 실행한다.
이 스크립트는 해시 봉인된 집계만 쓰며 원 관측 행을 영수증에 복사하지 않는다. 반환값은 출처 정보와
`experiments`, `records`를 담은 해시 봉인 JSON이며 각 센서 record의 `samples`에 원 행 번호 `source_row`, `time_s`,
`temperature_c`, `temperature_k`, `time_state`, `temperature_state`를 유지한다.
원 센서 위치와 명칭 차이도 남긴다. 열전대가 같은 workbook에 속한다는 사실은
보존하되 다른 실험들 사이의 공통 절대 시계는 추정하지 않는다.

`ebw_data.verify_ebw_thermal_source(record, archive)`는 봉인과 schema를 확인한 뒤
같은 reader로 원본을 다시 읽어 실험·센서·샘플 및 출처 영수증 전체가 일치하는지
검증한다. JSON digest만 다시 계산한 변경도 원본 replay와 다르면 거부한다.
이는 reader 재현성과 출처 연결 확인이며 온도 측정의 정확도나 물리 모델의 타당성을
입증하지 않는다.

`select_ebw_cooling_branch(record, *, upper_temperature_k=1073.15,
lower_temperature_k=773.15)`는 명시한 상하 온도를 통과하는 원 냉각 구간을
엄격한 규칙으로 검토한다. 기본 온도는 각각 800°C와 500°C다. 떨어진 위치의
동일 전역 최대값, 복수의 교차 그룹, 결측·잘못된 시계, 두 교차를 포함하는
구간 내부의 온도 상승이 있으면 `needs_data`로 남긴다. 선택된 구간에는
`status="selected"`, 원 record 해시, 선택 규칙·원 인덱스/행 범위·샘플과 원
모델용 `parameters`가 남는다. 이 선택 영수증을 확인하고 센서·온도 기준을
검토한 뒤 원 모델 요청을 구성한다. 원 전체 센서 이력도 그대로 유지한다.

온도·시각·센서 위치는 원 관측을 기준으로 읽는다. 파일명에 적힌 냉각 시간을
온도 이력 대신 입력하지 않는다. 원 전체 이력에는 가열·재가열·잡음이 포함될
수 있으므로 냉각 계산에는 센서·시각 경계·상하 교차 온도가 명시되고 원값이
비증가하는 가지가 필요하다. 단조 가지를 얻기 위한 숨은 필터나 원값 수정은
허용하지 않는다. 이 열 이력의 계산은 압력 배관의 용접 절차·조직·경도·현장
성능을 검증한 것이 아니다.

## 상태·근거·배포 경계

`needs_data`는 필수 검토·분석값·거리·효율·센서 근거가 없거나 완전 이력을
구성하지 못했음을 뜻한다. `out_of_scope`는 선택 냉각 가지의 상승이나 원
입열 모델과 맞지 않는 음의 전기 교환 등 적용 조건 밖의 요청에 사용한다.
잘못된 자산·시계·근거, 다른 방법의 검토 키, 반복·역순 시각, 벡터/단위 오류와
샘플 예산 초과는 입력 오류다. 누락 값을 기본 강종·0·대표 효율로 채워 성공
처리하지 않는다. 원 모델이 `computed`여도 일부 원소·미측정 영역의 미상값과
`issues`를 함께 확인한다.

입력 context의 관측 origin과 계산 결과를 구분하며, 원 receipt와
`record_digest`를 보존한다. `procedure_or_field_qualification`은 항상 거짓이다.
각 모델의 성공은 전체 규격 준수, 공법 승인, 예열 요구조건 또는 균열 확률의
검증을 뜻하지 않는다. 관련 실행 기록은
[용접 교환 결과](../research/welding_results.json)와
[EBW 열 이력 결과](../research/ebw_thermal_results.json)에서 확인한다.

원 ZIP·workbook·NetCDF·동영상·스캔·공급자 notebook·변환 샘플 행은 코드 배포에
포함하지 않는다. 카탈로그의 DOI·저자·버전·CC BY 4.0 권리와 공급자 체크섬을
보존하며, 여기에 설명한 선택·단위 변환·valid 표시·집계는 이 패키지의 처리다.
이 문서는 공급자의 문장·표·코드나 용접 규격의 계수·허용치 표를 복제하지 않는다.

두 자료는 서로 다른 공정·실험·재료다. 전기 파형과 열 이력을 같은 물리 용접의
관측처럼 합치지 않는다. 원 UTC 좌표의 변환은 하드웨어 시계 동기 검증이 아니다.
Tandem의 전체 원기록/명시창 signed진단은 research/tandem_welding_results.json을 따른다.
