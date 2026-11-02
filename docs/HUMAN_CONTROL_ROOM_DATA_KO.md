# 공정 제어실 사람-시스템 실험 자료

## 자료와 재사용 조건

Zenodo v1.1.2의 [Human-in-the-Loop Decision Support in Process Control Room Dataset](https://zenodo.org/records/13984198)은 제어실 시뮬레이터 실험의 참가자별 설문·상태 인식·부하·시뮬레이터 결과를 제공한다. 게시 기록에는 CC BY 4.0과 MD5가 선언되어 있다. 인용은 Ammar N. Abbas, Winniewelsh, 그리고 기록에 연결된 Amazu et al.의 2024년 *Data in Brief* 논문을 포함한다. 기준 URL·버전·자산명·체크섬은 `src/pipeline_integrity/catalog_data/datasets.json`에 고정했다.

Zenodo 자산은 116,494 bytes, MD5 `22c0303c109ea5f00e4cfe22ca954db0`, 취득 SHA-256 `b7c317f96354dbfd587656e0e4d4c9fe6f2a6c9d1b0df808b46efe67e4090038`이다. ZIP 안의 Excel SHA-256은 `1fbc5bea29ae4cb543b90fbbdd214027b3334f354c77210ee0501b07c6e981f7`다. 원본은 `data/raw/human_control_room/`에만 둔다. 원본 ZIP에는 별도 MIT LICENSE 파일도 들어 있으므로 이는 Zenodo 자산의 CC BY 선언과 구분해 보존한다. 이 프로젝트는 원본 ZIP, Excel 행, README 또는 license 파일을 코드 배포에 복사하지 않는다.

워크북에는 나이·성별 등 개인 특성과 참가자 코드가 포함되어 있다. 온라인 공개 자료라는 이유만으로 이 프로젝트의 저장소나 공개 결과에 참가자 행을 복사하지 않는다. 분석이 필요하면 권한·윤리·재식별 위험을 해당 연구에서 별도로 검토하고, 원본은 로컬의 접근 통제된 위치에 둔다.

## 원본 구조와 품질 경계

`read_human_control_room_archive(path)`는 catalog에 고정된 자산 크기와 체크섬, ZIP 멤버 allowlist, CRC, workbook SHA, sheet 순서 및 파싱 예산을 확인한 후 압축 해제 없이 읽는다. 수식은 실행하지 않고 수식 문자열을 보존한다. 반환 `HumanControlRoomArchive`와 `HumanControlRoomTable`에는 참가자 수준 행이 메모리에 있으므로 이 객체를 통째로 JSON 직렬화하거나 공개하지 않는다. `verify_human_control_room_archive(record, path)`는 동일 원본을 다시 읽어 exact replay를 확인한다.

공개 가능한 구조 요약은 `summarize_human_control_room_archive(record)`로 만든다. sheet별 행·열 수, 공백/수식 수, header hash 및 참가자/시나리오 고유 개수만 기록하고 헤더 문자열, 참가자 식별값, 행 값은 내보내지 않는다. 다음 명령은 해당 요약을 exclusive-create로 저장하고 입력 원본 replay를 수행한다.

```powershell
python scripts/ingest_human_control_room_archive.py `
  data/raw/human_control_room/dataset-v1.1.2.zip `
  --output research/human_control_room_ingestion_status_v3.json
```

확인한 워크북에는 11개 sheet와 총 1,458개 데이터 행이 있다. 게시 기록의 참가자 수 설명은 92명이나, 제공된 워크북의 참가자별 기본 정보 표는 57개 식별자이고 다른 표는 24~66개 식별자로 집계됐다. 따라서 게시 설명과 실제 파일의 표본 구성이 일치한다고 가정하지 않는다. reader receipt에는 이 불일치 검토가 완료되지 않았다고 표시하며 원 ID는 남기지 않는다. 표마다 결측 수가 다르므로 완전 사례 선택 및 결측 기전 검토 없이 통합하지 않는다.

## HRA 연결 방법

이 데이터는 포름알데히드 생산 공정의 통제된 시뮬레이터에서 수집됐으며 배관 현장 실험이 아니다. 시나리오·참가자 반복자료는 participant 단위로 묶어야 한다. 행 무작위 분할은 같은 참가자 또는 같은 시나리오의 반복을 평가 구간 양쪽에 누출시킬 수 있으므로 사용하지 않는다.

워크북의 성과·반응 관련 열 이름만으로 정확한 동작 정의, 성공/오류 상태, 시간 단위, cue 발생 시각, 사용 가능 기한 또는 공정 결과를 추론하지 않는다. `HumanResponseClient`, `InformationResponseStudy`, cluster bootstrap 및 기존 FTA adapter는 의미가 검토된 기회·오류·시간·모집단을 caller가 제공한 경우에만 연결한다. mapping evidence와 원 행 ID를 로컬 연구 기록에 보존하고, 공개 receipt에는 표본 행이나 participant identifier를 넣지 않는다.

부하/상황인식 점수의 상관이나 그룹 차이는 인과효과, 인간 실패 확률, 위험 빈도 또는 배관 운영자 모집단으로의 전이를 입증하지 않는다. 시간·오류 outcome의 시계와 정의, 결측·중단 처리, 연구 설계/그룹 배정, 참가자 단위 평가 및 독립 재현이 확보될 때까지 이 자료의 용도는 탐색적 구조 판독과 caller-reviewed 연구 설계로 제한한다. 누리온 회귀와 계산 검증도 별도 검증 큐에 있으며 자료의 연구 적합성을 자동 승인하지 않는다.

`human_control_room_study.HumanControlRoomOutcomeMap`은 `simulator_logs`의 허용 필드(`Consequence` 또는 `Overall Performance`)에 대해 성공/실패 값을 호출자가 직접 정하고, 작업·기회·모집단 정의와 mapping evidence digest를 첨부하도록 한다. `summarize_human_control_room_study` v2는 참가자 ID, 원 outcome label과 행을 내보내지 않고 시나리오별 참가자 수, 행 단위 매핑/미매핑/성공/실패 건수, 고유 participant-scenario 묶음의 success-only/failure-only/mixed/unmapped 상태, 묶음 안의 추가 행 수와 mapping digest를 기록한다. 반복 행은 참가자별 독립 시행으로 간주하지 않으며 상충/미매핑 묶음은 별도 집계한다. 매핑 evidence의 citation은 task definition과 같아야 하고 evidence ID·SHA-256이 context와 일치해야 한다. 이는 연구자가 승인한 outcome 분류의 기술 통계이지 관측 HEP, 신뢰구간, 연간 빈도, 독립성 또는 현장 자격이 아니다. `InformationResponseStudy`와 기존 FTA 연결에는 outcome 의미, 기회 정의, 시계, 결측 정책과 표본 설계의 별도 검토가 선행돼야 한다.

## 검증 기록

Zenodo 게시자 MD5와 취득 SHA-256을 대조했고, ZIP CRC를 확인했다. 내부 workbook의 SHA-256과 11개 sheet, 행 수, 열 개수, 결측/수식 집계를 source-sealed reader로 다시 재생했다. participant-level 원행은 연구 환경에서만 메모리에 읽었고 공개 receipt에는 넣지 않았다. 합성 회귀는 workbook formula 보존, Excel 행 식별, 공개 요약의 비식별 경계, 원본 pin 거부와 receipt 변조를 다룬다. 이 회귀와 Nurion 작업은 아직 실행하지 않았다. 원 HRA 계산·현장 적용·인과/예측 분석도 수행하지 않았다.
