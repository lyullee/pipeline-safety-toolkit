# 토양 열·수분 관측의 확보와 원조건 판독

실제 관측을 포함한 공개 자료 두 묶음에서 선택한 7개 파일, 7,822,891바이트를
확보했다. 공급자 MD5를 모두 확인하고 로컬 SHA-256도 기록했다. 관측 판독과
배관 모델 적용성은 별도다. 현재 자료의 배관 과도 열 검증·토양 물성 보정은
`needs_data`이며, 열전도도·열용량·매설관 형상이나 하중을 만들지 않는다.

## 출처와 선택 범위

| 자료 | 원 저자·판·권리 | 이번 선택 |
|---|---|---|
| [토양 표면 피복별 수열 거동](https://doi.org/10.5281/zenodo.16415302) | Bing Wu, Hong-Hu Zhu, 2025-07-24, 첫 공개 record 16415302; API의 version 문자열은 미입력. CC BY 4.0 | Climate data of experiment.xlsx, Soil temperature.xlsx, Water content.xlsx, Suction.xlsx, 합계 5,764,077바이트 |
| [가열 광섬유 시추공 시험](https://doi.org/10.5281/zenodo.6480800) | Bo Zhang, Kai Gu, Peter Bayer, Haibo Qi, Bin Shi, Baojun Wang, Yuehua Jiang, Quanping Zhou, 2022, version 1.0. CC BY 4.0 | estimation_data.mat, data_process.mlx, estimation_process.mlx, 합계 2,058,814바이트 |

두 자료 모두 원 Zenodo API의 파일 크기·MD5·라이선스와 DataCite 권리 등록을
취득 전에 확인했다. [피복 자료의 권리 등록](https://api.datacite.org/dois/10.5281/zenodo.16415302)과
[시추공 자료의 권리 등록](https://api.datacite.org/dois/10.5281/zenodo.6480800)은
CC BY 4.0을 명시한다. 사용 시 원 저자·판·DOI와 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)을
유지하고 자신의 변환을 표시한다. 공급자 문장·코드·관측 행을 이 코드 배포에 넣지 않는다.
라이선스 확인은 모든 후속 결합 배포의 포괄적 법률 보증이 아니다.

피복 자료의 두 Modeling workbook은 수치 모형 출력이므로 선택하지 않았다.
시추공 원 전체 `insitu_measurements.mat`은 공급자 크기 329,832,492바이트와 MD5
`02e88e184c13af98b165d4bd0f7d6b60`을 확인했지만 미취득·기본 선택 false다.
이번 작은 추정 입력을 읽는 데 필요하지 않아 사용자 다운로드를 요청하지 않았다.
`numerical_data.mat`도 미취득이며 실제 온도 관측과 혼합하지 않는다.

USAP-DC의 [별도 active-layer 자료](https://doi.org/10.15784/602028)는
권리·파일 정보만 검토했고 다운로드 reCAPTCHA를 우회하지 않았다.
소용량 자료는 위 unrestricted 경로에서 직접 확보했다. 처음의 API 경로 45초
시도는 세 파일의 partial을 남겼다. 종료를 확인한 뒤 같은 공급자의 record 경로와
체크섬·크기가 고정된 이어받기로 다시 취득했다. 각 파일 8MB, 두 번째 실행은
120초 한도를 사용했고 모든 최종 원장이 성공했다. 이전 timeout도 원장에 남겼다.

## 피복 실험 파일에서 실제 확인한 의미

원 실험은 야외 토양 탱크의 피복 조건을 비교한다. source의 B/T/S는 불투명
식별자로 보존하며, 선택 파일만으로 각 코드를 특정 피복으로 매핑하지 않는다.
같은 탱크의 인접 시각·깊이를 독립 시편으로 세지 않는다.
피복 원 관측의 origin은 laboratory, 시추공 선택 관측은 measured로 기록한다.
시추공 전체 묶음의 mixed compilation 구분과 선택한 처리 관측의 origin은 별개다.

| 파일 | 실제 판독 | 해결하지 않은 조건 |
|---|---|---|
| Soil temperature.xlsx | B 274일, T 167일, S 274일의 daily maximum/minimum/range. L1~L5와 원 열 위치 유지 | 수치 깊이·명시 온도 단위·timezone·피복 코드 대응. 일 극값을 순간 경계로 복원하지 않음 |
| Water content.xlsx | 24,048개 달력 시각, 10분 간격, 6채널. 유한 수치 142,620개, 미상/비수치 1,668개 | 원 header에 분율/백분율 단위 없음. 수치가 작다는 이유로 m³/m³ 또는 %로 지정하지 않음 |
| Suction.xlsx | 23,904개 달력 시각, 10분 간격, 6채널. 유한 수치 140,904개, 미상/비수치 2,520개. 음수 부호 유지 | 압력 단위와 센서/검교정. kPa·Pa·흡입 수두로 자동 바꾸지 않음 |
| Climate data of experiment.xlsx | 일 기온 257행, 원 rainfall 시계 3,984행과 mm header, formula/cache 분리 | 대기 기온은 토양 지표 온도가 아님. 두 기온 시각 열과 불규칙 원 rainfall 간격을 임의 정합하지 않음 |

수분·suction header의 5cm·15cm는 0.05m·0.15m로 명시 변환한다.
이 깊이를 별도 L1~L5 온도 센서나 관 외측 반경으로 옮기지 않는다.
온도 T sheet에서는 header대로 비교할 수 있는 564개 max/min/range 조합 중
557개가 불일치했고 최대 절대 차이는 약 4.01이다. 나머지 271개 조합도 값이
빠져 비교하지 못한다. 열 위치 이동이 의심되더라도 원 셀을 옮기지 않는다.
B/S의 비교 가능한 각 957조합은 이 산술 검사를 통과했지만 온도 단위나
센서 깊이·정확도가 확인된 것은 아니다.

달력 셀은 원 Excel의 naive datetime을 ISO 문자열과 원 타입으로 보관한다.
timezone은 None이며 UTC나 특정 지역 시각을 붙이지 않는다. 원 전체 행·열,
빈 셀·형식상 남은 빈 행·문자·음수·수식은 상세 기록에 보존한다.
수식의 원 문자열과 제공 cache는 다르고, 수식을 실행하거나 재계산하지 않는다.
관측 진단에 사용하지 못한 비어 있지 않은 행도 원 row 번호로 남긴다.

## 시추공의 실제 과도 온도 기록

공급자의 작은 추정 입력은 MATLAB v7.3/HDF5다. SciPy의 일반 MAT reader로는
읽히지 않아 이미 설치된 optional h5py를 사용했다. 실제 변수는 depth,
temperature, time_seconds 세 개이며 HDF5 원 배열은 temperature[1054,315],
depth[1,315], time_seconds[1054,1]이다. reader는 시간×깊이 방향을 유지하고
MATLAB 배열 표기와 HDF5 저장 방향을 혼동해 transpose하지 않는다.

온도는 332,010개가 모두 유한하고 약 17.392~42.0157°C다. 단위 s·m·°C는
체크섬으로 고정한 원 방법 파일의 축 표기에서 확인했다. 그 파일은 읽기 근거로
보존하며 MATLAB 코드·최적화·보정·추정은 실행하지 않는다. 수치 크기로 단위를
추정하지 않았고 원 방법의 열물성 계수도 배관 모수로 채택하지 않았다.

원 processing time은 **5~129,481초**다. 첫 점을 0으로 이동하지 않는다.
인접 간격은 121초 27개, 122초 1,013개, 123초 11개, 427초 1개, 843초 1개다.
공급자 설명의 약 120초와 실제 간격을 구분하고 긴 공백도 보간하지 않는다.
depth는 315점, 0~79.818m의 시추공 수직 좌표다. 이 값을 배관 토양의 radial
radius로 쓰지 않는다. 깊이 0도 지표/관벽 온도 경계라고 자동 해석하지 않는다.

공급 방법은 이상값 보정과 시간 thinning을 설명한다. 선택 MAT의 각 값에 대한
전체 처리 계보와 원 전체 trace는 확보하지 못했으므로 정확한 개별 처리 이력은
미상이다. 방법에 언급된 달력 가열 시각을 이 time_seconds에 임의 결합하지 않는다.
calendar origin/timezone, 가열 전력, 관/그라우트 형상, 유효 radial 위치,
열전도도·체적 열용량·지하수 대류는 unresolved다. 수직 온도점의 재생 성공은
배관 열모델의 물리 검증·독립 예측·토양 보정이 아니다.

## Python API와 실행

`read_soil_cover_workbook(path, *, source_spec=None)`는 선택 workbook 하나를,
`read_borehole_temperature(folder, *, source_spec=None)`는 MAT와 두 method 파일을
읽는다. 기본은 데이터 catalog의 검토한 DatasetSpec을 사용한다. 명시 spec을
주어도 권리·공급자 크기/체크섬·명시 파일명과 source layout을 검사한다.
다른 판·구조·단위는 새 검토가 필요하다. 저수준 `parse_soil_cover_workbook`와
`parse_borehole_temperature`는 원 bytes를 받지만 자체적으로 공급자 취득을
증명하지 않으므로 저수준 parse 기록만으로 신호를 내보내지 않는다.

`verify_soil_source_record(record)`는 digest, 원 배열/셀에서 재계산한 진단,
단위·좌표·미상/처리 상태·source identity를 확인한다. 해시를 다시 계산해도
온도 단위를 K로 바꾸거나 radial geometry·검증 완료로 승격한 기록은 거부한다.
이 검사는 서명·실제 측정 진위·공학 승인에 대한 독립 증명이 아니다.

`borehole_temperature_frames(record, context, *, depth_index, evidence_id,
clock_offset_s=0.0)`는 같은 raw asset SHA-256/권리의 EvidenceRef와
RunContext를 요구한다. context의 `soil_observation_binding`에는 asset_id,
clock_id, record_digest, source_clock_kind, coordinate_kind, 원 depth_index/depth_m,
명시 clock_offset_s와 quantity_meaning을 정확히 결합한다.
quantity_meaning은 `publisher_processed_point_temperature_not_pipe_or_radial_boundary`다.
측정 origin, 원 간격·공백·처리 시계와 None/valid를 유지하고 °C에 273.15를 더해
K로 내보낸다. UTC는 만들지 않는다. offset은 양수/0의 시계 대응이며 원 점을
지워 재영점화하는 기능이 아니다. 아래 단순 판독은 신호나 물리 모델을 호출하지 않는다.

```python
from pathlib import Path
from pipeline_integrity.soil_data import BOREHOLE_ID, read_borehole_temperature, verify_soil_source_record

raw_root = Path(globals().get("SOIL_RAW_ROOT", "data/raw"))
specs = globals().get("SOIL_SOURCE_SPECS", {})
record = read_borehole_temperature(raw_root / BOREHOLE_ID, source_spec=specs.get(BOREHOLE_ID))
assert verify_soil_source_record(record)
assert record["observations"]["temperatures"] == 332010
assert record["source_arrays"]["time_seconds"][0] == 5.0
assert record["applicability"]["radial_soil_pipe_model"] == "needs_data"
```

```powershell
python scripts/acquire_data.py --only soil_hydrothermal_covers soil_borehole_heating --workers 3 --maximum-seconds 120
python scripts/review_soil_source.py
python -m pytest -q tests/test_soil_data.py
python -m ruff check src/pipeline_integrity/soil_data.py tests/test_soil_data.py scripts/review_soil_source.py
python -m ruff format --check src/pipeline_integrity/soil_data.py tests/test_soil_data.py scripts/review_soil_source.py
```

reader import는 openpyxl·h5py·NumPy를 요구하지 않는다. 실제 workbook/HDF5 읽기 때만
해당 별도 라이브러리를 쓴다. source bytes 8MB, workbook 압축 해제 64MB와 1백만
cell·20sheet, HDF5 배열 1백만 원소 한도를 검사한다. 외부 HDF5 link·가상 dataset·
미지원 dtype/class·배열 방향을 거부한다. 비유한 원 값은 원 문자열과 invalid로
보존하고 null로 조용히 수리하지 않는다.

원본은 data/raw, metadata/권리 snapshot은 배포 경계 밖, 취득 원장은
artifacts/acquisition, 원 셀/배열·선택 신호·상세 receipt는 artifacts/soil_data에 둔다.
[공개 실행 집계](../research/soil_source_results.json)는 인용·권리·해시·집계·제한만
포함한다. 실제 적용 script는 원 파일 전후 해시, source 계약과 의미 오염 거부,
이 문서 Python 예제도 확인한다. 집중 pytest의 독립 작성 fixture는 실제 관측을
배포하지 않고 단위/시계/서명 없는 무결성·미상 보존을 확인한다.

후속 적용에는 동일 위치/물리 실험의 원 시간·센서 좌표·형상·물성·가열/표면
경계와 처리 계보가 필요하다. 관측을 radial far-field 경계에 연결하려면 별도
좌표/시계 mapping과 검토 근거를 제공하고, calibration과 독립 validation run을
분리해야 한다. 현재 source 확보량을 기술 완료율이나 현장 검증 개수로 환산하지 않는다.
