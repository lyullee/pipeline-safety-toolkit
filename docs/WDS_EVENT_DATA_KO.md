# WDSEventDB 원본 데이터 연결

## 자료와 재사용 조건

WDSEventDB는 실제 계측을 수행한 실험실 물 네트워크에서 정상 운전, 누수, 센서 고장 및 사이버 이벤트를 모은 자료다. 원 자료는 [Zenodo record 17547955](https://zenodo.org/records/17547955)에서 소개하며, 파일은 게시자의 [WDSEventDB GitHub 저장소](https://github.com/Moazeni-s-Concise-Lab/WDSEventDB) main commit `b9a3b54fbfce153e0728ec7c7e9f1e18414a0c19`에서 취득했다. 해당 repository의 LICENSE는 CC0-1.0으로 확인했다. Zenodo record의 웹 Rights 항목에는 license가 표시되지 않으므로 이 reader와 인벤토리는 GitHub 저장소의 명시적 CC0 표시를 근거로 한다. GitHub README가 요청하는 자료 인용도 유지한다.

아카이브의 크기는 2,265,766 bytes, MD5는 `4ac57517151a5958ebc2226954254f8d`, SHA-256은 `d1452c4a311f2c3132ddda1b78caab4c782f5c658553c5e32266cee230db459c`다. Zenodo가 표시한 MD5와 GitHub API의 파일 크기 및 blob SHA를 대조했고, 내려받은 GitHub 파일의 MD5가 Zenodo 값과 일치했다. 자료 원본은 `data/raw/wds_event_db/`에서만 보관하며 코드 번들에 포함하지 않는다. 취득 정보는 `research/data_inventory.json`에서 확인한다.

## 관측 형식과 실제 판독

고정 reader `pipeline_integrity.wds_event_data.read_wds_event_archive(path)`는 크기·MD5·SHA-256을 확인한 뒤에만 아카이브를 연다. 중복·위험 경로, 링크/특수 파일, 암호화 멤버, CRC 오류, 허용되지 않은 추가 멤버와 압축 해제 byte/row budget 초과는 거부한다. 9개 OOXML workbook을 메모리에서 판독하며 경로를 추출하지 않는다. 각 파일의 workbook SHA, 표본 순서, Excel 행 번호, 원 열 이름, raw cell 값 및 formula 위치를 `WdsEventArchive`에 보존한다. receipt는 reader 소스 SHA-256, Python 버전, openpyxl 버전도 고정한다. 호출자는 내부 `check_integrity()`와 `verify_wds_event_archive(record, path)`로 각각 receipt 봉인과 원본 경로의 전체 판독 replay를 확인할 수 있다. reader 코드 revision이 달라지면 기존 receipt 검증은 거부되므로 새 code hash 아래에서 다시 생성해야 한다.

고정 헤더는 압력 4채널, 유량 4채널, VFD 5채널, 아날로그 밸브 2채널, `Labels` 열이다. 실제 아카이브 적용은 9 workbook, 25,165 data row, 706 formula cell, 0 blank cell을 확인했다. directory 기준 행 집계는 clean 9,743, leakage 4,108, sensor failure 5,445, cyberattack 5,869다. 요약은 `research/wds_event_ingestion_summary_v2.json`에 있으며 원 관측 셀은 포함하지 않는다.

Excel formula를 실행하거나 cached result로 대체하지 않는다. 반환 표에서 수식은 원 formula 문자열로 남고 `formula_cells`에서 행·열 위치로 식별된다. 원본 데이터에서 수식은 `Labels`와 `Water Flow` 열에 나타났으며 합계 706개다. 반환은 원형 데이터 접근 API이고 사건 판정기나 분류 모델이 아니다.

## 후속 모듈 어댑터 경계

`select_wds_event_rows`는 caller가 검토한 행 번호를 source receipt와 workbook에 묶는다. 호출자는 `reviewed_artifact_sha256(path)`로 근거 파일 자체의 SHA-256을 계산해 `selection_evidence_sha256`에 전달해야 한다. helper는 일반 파일만 허용하고 읽는 중 파일 identity가 변하면 거부한다. `build_wds_event_evidence_packet`은 이 선택을 `monitoring_replay`, `dataset_partition` 또는 `risk_evidence_review` 목적으로 다른 모듈에 전달할 provenance-only packet을 만든다. packet에는 archive/workbook/selection digest, 원본 행 번호, evidence ID와 artifact SHA-256 및 소비자 계약 ID만 들어간다. `reviewed_artifact_sha256`은 hash helper이며 파일을 신뢰·검토된 것으로 인증하지 않는다; 사용자는 정책에 따라 원 evidence artifact를 보관·검토해야 한다. raw row 값은 포함하지 않는다. packet 생성과 검증 시 선택 receipt를 다시 검증하며, 다른 선택·archive·workbook·evidence artifact digest로 바꿔치기할 수 없다.

provenance packet만으로는 MonitoringSplit·SignalFrame·FTA 확률을 만들지 않는다. 별도의 reviewed mapping과 명시 시계 자료를 제공한 경우에만 아래 SignalFrame adapter가 작동한다. 소비자 쪽은 run grouping, 라벨 의미와 모델 적합성을 추가 검토해야 하며 packet의 목적 값이나 evidence ID 자체는 계측 교정 또는 실험 설계 검토를 증명하지 않는다.


### 검토된 mapping으로 SignalFrame 생성

`build_wds_signal_mapping`과 `build_wds_signal_batch`는 검토자가 명시한 채널 단위 변환 및 시간 자료를 기존 `contracts.SignalFrame`으로 전달한다. 각 채널에는 원 헤더·원 단위·SI 단위·승수·offset을 입력하고, mapping 규칙과 검토 artifact SHA-256을 묶는다. 시계는 별도 `time_s_by_excel_row`로 모든 선택 원행에 하나씩 제공해야 하며, 행 번호나 workbook 순서를 시간으로 대체하지 않는다. 전달한 초 값은 순서대로 엄격히 증가해야 한다. 실제 UTC 시각은 이 API에서 만들어내지 않는다.

숫자형 유한 cell만 변환한다. 빈 셀·formula 문자열·문자값·비유한 값은 `None`과 invalid quality로 보존하고 formula는 실행하지 않는다. `WdsSignalBatch`는 archive/table/selection/mapping/time-axis digest, 명시 초 값과 Excel 원행을 보존한다. `check_integrity(archive, mapping, selection)`는 source/archive·mapping·reviewed selection을 함께 확인하고, source row에서 기대 프레임을 재구성해 값·단위·quality·시각·invalid 집계까지 비교한다. `verify_wds_signal_batch`는 검토 selection과 caller 입력을 다시 적용해 exact replay한다. 이 연결은 source mapping 적용이지 센서 교정의 독립 확인, event label 검증, MonitoringSplit 생성 또는 위험률 산출이 아니다.

### 반복자료 분할 영수증

`build_wds_event_partition`은 각 workbook을 caller가 검토한 group ID에 배정하고, group 단위로 train/validation/test split을 봉인한다. 모든 pinned workbook을 정확히 한 그룹에 넣고 모든 그룹을 정확히 한 split에 배정해야 하므로 한 그룹이 양쪽 split에 들어가는 누수를 거부한다. 분할 규칙·근거 파일 SHA-256·archive 및 workbook digest가 영수증에 포함된다. API는 행 무작위 분할을 만들지 않고, workbook 간 독립성이나 그룹 이름의 과학적 타당성을 증명하지 않는다. 실제 배포 전 PIPE-EXT-016 입력 검토를 통해 workbook이 어떤 실험/run을 나타내는지 근거를 제공해야 한다. 현재 9개 파일 수만으로 일반화 성능 평가가 충분하다고 주장하지 않는다.

## 물리·시계 매핑 경계

`Pressure 1 Out` 같은 열 이름만으로 절대/게이지압, 압력 단위, 포트 위치, 유량 단위, VFD의 물리량 또는 밸브 개도를 해석하지 않는다. workbook의 날짜형 sheet title을 실제 공정 timestamp로 바꾸지 않으며 행 번호를 초나 고정 sample period로 바꾸지 않는다. `WdsEventArchive.units_mapped`, `time_axis_mapped`, `formulas_evaluated`는 항상 false다. SignalFrame·MonitoringSplit이나 고장률 입력으로 연결하기 전에 별도 검토 mapping, 계측 교정, 실제 timestamp/clock 근거, label 생성 규칙을 caller가 제공해야 한다.

하위 폴더 분류와 `Labels` 셀은 이 자료의 시험 설계 표식이다. 이들은 field failure rate, 인과 원인, 결함 발생 순간 또는 일반적인 sensor failure probability를 뜻하지 않는다. 먼저 workbook/run 단위로 분할하고 파일 간 동일 기초 신호 또는 시계 중첩을 확인한다. 행을 무작위 분할해 성능을 보고하지 않는다.

## 사용 예

```python
from pipeline_integrity.wds_event_data import (
    build_wds_event_partition,
    build_wds_event_evidence_packet,
    read_wds_event_archive,
    reviewed_artifact_sha256,
    select_wds_event_rows,
)

record = read_wds_event_archive("data/raw/wds_event_db/WDSEventDB.zip")
record.check_integrity()
review_sha = reviewed_artifact_sha256("research/reviewed-run-partition.md")
selection = select_wds_event_rows(
    record,
    "WDSEventDB/Clean/CleanData.xlsx",
    (2, 3),
    selection_rule="caller-reviewed run partition; see the evidence artifact",
    selection_evidence_id="run-partition-review-01",
    selection_evidence_sha256=review_sha,
)
packet = build_wds_event_evidence_packet(
    record, selection, purpose="dataset_partition", consumer_contract="monitoring.v1"
)
packet.check_integrity(record, selection)
# reviewed_groups and reviewed_splits must cover each pinned workbook once,
# based on the caller's verified experiment/run map.
partition = build_wds_event_partition(
    record, reviewed_groups, reviewed_splits,
    grouping_rule="reviewed experiment/run grouping",
    grouping_evidence_id="run-map-review-01",
    grouping_evidence_sha256=review_sha,
)
for table in record.tables:
    print(table.category, table.event_id, len(table.rows), table.workbook_sha256)
    # Raw values and formula strings are available in table.rows.
```

선택 dependency는 `pip install -e '.[data]'`의 openpyxl이다. 원본 자료 적용은
`python scripts/ingest_wds_event_archive.py data/raw/wds_event_db/WDSEventDB.zip --output research/wds_event_ingestion_summary_v2.json`으로 provenance-only receipt를 만든다. 원본을 다시 읽어 parsing receipt를 재생 검증하며 기존 결과 파일을 덮어쓰지 않는다.

## 검증·개발 상태

실제 자료에서 공급 MD5, 로컬 SHA-256, 38개 archive entry, 전체 CRC, 9개 workbook/headers, 행 수 및 formula 위치를 판독했다. 실제 행의 원 관측값을 status 문서나 source release에 복사하지 않았다. 합성 회귀는 OOXML archive의 해시 거부, formula 보존, receipt 변조, reader revision 고정, source replay, 행 선택, 검토 artifact hash, provenance packet binding 및 SignalFrame 변환 receipt의 재봉인 변조를 다룬다. 회귀 함수 20개는 작성했지만 실행하지 않았다. 이번 로컬 실행은 자료 판독/정적 검사이며 pytest는 아니다. 단위, 공통 시간, 분석 성능, field 전이 및 위험 모델 타당성은 아직 검증하지 않았다. 수치/회귀 검증은 누리온 대기다.
