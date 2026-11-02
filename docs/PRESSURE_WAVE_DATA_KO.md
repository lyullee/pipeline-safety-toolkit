# 실험용 압력 CSV의 제한 읽기

`pressure_wave_data.py`는 공개 실험 자료의 압력 CSV를 읽기 위한 얇은 어댑터이다. 신호 도착 검출기나 누출 위치 해석기를 재구현하지 않는다. 검토된 단위·센서·시계 매핑을 공급한 뒤 기존 신호 모듈의 입력 계약으로 연결해야 한다.

## 확보 및 실제 확인 범위

Mendeley Data `xw44wv2g88`, version 1의 공급 압축파일은 717,637,500 bytes이며 SHA256은 `c6d4a68901a145155a58ccb691769753cb7c0960416a375acac12128bedfd881`이다. 공개 메타데이터의 라이선스는 CC BY 4.0이다. 원본과 공개 메타데이터의 귀속·버전·라이선스를 함께 보존한다. [공급 데이터 페이지](https://data.mendeley.com/datasets/xw44wv2g88/1)를 인용한다.

별도 Mendeley DOI `10.17632/tbrnp6vrnj.1`도 같은 저자, 280개 신호, 같은 길이/재료의 시험대, 같은 6개 센서 배치 및 누설·유량·배치 요인 설명을 게시한다. [tbrnp6vrnj 기록](https://data.mendeley.com/datasets/tbrnp6vrnj/1), [xw44wv2g88 기록](https://data.mendeley.com/datasets/xw44wv2g88/1). 메타데이터상 강한 중복 후보로 보고 별도 물리 실험군처럼 취급하지 않는다. 현재 저장소에는 xw44wv2g88 공급 아카이브만 있으며, tbrnp6vrnj의 공식 파일 크기·해시·member 목록 및 두 아카이브의 행/파일 수준 비교는 확보되지 않아 동일 데이터라고 단정하지 않는다. 취득 로그의 `dynamic_pressure_v1.zip` 이름과 현재 catalog의 전체 ZIP 자산명도 달라 재취득 전에 대상을 확인해야 한다. 추적표는 `research/leak_dataset_crosswalk.json`이다.

2026-10-28에 사용자 제공 원본으로 공급 체크섬과 허용목록 멤버를 다시 검사했다.
동압 CSV는 80개(압축 해제 기준 합계 1,883,226,891 bytes)이며 2개 네트워크 유형,
5개 누수 조건, 4개 운전 조건, P1/P2 조합을 이룬다. 전체 압축 해제나 로컬 절대경로
기록 없이 제한된 archive reader로 조사했다. 이 구조 검증은 단위·압력 기준·공통 trigger
또는 누수 발생시각의 검증을 뜻하지 않는다. 기계 판독 상태는
`research/pressure_archive_verification_status.json`에 둔다.

Windows 기본 tar의 목록 읽기로 일반 파일 형식의 Dynamic Pressure Sensor CSV 80개를 확인했다. Branched/Looped, 누출 조건 5종, 배경 유량 조건 4종에 각각 P1/P2가 있어 이름상 40쌍이다. 멤버별 경로·크기·조건은 `artifacts/pressure_wave_data/inventory.json`에 보존한다. 각 CSV는 약 23–26 MB이다. 이는 이름에 따른 실험 조건의 연결이며 센서 동기화의 증거가 아니다.

이전 No-leak/Transient 전체 선택 CSV 읽기는 30 MiB/60초 출력 제한에서 timeout이었다. 이후 같은 파일명 조건의 P1/P2에서 각각 처음 8개 완성 행만 읽었다. 두 파일 모두 `Sample,Value` 헤더와 7개 수치행이며 관측 좌표 prefix는 같고 값 prefix는 다르다. No-leak/Transient 파일을 포함한 나머지 헤더, 전체 CSV 형식, 압력 단위, 압력 기준, 센서 간 공통 시계 및 Sample 열의 단위는 **미확인**이다. prefix 관계를 Pa 단위 신호 또는 동기화 확인으로 표시하지 않는다.

공급 설명의 Transient는 기록 약 20초 시점에서 배경 유량이 바뀌는 조건이다. 누출 조건 이름과 별개의 실험 요인이다. 그 시각을 누출 발생 시각, 압력파 도착 정답 또는 센서 지연 정답으로 입력하지 않는다. 또한 공급 설명의 8000 Hz는 hydrophone 자료의 정보이며 압력 센서 표본율로 옮겨 쓰지 않는다.

## 안전한 접근 API

- `pressure_member_labels(member)`는 검토된 압력 CSV 경로, 네트워크/누출 코드의 일치와 P1/P2 조건을 확인한다. 절대 경로, 상위 이동, 역슬래시, 드라이브 경로, 실행 파일은 거부한다.
- `verify_pressure_archive(path)`는 실제 크기와 전체 SHA256을 확인한다. reader 호출 시 목록이나 캐시의 확인 표시를 신뢰하지 않는다.
- `list_pressure_members(..., tar_executable=..., byte_budget=..., timeout_s=...)`는 체크섬 확인 후 일반 파일 멤버만 목록화한다. 이 구현은 Windows bsdtar의 현재 verbose 출력 형식에 맞춘다. 다른 도구 형식은 별도 검토 없이 사용하지 않는다.
- `read_pressure_member(...)`는 일반 파일 목록의 선택 항목 및 선언 크기를 확인하고 stdout으로만 읽는다. 압축파일의 경로를 디스크 경로로 실행하거나 링크를 생성하지 않는다. 총 반환 데이터는 명시 byte budget, 프로세스 시간은 명시 timeout으로 제한한다. 전체 해제 API는 제공하지 않는다. Solid 압축에서는 선택 파일만 출력하더라도 앞선 압축 블록을 내부적으로 읽는 비용이 발생할 수 있다.

압축 도구는 사용자가 지정한 신뢰된 실행파일이어야 한다. 이 프로젝트는 별도 archive 바이너리나 원본에 포함된 프로그램을 배포/실행하지 않는다. 본 환경에서는 `C:/Windows/System32/tar.exe`를 사용했다. 실패 시 조용히 일부 데이터만 채택하지 않으며 오류를 호출자에게 전달한다.

## 명시적 단위와 시계 매핑

공개 [Data in Brief 자료 논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC10147960/)은
동압 센서를 PCB 102B16, 취득 장치를 NI-9234, 취득 소프트웨어를 LabVIEW NXG 5.1로
설명하며 동압/가속도 측정 주파수를 51.2 kHz로 보고한다. 동압 센서 표에는 감도
7.25 μV/Pa와 1–689 kPa 범위가 제시된다. 이 사실은
`pressure_source_publication_metadata()`에 원 출처와 함께 기록한다.

장비 감도와 명목 취득 주파수만으로 저장 CSV의 `Value`가 센서 전압인지 이미 변환된
압력인지, `Sample`이 초인지 다른 좌표인지 결정할 수 없다. P1/P2가 같은 trigger와
sample index를 공유한다는 근거도 자료 논문의 일반 취득 설명만으로 확정하지 않는다.
따라서 이 함수의 CSV 변환·압력 기준 필드는 null이고 pairing flag는 false다.
첫 prefix의 좌표 간격과 출판 주파수의 대응도 아직 설명되지 않았으므로 주파수를
`parse_pressure_csv`의 시간 계수로 자동 전달하지 않는다.

`parse_pressure_csv`는 구분자, 헤더 행 수, 시간 열과 압력 열, 초 및 Pa 변환 계수, 압력 오프셋, 센서 ID, 시계 ID, 최대 행 수, `mapping_evidence_id`, `pressure_reference`를 필수로 받는다. 압력 기준은 absolute/gauge/dynamic_perturbation 중 하나를 공급해야 한다. 알 수 없는 단위를 추측하는 기본 매핑은 없다. 헤더와 원본 해시를 유지하고 비유한 값, 시간 역행/중복, 열 불일치와 행 예산 초과를 거부한다. 음의 동압은 자동으로 잘라내지 않는다. 행 수 예산은 반환 자료를 조용히 자르는 용도가 아니다.

CSV quoting은 strict parser로 읽는다. 닫히지 않은 따옴표 등 문법 오류와 Python CSV reader의 field-size 제한은 `ValueError` 계약으로 정규화하며, 긴 행이나 field를 부분 채택하지 않는다. 한 행의 큰 비선택 열도 parser 자원 한도에 걸릴 수 있으므로 호출자는 원 형식을 별도로 검토해야 한다. 이 에러 정규화는 열·단위·샘플률의 물리적 의미를 보증하지 않는다.

이 parser를 호출했다는 사실은 매핑 근거의 독립 검증을 뜻하지 않는다. 실제 자료의 단위/압력 기준이 해결되기 전에는 호출하지 않고 데이터 준비 상태를 `needs_data`로 유지한다. 센서 파일 이름이 짝이라는 이유로 clock ID를 동일하게 부여하지 않는다. 반환의 `shared_clock_verified`, `arrival_ground_truth`, `leak_onset_ground_truth`는 각각 거짓, null, null이며 후속 독립 검토가 필요하다.

## 개발·검증 상태와 후속 작업

원본 체크섬과 목록 읽기는 실행한 데이터 취급 작업이다. 코드는 Ruff와 Python 컴파일을 통과했으며 pytest 및 수치 검증은 사용자 지시에 따라 실행하지 않았다. 시험 코드는 경로 공격/조건 코드 불일치, 체크섬 우선 확인, 단위 매핑, 음의 동압, 시간 순서, 결측/비유한 값, quoting/field-size 오류 및 자원 제한 위반을 다룬다. 후속 누리온 환경에서 `python -m pytest tests/test_pressure_wave_data.py -q`를 실행하고 실제 결과를 기록한다.

후속 데이터 준비에서는 압력 CSV의 소수 원행과 단위 설명을 먼저 확인한다. 공통 시각·표본율·센서 위치·압력 기준의 근거를 확보한 다음 SI 시계열을 생성하고, 원본 조건과 파형 도착 정답을 분리하여 기존 NPW 모듈에 연결한다. 현재 상태로 알고리즘 성능, 누출 위치 오차 또는 현장 적용성을 주장할 수 없다.


## 교차 정적 검토 보강

목록과 선택 stdout 추출 전후에 원 압축파일 hash 및 실제 archive executable hash를
대조하여 읽는 도중 source/tool이 바뀌면 자료를 채택하지 않는다. 따라서 큰 원본의
전체 hash 재확인 비용이 발생한다. 실제 추출을 이번 검토에서 다시 실행하지 않았다.
일반 mode만으로 링크가 없다고 가정하지 않고 verbose link count도 확인한다. 원 catalog
entry SHA·출처·귀속·권리·origin 및 도구 identity를 선택 member receipt에 보존한다.

CSV 반환은 전체 mapping(열·단위계수·offset·기준압·행 한도·sensor/clock/evidence)을
포함하고 reader source SHA와 record_digest를 가진다. `verify_pressure_csv_record(record, raw)`는
원 bytes와 mapping으로 다시 읽어 비교한다. unsigned seal은 매핑/교정의 독립 검증이 아니다.
absolute pressure의 0 이하 값만 거부하며 음의 gauge/perturbation은 유지한다. 원 시계의
음수 pre-trigger 시각도 유지하고 자동 shift하지 않는다. Nonnegative start를 요구하는
SampledTrace로 변환할 때에는 검토된 별도 origin 변환이 필요하다. pressure와 시간의
단위 계수에 bool을 쓰지 않으며 raw CSV는 128 MiB의 명시 상한으로 제한한다.
새 replay·absolute 기준·linked member·원본 교체 시험은 작성했으며 미실행이다.


## 첫 파일의 부분 형식 확인

전체 No-leak CSV 읽기의 기존 timeout과 별도로, archive 목록의 첫 pressure CSV에서
처음8개 텍스트행을 제한적으로 읽었다. 원 archive/tool을 전후 확인했고 헤더는
`Sample,Value`이며 일부 값에 음수가 있다. 이 기록은 한파일의 prefix뿐이다.
전체 CSV·80파일 공통형식·SI unit·pressure reference·동시시계는 확인한 것이 아니다.
음수가 있다는 이유로 Pa/gauge/변동압으로 자동 분류하지 않는다. 출판자 설명의
hydrophone8kHz를 dynamic pressure에 전용하지 않는다. 이후 확인한 자료 논문의 동압
51.2kHz도 `Sample` 열 변환과 동일하다는 별도 근거 전에는 자동 적용하지 않는다.

재현용 `scripts/inspect_pressure_source_prefix.py`는 검토된 regular member만 stdout으로
읽고 줄/바이트/시간 예산에서 중단한다. 원본/도구 변경이나 timeout이면 prefix를
채택하지 않으며 complete_csv_extracted는 항상 False다. 출력에는 일부 원자료가
있으므로 artifacts에 보관한다. 실제 형식확인은 private prototype으로 수행했으며
새 재현 스크립트 자체는 정적검사만 했다. 모델·parser의 단위 mapping·수치시험은 실행하지 않았다.
공개 요약은 `research/pressure_prefix_review_status.json`이다.

## Prefix script의 독립 안전 검토

`scripts/inspect_pressure_source_prefix.py`는 version 2 receipt를 제공한다. 요청한 complete LF/CRLF 행 수, byte 상한과 압축 프로세스 시간 한도를 구분한다. 목표 행을 얻어서 reader가 의도적으로 종료시킨 tar와, tar 자체의 nonzero failure를 구별한다. 자연 실패·timeout·source/tool 변경·행 목표 미달이면 일부 정상 텍스트가 있어도 채택하지 않는다. CR만 도착한 미완성 CRLF는 완성 행으로 세지 않는다. 따옴표가 끝나지 않았거나 multiline인 numeric field, nonfinite 값, 열 폭 불일치는 별도의 format 상태로 남긴다.

`inspect_pressure_source_prefix(...)`는 원 inventory와 선택한 regular-file entry, catalog/source/license/origin, 실제 tar 및 reader/script 지문을 결합한다. raw capture bytes/hash와 실제 채택한 prefix bytes/hash를 구분하고 원 bytes는 base64로도 보존한다. 이는 이전 prototype의 4096-byte capture hash와 8행 텍스트 파일을 같은 파일의 checksum으로 혼동하지 않기 위한 것이다. 이전 prototype은 새 영수증 규격으로 검증된 기록이 아니며 기존 출처 관측으로만 유지한다.

`review_prefix_payload`는 형식만 검사한다. 알려진 `Sample,Value` label과 두 numeric 열을 확인해도 Sample을 초 또는 순번으로 정하지 않는다. 증가하는 값 간격에서 Hz를 계산하거나 음의 Value를 gauge/dynamic pressure로 분류하지 않는다. 다른 헤더면 별도 format review 상태이다. prefix 전체를 추출했더라도 `complete_csv_extracted=False`, 단위/기준/도착 정답은 null, 동기화 검증은 false이다.

`verify_pressure_prefix_receipt(record)`는 seal·payload·format·채택 조건의 내부 일관성을 확인한다. 실제 archive를 다시 읽거나 과거 실행을 인증하지 않는다. `write_pressure_prefix_receipt`는 새 파일을 exclusive-create로만 생성한다. 원 archive/tool 경로와 충돌하거나 기존 파일이 있으면 덮어쓰지 않는다. CLI도 고비용 source 접근 전에 기존 output을 거부한다.

압축 프로세스 timeout은 전체 작업의 wall-clock 상한이 아니다. 기존 reader가 inventory 전후 및 prefix 후에 실제 archive hash를 확인하므로 718 MB 파일을 반복 읽는 비용이 따로 발생한다. v2는 inventory/preverification, 압축 프로세스 및 전체 경과 시간을 나누어 기록한다. cached artifact를 실제 archive 무결성 검증의 대체물로 쓰지 않는다.

## P1/P2 제한 형식 비교 — 실행 완료

같은 `Branched/Circumferential Crack/BR_CC_0.18 LPS` 조건의 P1과 P2를 각각 8개 complete text lines, 최대 4096 stdout bytes, 압축 프로세스 최대 45초로 읽었다. 두 실행 모두 원 archive의 전체 SHA-256과 tar 지문을 전후 대조했고 v2 receipt의 seal·payload·format·채택 조건을 다시 검사했다. 원행은 private artifact에만 두고 공개 상태에는 관계만 기록한다.

`compare_pressure_prefix_pair`는 두 receipt를 먼저 검증하고 파일명상 한 조건의 P1/P2인지 확인한 뒤 헤더·좌표·값 prefix의 동일 여부만 반환한다. 실제 결과는 헤더와 7개 좌표가 같고 값은 다르다. 좌표 단위와 값 단위는 null이며 common trigger와 clock synchronization은 false다. 별도 근거 전에는 NPW 도착 검출/속도·위치 평가로 진행하지 않으며 20초 background flow 전환을 leak onset으로 사용하지 않는다.

## 분석 입력 자격 계약

`qualify_pressure_pair(...)`는 전체 CSV의 명시적 단위 매핑이 해결된 뒤 사용하는 공개
경계다. 두 원 byte payload로 각 sealed parse record를 재생하고, 선택 member가 같은
파일명 조건의 P1/P2인지 확인한다. `pairing_evidence_id`, common-trigger 검토 결과와
sample-alignment 검토 결과는 호출자가 명시해야 하며 파일명이나 같은 좌표만으로 true가
되지 않는다. 두 검토가 모두 true일 때에도 parsed clock ID와 SI 시간 좌표가 실제로
같지 않으면 모순으로 거부한다.

검토가 미완료이면 원본·record digest·관측된 clock/coordinate 관계를 보존한
`needs_pairing_evidence` 영수증을 반환한다. 자격화가 성공해도 arrival/leak-onset 정답은
생성하지 않으며, 단위 매핑의 독립 검증을 대신하지 않는다. 따라서 후속 NPW·음향·역추정
소비자는 이 영수증의 상태를 먼저 확인해야 한다. 구현 상태는
`research/pressure_pair_qualification_status.json`을 따른다.

`verify_pressure_pair_qualification(...)`은 호출자가 고정한 expected evidence ID와 두
원 payload를 받아 parse record와 qualification을 전부 재생한다. 영수증 내부의 unsigned
digest만으로 변경된 외부 근거를 신뢰하지 않으며, 근거 ID·원 bytes·mapping·member 또는
구현 결과가 달라지면 이전 자격 영수증을 재사용하지 않는다.

## 고정격자 파형 브리지

`build_pressure_pair_trace_record(...)`는 ready 자격 영수증과 두 원 CSV/parse record를
다시 검증한 뒤 공통 `SampledTrace` 두 개를 만든다. 최소8개 동일 좌표가 필요하고,
전체 좌표를 양 끝으로 정의한 고정격자에 대조해 관측 최대 오차가 호출자 상한을 넘으면
거부한다. 보간·재표집·결측 채움은 하지 않는다. 각 trace의 `source_digest`는 해당 CSV
SHA-256이고 pressure 값은 parse record의 Pa 값을 그대로 사용한다.

`SampledTrace`는 음이 아닌 시작 시각을 요구하므로 pre-trigger 음수 좌표를 조용히
0으로 옮기지 않는다. 호출자가 `time_origin_shift_s`와 별도
`time_origin_evidence_id`를 명시해야 하며, 이동 뒤에도 시작이 음수면 거부한다.
asset, distinct trace ID, measured/laboratory origin, 허용 grid error도 명시 입력이다.
`verify_pressure_pair_trace_record(...)`은 고정한 pairing evidence와 원 bytes부터 이
변환을 재생한다. 상태는 `research/pressure_pair_trace_bridge_status.json`을 따른다.

이번 체크포인트에서는 P1/P2의 제한 source driver와 전체 archive 재hash를 실제 실행했다. complete CSV, parser 단위 mapping, 수치 모델, native 계산 또는 pytest는 실행하지 않았다. 비교 계약 회귀 2개를 추가했으며 누리온 실행 대기다. 기존 `pressure_wave_data.py` 구현과 shared inverse/friction 파일은 변경하지 않았다.
