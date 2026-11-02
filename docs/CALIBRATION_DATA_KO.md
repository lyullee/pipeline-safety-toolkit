# 상관된 교정 자료의 출처와 연결

`calibration_data`는 PTB·LNE가 공개한 노즐 교정 예제의 원본을 확인하고,
측정에서 얻은 기본량과 공급자가 불확도를 전파한 중간량을 구분해 읽는다.
30점의 x·y와 전체 60×60 표준 공분산을 함께 전달한다. 공분산의 비대각 항을
버리거나 점들을 서로 독립인 시험으로 바꾸지 않는다.

이 자료의 실제 읽기·출처 검증·행렬 구조 확인과 새 교정 모듈의 적합 결과,
독립 실험 검증, 현장 적용 승인은 각각 다른 단계다. 선택한 파일에는 별도
검증 캠페인이 없어 독립 검증 자료를 만든 것으로 표시하지 않는다.

## 원본·이용 조건·취득

원기록은 [EMUE-D4-3-QuantifyUncertaintiesInCalibration](https://zenodo.org/records/4016916),
DOI `10.5281/zenodo.4016916`, 버전1.0이다. 저자는 Steffen Martens·Katy Klauenberg·
Bodo Mickan·Catherine Yardin·Nicolas Fischer·Clemens Elster다.
[등록 권리](https://api.datacite.org/dois/10.5281/zenodo.4016916)와 공급자 API에서
CC-BY-4.0을 확인했다. 저자·출처·권리·변경 사항은 원자료의 재사용 시 보존한다.
보고서의 규격 언급은 이 패키지의 규격 적합성 선언으로 가져오지 않는다.

선택 파일은 `EMUE-D4-3-SonicNozzles.zip` 하나, **1,406,894바이트**다.
공급자 MD5는 `ffc39a245e2e4361a3337be63a0b21db`, 취득 SHA-256은
`351c8f662491ae18394f1ac181f0c7ccdb89a7719faae94cdeb6f8a867d9356c`다.
크기와 권리를 확인한 뒤 제한시간을 두고 직접 내려받아 공급자 체크섬을 검사했다.
대용량 다운로드 도움이나 추가 패키지 설치는 필요하지 않았다.

원 ZIP은 `data/raw/emue_sonic_nozzle_calibration`에 둔다. 파일별 URL·취득시각·
공급자 검증 원장은 `artifacts/acquisition/emue_sonic_nozzle_calibration.json`이다.
출처 메타데이터는 저장소 밖 캐시에 보존했다. 원 ZIP·선택한 수치 행·전체 행렬·
상세 기록은 코드 배포에 포함하지 않는다. 공개 [검토 결과](../research/calibration_source_results.json)에는
수·집계 진단·해시만 싣는다. 공급자 문장·표·도면·프로그램을 공개 문서나 구현에
복사하지 않는다. ZIP의 R Markdown·TeX·HTML을 실행하거나 다른 모듈로 복제하지 않는다.

## 세 종류의 자료 의미

| 자료 | 의미 | 처리 |
|---|---|---|
| `data_A223.csv` 30행 | 측정에서 얻은 Q·임계 질량유속과 Type A 표준불확도 성분 | 원 문자열·출처 행·숫자·단위를 보존 |
| `MC_data_A223.xlsx`의 Data | 공급자가 몬테카를로 전파로 얻은 중간량 x·y 추정값 | 원 점 순서 그대로 전달 |
| 같은 workbook의 Var_x·Var_y·Var_xy | 중간량의 점 사이·축 사이 표준 공분산 | 전체 60×60 행렬로 묶어 전달 |

CSV의 `mean.Q`, `SU.Q`는 kg/s, `mean.Psi`, `SU.Psi`는 kg/(m²·s)다.
SU는 반복 측정에 대한 Type A 표준불확도 성분이며 전체 불확도·확장불확도가
아니다. 0인 성분을 미상으로 바꾸지 않는다. 빈 값·NaN·Inf·해석할 수 없는
문자열은 원 토큰을 남기고 숫자를 미상으로 둔다. 음의 표준불확도나 양이 아닌
기본 유량은 유효하지 않은 영역으로 기록한다. 자유도·확장계수는 제공된
CSV에서 임의로 만들지 않는다.

중간량 x는 Reynolds 수의 역제곱근, y는 유출계수이며 둘 다 무차원이다.
이 추정값과 공분산은 실험 입력에 대한 **공급자의 몬테카를로 요약 결과**다.
실제 기준 센서와 시험 계기를 직접 읽은 서로 독립인 두 열로 표시하지 않는다.
공급자가 언급한 10⁸회 계산의 원 표본은 취득하지 않았고 그 계산을 다시 수행하지도 않았다.
기본량의 Type A 성분만으로 공급자 전체 공분산을 대체하거나 Type B 성분을
추정해 채우지 않는다.

행렬 순서는 `(x1,…,x30,y1,…,y30)`이다. Var_xy의 i행 j열은 Cov(X_i,Y_j)이며
아래 왼쪽 블록에는 그 전치를 둔다. x가 단조 순서가 아니어도 정렬하지 않는다.
`publisher-point-1` 등의 ID는 원 행 순서에 붙인 분석 식별자이며 센서의 일련번호나
서로 다른 실험 캠페인 ID가 아니다. 선택된 30점은 공통 입력의 영향을 받는
하나의 자료군이다. 점을 임의로 나눠 독립된 학습·검증 런으로 부르지 않는다.

자료에 없는 관측 시간·완전한 가스 조성·상류 절대압력·온도는 `None`으로 둔다.
또한 공급자 방법 설명의 점성계수 단위에는 해석이 필요한 불일치가 있어 그 상수를
사용한 물성·임계 유량 재계산은 수행하지 않는다. 공급자 중간량과 공분산을
읽었다는 사실이 열역학 모델이나 초크 유동을 검증했다는 뜻은 아니다.
기존 [가스 유량계](GAS_METER_KO.md)의 중간 차압·비초크 범위를 이 노즐 자료에
자동 적용하지 않는다.

## 실제 workbook 범위 확인

네 시트 모두 파일의 선언 범위가 A1:A1이다. 실제 XML 셀은 Data가 A1:B31,
나머지 세 시트가 A1:AD31이다. 선언 범위만 따르는 읽기는 실제 값을 잃는다.
이 소스에 한해 지연 로드한 openpyxl의 읽기 범위를 다시 조사하고 두 범위를
모두 기록한다. 원본 셀을 보충하거나 파일을 저장해 수정하지 않는다.

기존 자료 모듈의 압축파일 경로·중복·해제 크기 검사와 체크섬 검사를 재사용한다.
추가로 원 ZIP 2MB, 구성원40개, 비압축 합8MB 이하를 요구한다. 실제 workbook의
시트 이름·순서·31행·열수·헤더를 확인하고 수식이 있으면 거부한다. 파일을
디스크에 일괄 해제하지 않는다. 공급자 workbook의 기본 스타일 누락 경고는
숨겨 성공으로 바꾸지 않고 상세 및 공개 집계에 기록한다.

## Python API

| 호출 | 계약 |
|---|---|
| `parse_emue_archive(payload)` | 검토한 ZIP 구조 파싱; 출처 확인 전 자료 기록 |
| `read_emue_calibration(path, source_spec=None)` | 원 ZIP의 크기·체크섬·CC-BY-4.0·혼합 기원 검증 후 파싱 |
| `verify_calibration_source_record(record, source_root=..., source_spec=None)` | 원 ZIP 재읽기·전체 투영 비교; 성공 True 또는 예외 |
| `emue_calibration_inputs(record, source_root=..., source_spec=None)` | 원본 재검증 후 x·y·전체 표준 공분산과 의미·출처 반환 |

`source_root`는 자료 ID 폴더들을 가진 공통 `data/raw`다. `source_spec`를 생략하면
등록 카탈로그를 읽는다. 명시한 다른 명세도 같은 자료 ID·권리·공급자 체크섬·
`mixed_compilation` 기원이 필요하다. 기본량은 실험에 기초하지만 파일 전체에는
공급자의 모의 전파 요약도 있어 카탈로그 기원을 순수 현장 측정으로 바꾸지 않는다.

반환한 `x`, `y`, `point_ids`, `covariance_order`, `standard_joint_covariance`는
점 순서를 보존한 tuple이다. 축 단위는 `x_unit=y_unit='1'`, 불확도 의미는
`source_uncertainty_scale='standard_covariance_not_expanded'`다.
`source_asset_sha256`, `source_member_sha256`, `source_record_digest`는 ZIP과
해당 workbook·현재 읽기 기록을 묶는다. `source_origin`, `split_unit`,
`independent_validation=None`, `observation_time_s=None`도 함께 전달한다.
`source_citation`, `source_url`, `source_license_id`도 보존한다.
연동 시 이 필드들을 선택한 EvidenceRef와 RunContext에 연결한다.

구조 검사·양의 대각·대칭만으로 양의 정부호를 일반적으로 보장하지 않는다.
실제 적용 스크립트에서 해당 원 행렬의 상관 스케일 고유값과 수치 계수를
확인했다. 교정 서비스도 전달된 전체 행렬의 수치 지원 범위를 별도로 확인해야 한다.
수치적으로 문제가 있는 행렬에 jitter·근접 행렬·대각화를 자동 적용하지 않는다.

```powershell
python -m pip install -c requirements-tested.txt ".[data,engineering,dev]"
python scripts/acquire_data.py --only emue_sonic_nozzle_calibration --maximum-seconds 90
python scripts/review_calibration_source.py
python scripts/review_calibration_source.py --fit-source
python -m pytest tests/test_calibration_data.py -q
python -m ruff check src/pipeline_integrity/calibration_data.py tests/test_calibration_data.py scripts/review_calibration_source.py
```

```python
from pathlib import Path
from pipeline_integrity.calibration_data import (
    EMUE_ID,
    ARCHIVE_NAME,
    read_emue_calibration,
    verify_calibration_source_record,
    emue_calibration_inputs,
)

raw = globals().get("CALIBRATION_RAW_ROOT", Path("data/raw"))
spec = globals().get("CALIBRATION_SOURCE_SPEC")
record = read_emue_calibration(raw / EMUE_ID / ARCHIVE_NAME, source_spec=spec)
assert verify_calibration_source_record(record, source_root=raw, source_spec=spec)
inputs = emue_calibration_inputs(record, source_root=raw, source_spec=spec)
assert len(inputs["x"]) == len(inputs["y"]) == 30
assert len(inputs["standard_joint_covariance"]) == 60
assert inputs["independent_validation"] is None
assert inputs["observation_time_s"] is None
assert inputs["source_uncertainty_scale"] == "standard_covariance_not_expanded"
```

## 검증과 이어갈 연구

현재 작성한 단위·계약 테스트35개, 실제 자료의 혼합 출처·구조 기준26개,
실제 원본 기록의 재해시 변조 거부12건과 문서 Python 예제1개를 확인했다.
각 수를 독립 물리 검증 횟수로 합산하지 않는다. 기본 자료 검토는 native 적합을
호출하지 않는다. `--fit-source`를 명시하면 검토한 연구 적합을 별도로 실행한다.

작성한 가상 원본으로 실제 XML 범위·단위와 축·공분산 전치·음수 및 미상·0인
Type A 성분·ZIP 안전성·출처 손상·권리·선택 의존성 지연 로드·기록 변조를 검사한다.
실제 원본 기록에서도 기원·확장/표준 의미·시각·점 순서·독립 검증·비대각 항·
방법 출처를 바꾸고 해시를 다시 계산한 12경우를 거부한다. 원본 재확인 없이
해시가 맞는다는 이유로 저장 기록을 승인하지 않는다.

실제 행렬은 대칭이며, 비대각3,540개가 모두 0이 아니다. 상관 스케일의 고유값
범위는 약0.00197764–29.99483, 수치 계수는 약15,166.98, 수치 랭크는60이다.
이 값은 이 파일의 수치 진단이며 현장 계측 성능이나 불확도 모델의 참값을 검증한
것이 아니다. 실행 기준·현재 제작 파일 해시·런타임은 공개 검토 JSON을 따른다.

전체 공분산을 지원하는 교정 공급자에 이 입력을 전달하면 출판된 WTLS의 반올림
수치 결과와 비교할 수 있다. 출판 수치 재현과 독립 실험 검증은 구분한다.
대각화한 별도 비교는 상관을 무시한 다른 가정의 예제로 명시해야 하며 기본
계산을 대체하지 않는다. 독립 검증을 요구하는 기본 정책에는 추가 캠페인이
필요하다. 적합만 수행하도록 명시한 연구 요청도 독립 검증 상태는 미제공으로 둔다.

선택 실행은 기존 `calibration` 서비스와 `examples/calibration.py`의 공통 요청
구성을 재사용한다. 전체 원 공분산을 사용한 연구 적합1건, 미검토 보류1건,
별도 검증 자료를 요구해 거부한1건을 실제 호출했다. 계산된 기록도 native로1회
재생했다. 출판된 반올림 offset·slope·두 표준불확도·공분산의5수치와 출처·정책·
기록의6기준, 총11기준을 별도로 확인한다. 원 공분산을 잔차로 재조정하지 않는다.
기본량의 Type A만 대입하거나 비대각을0으로 채운 계산도 아니다.
요청의 연구용 탐색 경계는 현장 설비의 지원 영역을 입증하지 않는다.
현재 출력의 독립 검증은 미제공이며 미래 예측의 정확도나 합격을 주장하지 않는다.
적합·위임 예제·native backend의 해시와 실제 상태는 공개 검토 결과의
`native_source_application`에서 확인한다. 세부30점·잠재 x·잔차·전체 기록은
`artifacts/calibration_data/actual_source_native_calibration_records.json`에만 저장한다.

NIST의 수치 회귀 기준은 별도의 후보로 조사했으나 선택 자료로 취득하지 않았다.
이번 읽기·전체 공분산 연결에는 기존 openpyxl과 NumPy를 사용하며 의존성
버전을 바꾸지 않았다. 공급자 보고서의 의미 확인에는 기존 pypdf를 사용했다.
