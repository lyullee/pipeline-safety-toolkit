# 공개 유량계 시험 자료 연결

실제 시험 자료 두 종류를 원본 크기·공급자 체크섬·이용 조건과 함께 읽는다.
LNG·물의 교정 결과와 압축공기의 압력·온도 관측은 적용 범위가 다르다.
자료 취득, 단위 변환, 원자료 관계식 확인, 가스 유량계 모델 검증을 구분한다.
현재 자료만으로 독립적인 가스 유량 정확도나 현장 교정 성능을 판정하지 않는다.

## 출처와 취득 범위

| 자료 ID | 고정한 공급자 기록 | 선택 파일 | 원본 바이트 | 권리 |
|---|---|---|---:|---|
| `vsl_lng_water_calibration` | Zenodo 22274102, DOI 10.5281/zenodo.22274102 | 교정 workbook·공급자 시험 설명 | 364,146 | CC-BY-4.0 |
| `vortex_air_meter_experiment` | Mendeley Data V1, DOI 10.17632/8zktfd9k9n.1 | 압축공기 실험 관측표 | 183,183 | CC-BY-4.0 |

총 2자료군·3파일·547,329바이트다. 추가 대용량 파일은 요청하지 않았다.
[VSL 기록](https://zenodo.org/records/22274102)과
[해당 DOI의 권리 메타데이터](https://api.datacite.org/dois/10.5281/zenodo.22274102),
[압축공기 원기록](https://data.mendeley.com/datasets/8zktfd9k9n/1)과
[공급자 메타데이터](https://data.mendeley.com/public-api/datasets/8zktfd9k9n/snapshot/1)를 확인했다.
출처 표시는 수치·권리의 추적을 위한 것이며 공급자의 인증 주장을 이 패키지의
인증이나 현장 적용 승인으로 이전하지 않는다.

VSL 저자는 Ara Abdulrahman·Menne Schakel이다. API의 별도 버전 문자열은 없다.
고정 단위는 기록번호 22274102다. 공급자 설명의 내부 버전 1.0/2026-09-04와
기록 발행일 2026-09-03의 차이를 하나의 날짜로 합치지 않는다.
압축공기 자료 저자는 Eli Wilfredo Zavaleta Aguilar·Beethoven Narváez-Romo·
Jose R Simoes-Moreira다. 원본·권리 메타데이터와 상세 가공 기록은 공개 코드
배포에서 제외하는 `data/raw`, `artifacts` 및 저장소 밖의 출처 캐시에 둔다.

| 원본 | 공급자 체크섬 | 로컬 SHA-256 |
|---|---|---|
| VSL workbook, 116,295B | MD5 `a3c75510e4765c1d207b65a47d9ea795` | `e97c896dde7792bae026e47fb8384256a8eff035ecbf1f85719e60069577a276` |
| VSL `ReadMe.pdf`, 247,851B | MD5 `1c857ea8fa484f9b966bdbbf4afb45c9` | `e0ca856ec584e646e459f2f7e2f626cc8fbb76f6691d8e01de16addb22078135` |
| 압축공기 `Research data.pdf`, 183,183B | SHA-256 `8c73b8be87add5f4c8763579c7c0e7bfeb1ab0be0f81cdca0c57f371da28c67a` | 공급자 SHA-256과 동일 |

파일별 취득 시각·URL·크기·공급자 검증은
`artifacts/acquisition/vsl_lng_water_calibration.json`과
`artifacts/acquisition/vortex_air_meter_experiment.json`에 있다.
공개 [검토 결과](../research/meter_source_results.json)에는 집계·검사·해시만 싣는다.
원래 문장·관측 행·표·도면을 문서에 옮기거나 재배포하지 않는다.
CC-BY 자료를 별도로 재배포·변형할 경우 원저자·출처·권리·변경 사항을 보존해야 한다.

## VSL 자료의 의미

11개 시트에서 LNG 반복 시험 평균 274행, 설정점 요약 123행(LNG 84·물 39),
조성 및 조성 확장불확도 12행을 읽었다. 다섯 상세 시트의 행 수는 각각
73·50·64·60·27이다. 시트 `Emerson CMF100 LH2`는 식별 문자열로 보존한다.
시트 내부 시험 유체는 LNG이므로 해당 이름에서 수소 조건을 만들지 않는다.
123요약 중 오차 열이 있는 것은 104행이다. Trigas의 나머지 19행은 평균
펄스계수 요약이며 `error_percent=None`이다. 빈 오차를 0으로 바꾸지 않는다.

상세 행은 한 번의 반복 시험 동안 평균한 값이다. 원 계수기의 연속 펄스 이력이나
공정 SCADA를 확보한 것으로 해석하지 않는다. 날짜·시작·종료 문자열과 실제
계수기 측정 시간을 각각 보존한다. 224행에서 두 시간의 차이가 있으며 부호도
다르다. 그 차이를 보정하거나 한 시간을 다른 시간으로 대체하지 않는다.
날짜의 세기는 검토한 2026년 캠페인만 처리하고 시간대는 미상으로 남긴다.
다른 두 자리 연도나 자정을 넘는 모호한 시각에는 유효 시각을 부여하지 않는다.

압력은 bar(g), 온도는 섭씨다. 제공된 같은 시험일의 대기압에 `Pa`가 명시된
경우에만 게이지압에 더해 절대압력을 만든다. Trigas의 둘째 날 대기압 셀에는
단위가 없어 26행의 절대압력은 미상이다. 질량유량 kg/h는 kg/s로, 국소 액체
부피유량 m³/h는 m³/s로 바꾼다. 원문 셀·수식·공급자가 저장한 수식 계산값은
별도 필드에 보존하고 수식을 실행하지 않는다. 수식의 계산값이 없으면 미상이다.

공급자가 제공한 밀도는 일별 GC 조성과 시험 평균 압력·온도에 대한
REFPROP 10/GERG 기반 값이다. 같은 조성의 CoolProp 계산으로 바꾸거나 두
물성계산의 일치를 확인한 것으로 표시하지 않는다. 액체의 해당 위치 부피를
건조가스·습윤가스·표준상태 부피로 바꾸지 않는다. 마스터 계기의 원 펄스,
보정계수 및 전체 추적성 사슬을 새로 계산한 것도 아니다.

요약의 `Expanded U (%)`는 공급자가 명시한 k=2의 확장불확도다.
`scalar_standard_percent_from_U_over_k`는 U/2의 단일 스칼라 변환이며
여러 입력의 표준불확도·독립성·공분산을 제공하지 않는다. 약 95%라는 값은
공급자의 정규분포 조건부 설명을 `coverage_condition`과 함께 보존한다.
자유도·공동분포·반복 간 상관·성분 공분산은 미상이다. Trigas의 Type A 평균은
평균 펄스계수에 대한 상대값으로 유지한다. 다른 계기의 오차 Type A와 혼합하지 않는다.
CMF300의 원 오차와 공급자의 영점 후처리 오차도 별도 값이다.
조성의 cmol/mol을 분율로 변환해도 총합을 1로 정규화하지 않는다.
요약과 이를 구성한 반복 행은 독립 표본으로 중복 사용하지 않는다.

## 압축공기 자료의 의미와 가스 API

RH-1부터 RH-5까지 다섯 운전 조건에 inlet·hot·cold 세 분기가 있다.
관측표에 대기압·게이지압·오리피스 차압(kPa)·온도(섭씨)가 있다.
주어진 대기압을 사용해 절대압력으로 바꾸고 차압은 Pa, 온도는 K로 바꾼다.
실험 번호는 시간축이 아니다. 시간·측정 구간·상대적인 시험 간격을 만들지 않는다.

관경·오리피스 크기·압력 탭 위치·Cd·팽창계수·가스 몰분율·습도 기준·기준
부피의 압력과 온도·독립 유량 참값·측정 불확도는 선택 파일에서 확인되지 않는다.
표의 압력 관측 위치와 오리피스 차압의 두 끝점을 완전히 대응시킬 수 없어
하류압력도 `상류압력 - 차압`으로 채우지 않는다. 명칭이 공기라는 이유로
건조 Air의 조성이나 물성을 지정하지 않는다.

`vortex_gas_meter_cases`는 실제 [`gas_meter`](GAS_METER_KO.md) 요청 15개를 만든다.
`time_s`, `geometry`, `components`, `support`, `volume_basis`, `reference`,
`downstream_pressure_pa_abs`는 `None`, 적용성 검토는 빈 사전이다.
배관 자산의 실제 포트 번호를 얻은 것이 아니므로 `source-diagram:*` 식별자는
도면의 분기 방향을 표현하는 분석용 노드로만 사용한다. 그 의미와 실제 포트
대응 미상은 `RunContext.metadata.meter_observation_binding`에 남는다.
원 시험 ID·분기·파일 SHA-256·CC-BY-4.0 근거를 연결한다.
실제 15요청 모두 `needs_data`이며 CoolProp·Fluids 계산은 0회다.
따라서 이 자료를 이용한 가스 유량 모델 독립 검증 사례도 0개다.

## Python API와 재현

`pipeline_integrity.meter_data`는 다음 함수를 제공한다.

| 함수 | 입력 및 결과 |
|---|---|
| `parse_vsl_calibration(payload)` | 검토한 XLSX 스키마를 읽어 셀·캐시·반복·요약·조성을 반환; 출처 확인 전 파싱 |
| `parse_vortex_air_pdf(payload)` | 1MB 이내 단일 페이지 자료의 실제 관측표 추출; 출처 확인 전 파싱 |
| `read_vsl_calibration(folder, source_spec=None)` | workbook과 공급자 방법 설명을 모두 크기·체크섬·권리·실험 기원으로 확인 |
| `read_vortex_air_meter(path, source_spec=None)` | 원 PDF의 공급자 체크섬 확인 후 실제 읽기 |
| `verify_meter_source_record(record, source_root=..., source_spec=None)` | 원본 재검증·재파싱과 전체 기록 비교; `True` 또는 예외 |
| `vortex_gas_meter_cases(record, source_root=..., source_spec=None)` | 원본 재검증 후 `(GasMeterRequest, RunContext)` 15쌍 |

`source_root`는 자료 ID 하위 폴더들을 가진 공통 `data/raw` 경로다.
`source_spec` 생략 시 공개 카탈로그를 사용한다. 다른 명세를 전달한다면 같은
ID의 검토한 실험 자료·CC-BY-4.0·공급자 크기·체크섬을 요구한다. 읽기 함수는
원본을 수정하지 않는다. 검증은 공급자 파일이 없으면 성립하지 않는다.
기록 해시를 다시 계산했더라도 기원·시각·단위·U/k·가스 기준·관측값을 바꾸면
원본과의 전체 비교에서 거부한다. 이 무서명 기록은 과거 실행의 서명 증명이나
자료의 참값·공급자 인증·공학적 승인을 증명하지 않는다.

원자료 셀과 공급자 설명을 읽는 선택 의존성은 함수 호출 시 지연 로드한다.
시험 버전은 openpyxl 3.1.5·pypdf 6.10.0이다. 기존 토양 자료의 범용
workbook 예산·체크섬 읽기를 재사용하며 유량계 전용 단위·기원·의미를 덧붙인다.
핵심 import만으로 openpyxl·pypdf·Fluids·CoolProp을 로드하지 않는다.

```powershell
python -m pip install -c requirements-tested.txt ".[data,dev]"
python scripts/acquire_data.py --only vsl_lng_water_calibration vortex_air_meter_experiment --workers 2 --maximum-seconds 90
python scripts/review_meter_source.py
python -m pytest tests/test_meter_data.py -q
python -m ruff check src/pipeline_integrity/meter_data.py tests/test_meter_data.py scripts/review_meter_source.py
```

```python
from pathlib import Path
from pipeline_integrity.meter_data import (
    VSL_ID,
    VORTEX_ID,
    read_vsl_calibration,
    read_vortex_air_meter,
    verify_meter_source_record,
    vortex_gas_meter_cases,
)
from pipeline_integrity.gas_meter import evaluate_gas_meter

raw = globals().get("METER_RAW_ROOT", Path("data/raw"))
specs = globals().get("METER_SOURCE_SPECS", {})
liquid = read_vsl_calibration(raw / VSL_ID, source_spec=specs.get(VSL_ID))
assert verify_meter_source_record(liquid, source_root=raw, source_spec=specs.get(VSL_ID))
u = liquid["summaries"][0]["uncertainty"]
assert u["scalar_standard_percent_from_U_over_k"] == u["expanded_percent"] / u["coverage_factor"]
assert u["component_covariance"] is None
air = read_vortex_air_meter(raw / VORTEX_ID / "Research data.pdf", source_spec=specs.get(VORTEX_ID))
pairs = vortex_gas_meter_cases(air, source_root=raw, source_spec=specs.get(VORTEX_ID))
assert len(pairs) == 15
for request, context in pairs:
    result = evaluate_gas_meter(request, context)
    assert result["status"] == "needs_data" and not result["native_called"]
```

## 검증 경계와 연구 인수인계

작성한 단위·계약 검사 37개와 실제 자료 검토를 구분한다. 실제 검토의 56개
혼합 기준에는 원본 확인, 출처 의미, 15요청 보류·재생, 수치 관계식 집계가 있다.
실제 원본에서 만든 기록을 변조한 뒤 해시를 다시 계산하는 11경우도 거부한다.
요약·반복의 독립성이나 현장 물리 성능을 검증한 횟수로 이 숫자를 사용하지 않는다.

224반복의 표시 유량과 기준 유량으로 오차를 다시 계산하고 공급자가 반올림한
오차 열과 비교한다. 허용치는 0.000501 퍼센트포인트이며 불확도나 정확도
합격선이 아니다. 질량 펄스 관계 160건의 허용치는 5.01×10⁻⁶kg/s,
국소 부피 펄스 관계 64건의 수식 캐시 일치는 10⁻¹⁵m³/s다.
터빈 50건의 주파수/기준부피유량 펄스계수 관계는 2×10⁻¹³ pulse/L 범위에서
확인한다. 같은 공급자 열·수식으로 구성된 관계이므로 독립 유량계 물리 기준이 아니다.
실제 최대 잔차·각 수·런타임·제작 파일 해시는 검토 결과 JSON에서 확인한다.

다음 연구는 `summaries[*].uncertainty`의 U/k 단일 스칼라 전달과
`runs[*].observations_si`의 기준/표시 질량유량을 [계측 불확도 서비스](METROLOGY_KO.md)에
연결할 수 있다. 성분별 표준 공분산이 없으면 두 입력 유량의 불확도 전파는
`needs_data`로 둔다. 요약 U를 임의로 두 유량에 나누거나 상관 0을 채우지 않는다.
실제 시각이 없는 요약을 시간 신호로 출력하지 않는다. 별도의 분석 시계가
필요하면 원 관측 시각과 구분해 호출자가 명시한다.
정적 불확도 요청은 `time_s=None`으로 원 시각 미상을 유지할 수 있다.
오차 104요약은 `error_percent/100`과 `expanded_percent/100/k`를 단일 입력의
항등 감도 J=1에 전달해 원 확장불확도 보존을 확인할 수 있다. 이 작업은 원
값을 다른 표현으로 전달한 것이며 새 교정이나 모델 검증이 아니다.
Trigas 19요약은 `source_values['Average pulse factor (P/L)']`와 해당 상대
불확도의 의미를 별도 검토해야 하며 같은 오차 항등 예제로 처리하지 않는다.

가스 모델 독립 검증을 늘리려면 형상·압력 탭·동일 조성/수분 기준·상태·독립
유량 참값·불확도·실험 간 의존성이 함께 공개된 추가 자료가 필요하다.
VSL의 밀도 재현에는 별도의 REFPROP/GERG 사용 조건과 입력 근거 검토가 필요하다.
현재 자료를 수소 기체나 표준 부피 계량으로 외삽하지 않는다.

pypdf는 실제 자료를 추출할 때 사용하는 BSD-3-Clause 선택 의존성이다.
[공식 배포](https://pypi.org/project/pypdf/6.10.0/)의 wheel 334,459B와
공급자 SHA-256 `90005e959e1596c6e6c84c8b0ad383285b3e17011751cedd17f2ce8fcdfc86de`를
확인하고 6.10.0만 `--no-deps`로 추가했다. 기존 패키지를 교체하지 않았다.
설치된 LICENSE 해시는 `a97ac230e5f33ef10a5367a850eb01f91f1a0b064e34742c7794d2294557f524`다.
자료의 CC-BY 조건과 코드 의존성의 BSD 조건은 각각 보존한다.
