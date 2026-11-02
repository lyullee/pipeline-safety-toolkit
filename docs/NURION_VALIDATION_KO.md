# 누리온 병렬 검증 준비

현재는 실행 준비 코드이며 누리온 제출·성능·수치 결과를 검증하지 않았다.
Windows 가상환경을 옮기지 않고 계산 노드에 맞는 Linux Python 3.12 이상과
프로젝트 extras 및 기존 소유 엔진을 별도 설치한다. 실행 버전은 requirements-tested.txt를
참고하되 해당 아키텍처의 의존성 설치 가능성을 먼저 확인한다.

PBS 템플릿은 jobs/nurion_pytest.pbs다. 현재 계정의 큐·과제·자원 요청을
qsub 옵션으로 지정하고 PIPELINE_HPC_PYTHON에 계산 노드 Python 절대 경로를 준다.
공식 [PBS 안내](https://docs-ksc.gitbook.io/nurion-user-guide/undefined/running-jobs-through-scheduler-pbs)를
확인한다. 큐·과제 번호는 이 저장소에서 추정하지 않는다.

## 두 공개 누수 데이터 등록본의 대용량 content crosswalk

2026-10-29에 tbrnp6vrnj.1 집계 ZIP 1,080,066,320바이트와 기존 xw44wv2g88.1 7z 717,637,500바이트를 모두 로컬 원본 SHA-256 및 크기로 확인했다. 두 등록본의 80/80/122개 센서 멤버 상대경로·바이트 수는 같지만 내용 SHA는 미비교다. 전송 묶음 `artifacts/hpc/data-plan-20261029-leak-crosswalk-v1.json`은 1,797,703,820바이트이며 raw를 코드 배포물에 포함하지 않는다.

검증 전에 전송 plan을 검토한 다음, 사용자 지시에 정한 공유 경로로 두 파일을 같은 raw root 아래 각각 `leak_testbed/` 및 `leak_multisensor_transients/` 하위에 둔다. `scripts/hpc_data.py check`로 전송 후 두 파일의 SHA를 확인한다. 노드의 `tar`는 7z를 읽는 libarchive/bsdtar여야 한다. 다음 명령은 282개 센서 파일을 경로·길이·SHA-256으로 비교하고 값이나 파형을 저장하지 않는다.

```bash
bash jobs/run_leak_deposit_crosswalk.example.sh \
  /path/to/reviewed/raw-root \
  /path/to/persistent/leak_dataset_crosswalk_results.json
```

각 sensor member는 별도 추출로 경계를 보존하고 최대 1 MiB 블록을 최대 2 GB scratch 파일에 기록·해시한 뒤 즉시 제거한다. 처리량 최적화보다는 멤버 경계의 명시성과 bounded memory/scratch를 우선한다. 결과에 byte-identical member set이면 디지털 파일 동일성만 확인된 것이다. 독립 실험·시행 수, 계측 단위/교정, 시계 정합, 누수 시작 정답 또는 현장 자격을 확정하지 않는다. 이 작업은 큐에만 추가했으며 PBS 제출은 없다.

## HRA 공정 제어실 자료 replay 준비

Zenodo v1.1.2 원자료는 116,494 bytes이며 transfer plan `artifacts/hpc/data-plan-20261029-human-control-room-v1.json`에서 1개 dataset/file, publisher license/citation, SHA-256과 함께 고정했다. 로컬 `hpc_data check`는 `verified=true`, numerical_tests_executed=0으로 완료했다. source-only test bundle에는 raw file이 없다.

현재 HRA study 회귀는 raw ZIP이 필요하므로 manifest에 116,494 bytes `data_fixture`가 pin되어 있다. 실제 실행 시 전송 plan과 raw-root에 맞게 `jobs/nurion_human_control_room_data.example.json`을 복사·수정해 `--data-map`으로 주고, rank staging은 각 shard에 사본을 만들며 체크섬을 다시 확인한다. 코드 bundle 자체에는 raw가 없다. source replay receipt 생성이 필요하면 데이터 extra의 openpyxl이 설치된 Python 환경에서 아래 CLI를 실행한다.

```bash
python scripts/hpc_data.py check \
  --plan artifacts/hpc/data-plan-20261029-human-control-room-v1.json \
  --raw-root /path/to/reviewed/raw-root \
  --maximum-bytes 116494 \
  --output artifacts/hpc/data-check-human-control-room.json
python scripts/ingest_human_control_room_archive.py \
  /path/to/reviewed/raw-root/human_control_room/dataset-v1.1.2.zip \
  --output artifacts/hpc/human-control-room-ingestion.json
```

이 캠페인은 표 구조와 source replay를 확인하며 HRA outcome mapping, participant population reconciliation, field transfer 또는 HEP 추정을 검증하지 않는다. 원 participant rows를 public report나 소스 번들에 포함하지 않는다. PBS 제출은 별도 사용자 지시가 있을 때만 한다.

percussion RAR는 5.43 GB이며 source bundle/data fixture 어느 쪽에도 포함되지 않았다. `percussion_data` real-member replay를 누리온에서 재현하려면 추후 별도 RAR transfer plan과 staging budget을 검토해야 한다. 현재 해당 자료의 selected-member parsing은 로컬 source replay로만 확인했고 regression functions는 이번 targeted campaign의 synthetic checks로 둔다.

## 실행 구성

scripts/hpc_tests.py prepare는 시험 모듈을 분할하고 소스 체크섬을 고정한다.
시험을 실행하거나 수집하지 않는다. PBS 템플릿은 할당된 MPI rank마다 run을
실행하고 aggregate로 전체 결과를 확인한다. rank별 소스 복사본·로그·JUnit·receipt가
분리되며 BLAS 스레드는 1로 제한한다. 실제 작업 수와 노드 배치는 계정 자원에 맞춘다.

외부 소유 엔진은 jobs/nurion_sources.example.json 형식의 비공개 경로 매핑을
만들고 PIPELINE_HPC_SOURCE_MAP으로 지정한다. 소스 트리 체크섬을 기록하고
각 rank의 비공개 owner 복사본을 만들고 그 import root를 자식 시험의 PYTHONPATH에 연결한다.
경로 지정 자체가 이용 허락이나 모델 검증을 뜻하지 않는다.
원자료와 외부 소유 엔진은 소스 번들에 포함하지 않는다. 자료 의존 검사는 필요한
자료를 별도로 준비해야 하며 생략을 통과로 바꾸면 안 된다.

누락 rank, 실패·오류, 중복 사례, 소스 변경, 기본 설정의 skip은 집계 실패다.
각 rank 제어 프로세스의 종료 0만으로 통과를 판단하지 않는다. aggregate의
결과와 실제 JUnit을 확인한다. 기본 결과는 artifacts 아래에 보관한다.
공개 요약은 검토한 뒤 --public-summary로 명시적으로 저장한다.

## 전송 준비

```bash
python scripts/hpc_tests.py bundle --output artifacts/hpc/nurion-source.tar.gz
python scripts/hpc_tests.py prepare --shards 8 --manifest artifacts/hpc/manifest.json
```

bundle은 공개 소스 허용 목록만 묶고 파일별 SHA-256을 남긴다. 실행 소스가 아닌
`research/validation_queue.json`은 계획과 번들의 자기참조를 피하도록 제외하며,
계획은 작업 디렉터리에서 별도로 보관한다. 원자료·캐시·외부
엔진·가상환경은 제외한다. 이 번들은 검증 완료 배포본이 아니다.
prepare 이후 소스를 바꾸면 계획을 새로 만든다. 변경 중인 연구 파일도 체크섬에 포함된다.

대기 범위는 research/validation_queue.json이다. 이전 전체 2,403개 결과와 새 소스를
구분하고, 새 시험의 실제 실행 수는 누리온 JUnit이 생성된 후에만 보고한다.

### 원자료 별도 전송 확인

`scripts/hpc_data.py`는 기존 `research/data_inventory.json`에서 명시한 자료군만
고정한다. downloader·물리 모델을 실행하지 않고, 원자료를 코드 번들에 넣거나
다른 위치로 복사하지 않는다. 자료 목록의 취득 허용·선택 파일 완결 상태와
인용·라이선스·권리 검토를 보존하며, 같은 크기와 SHA-256인지 원자료를 스트리밍해
확인한다. 송신 측과 수신 측에서 같은 plan을 사용한다.

```bash
python scripts/hpc_data.py prepare --inventory research/data_inventory.json --only pipe_friction_roughness --output artifacts/hpc/friction-transfer.json
python scripts/hpc_data.py check --plan artifacts/hpc/friction-transfer.json --raw-root data/raw --maximum-bytes 12340 --output artifacts/hpc/friction-check.json
```

명령은 새 output만 생성한다. 이미 있는 결과는 덮어쓰지 않는다. 경로는 raw root
아래의 `dataset_id/filename`으로 제한하며, traversal·절대 경로·대소문자 충돌·
symlink·다중 hard link를 거부한다. 파일 크기가 맞을 때만 지정한 총 바이트 예산
안에서 해시를 읽는다. 읽기 전후 파일과 열린 descriptor의 식별자·크기·수정 시각을
비교한다. Windows에서 경로 stat과 descriptor fstat의 ctime 의미가 다를 수 있어
ctime은 각 API 안에서 전후 비교한다. 이 검사는 적대적인 동시 파일 변경에 대한
파일 시스템 격리나 서명 인증을 제공하지 않으므로, 전송 중 쓰기를 멈춘 자료를 쓴다.

누락·크기 불일치·해시 불일치는 `verified=false`, 종료 1로 남긴다. 선택한 자료만
확인하므로 모든 자료 준비나 시험 fixture의 경로 구성까지 증명하지 않는다.
기존 owner source map과 시험의 자료 배치 계약은 별도로 맞춰야 한다.
plan digest는 실수에 의한 변조 확인용이며 권한 있는 발행자의 서명이 아니다.
`verified=true`는 원본 바이트 일치만 뜻한다. 단위·시계·측정 타당성·재배포 권리나
수치 검증을 승인하지 않는다. analysis-only 자료의 기존 제한도 유지한다.

현재 로컬에서 관 마찰 자료 4파일·12,340바이트의 전송 확인을 실제 수행했다.
초기 확인에서는 Windows ctime API 차이로 보류했고 수정 후 네 해시 모두 일치했다.
이는 파일 무결성 확인이며 새 회귀 시험의 통과 수에는 포함하지 않는다.

### 시험 rank의 원자료 fixture 배치

`scripts/hpc_tests.py`의 `--data-map`은 전송 plan, 검토한 raw root, 자료를 실제로
요구하는 시험 모듈, rank별 복사 허용 바이트를 결합한다. 형식은
`jobs/nurion_data.example.json`을 따른다. private data map은 source bundle에 넣지
않는다. prepare는 plan의 권리·인용·파일 SHA와 소비 시험이 현재 suite에 있는지
검사해 manifest에 경로 없는 identity만 고정한다. raw root의 절대 경로는 manifest와
rank receipt에 기록하지 않는다.

run은 해당 시험이 배정된 rank에만 `work/data/raw/<dataset_id>/<filename>`을 만든다.
복사 전 원본, 복사 직후 staging, pytest 종료 뒤 staging과 원본을 같은 plan으로
다시 검사한다. 원본·staging 변경, 누락, 해시 불일치, 소비 시험 변경은
`provenance_unchanged`를 만족하지 못한다. 다른 rank에는 원자료를 복제하지 않는다.
aggregate는 각 rank가 `staged_and_verified`, `not_required_for_rank`,
`not_configured` 중 계획과 일치하는 상태인지 확인한다.

rank별 복제는 명시한 `maximum_stage_bytes`까지만 허용하며 현재 controller 상한은
512 MiB다. 여러 rank에 같은 대용량 자료를 복제하는 일반 자료 배포기가 아니다.
상한을 넘는 원본에는 별도 `shared_read_only` 계약을 사용한다. 이 모드는 최대 8 GiB의
명시 검증 예산 안에서 공유 원본을 실행 전후 transfer plan의 크기/SHA로 검사하고,
필요한 모듈이 배정된 rank에만 `PIPELINE_HPC_RAW_ROOT`를 전달한다. rank workspace에는
복사하지 않으며 manifest/rank receipt에는 절대경로를 넣지 않는다. 누리온 POSIX worker는
공유 root, dataset 디렉터리와 원 파일 모두에 user/group/other 쓰기 비트가 없어야 시작한다.
소유자가 실행 도중 권한을 되돌리는 경우까지 OS sandbox로 차단하는 기능은 아니므로 종료
후 SHA 불변 검사도 반드시 통과해야 한다. 관 마찰 자료는
기존 `copy_to_rank`로 `tests/test_friction_study.py`가 배정된 rank 하나에
4파일·12,340바이트만 배치하도록 batch11 plan에서 고정했다.

717,637,500-byte 누수 다중센서 7z는
`jobs/leak_multisensor_transfer.example.json`의 공급 SHA와
`jobs/nurion_shared_data.example.json`의 공유 계약을 사용한다. 누리온에서 정식 파일명을
`<raw_root>/leak_multisensor_transients/` 아래에 두고 private map의 두 경로만 실제 값으로
바꾼다. `tests/test_leak_multisensor_archive_hpc.py`가 배정된 rank에서 archive 전체
무결성과 80/80/122 센서 재고를 검사한다. 이 계약은 아직 MPI/PBS에서 실행하지 않았다.
targeted 캠페인인데 private map이 없어 `PIPELINE_HPC_RAW_ROOT`가 전달되지 않으면 해당
통합시험은 실패한다. 일반 source-only full-suite에서 원자료 미실행은 통과 주장으로
기록되지 않으며 별도 targeted summary만 실제 archive 검증 증거다.

업로드 파일을 정식 공유 구조로 준비할 때는 `prepare_shared_raw.py`를 사용한다. 도착
파일을 바로 신뢰하지 않고 transfer plan의 바이트 수와 공급 SHA를 복사 중 확인하며,
정식 target이 이미 있으면 덮어쓰지 않는다. 같은 파일시스템의 임시 이름에 쓴 뒤
`os.replace`로 원자 설치하고 POSIX에서 file 0444, dataset/root directory 0555로 봉인한다.
receipt에는 절대경로를 넣지 않는다. 원 업로드 파일은 삭제·이동하지 않는다.
이미 `rsync` 등으로 정식 상대경로에 둔 경우에는 `--source` 대신 `--verify-existing`을
사용한다. 이 경로는 복사하지 않고 모든 plan 파일을 봉인 전후 두 번 검증한 뒤 권한만
읽기 전용으로 바꾼다. 잘못된 기존 파일을 교체하거나 수정하지 않는다.

```bash
mkdir -p /shared/pipeline-raw
python scripts/prepare_shared_raw.py \
  --source /incoming/Dataset.7z \
  --raw-root /shared/pipeline-raw \
  --plan jobs/leak_multisensor_transfer.example.json \
  --maximum-bytes 717637500 \
  --receipt /private/leak-stage-receipt.json

# 또는 이미 정식 경로에 전송한 경우
python scripts/prepare_shared_raw.py \
  --verify-existing \
  --raw-root /shared/pipeline-raw \
  --plan jobs/leak_multisensor_transfer.example.json \
  --maximum-bytes 717637500 \
  --receipt /private/leak-existing-receipt.json
```

targeted 제출은 `jobs/nurion_leak_archive_targeted.pbs`를 사용한다. site queue/account와
자원 옵션은 제출 환경에서 지정하고, `PIPELINE_HPC_PYTHON`, `PIPELINE_HPC_DATA_MAP`, 필요 시
`PIPELINE_HPC_RESULTS`를 설정한다. 템플릿은 1 rank만 실행하며 임의 account ID를 포함하지 않는다.
현재 Windows의 `bash.exe`는 WSL 실행 래퍼여서 비대화식 `bash -n` 증거를 만들지 못했다.
누리온 로그인 노드에서 `bash -n jobs/nurion_leak_archive_targeted.pbs`를 먼저 실행하고 그
결과를 캠페인 receipt와 함께 보존한다.

대용량 archive 캠페인은 관마찰 전체 회귀와 별도 manifest로 만든다. `--only-tests`는
선택 모듈만 `test_file_sha256`과 shard에 넣고 manifest에 `campaign_kind=targeted`와 정확한
선택 목록을 봉인한다. 따라서 이 결과를 전체 suite 통과로 집계할 수 없다.

```bash
python scripts/hpc_tests.py prepare --shards 1 \
  --only-tests tests/test_leak_multisensor_archive_hpc.py \
  --data-map /private/nurion_shared_data.json \
  --manifest /private/manifest-leak-archive.json
```

관마찰 자료를 결합한 full-suite manifest와 위 targeted archive manifest는 각각 실행·집계해
서로 다른 summary/receipt로 보존한다. 어느 한쪽 결과로 다른 캠페인을 통과 처리하지 않는다.

PBS에서는 source map과 별도로 `PIPELINE_HPC_DATA_MAP`에 private data map 경로를 준다.
이 계약은 원자료 재배포 권리를 만들지 않으며, 테스트가 참조하는 바이트를 실행 중
고정하는 기능이다. 실제 MPI/PBS/pytest 실행은 아직 하지 않았다.


## 소유 엔진과 선택적 물리 라이브러리

외부 소유 엔진은 src만 떼어 복사하지 않고 해당 프로젝트의 LICENSE·pyproject와
원 소스 식별자를 확인할 수 있는 검토된 checkout을 준비한다. source map에는 그
checkout의 실제 import 디렉터리를 준다. 각 어댑터의 native metadata가 요구하는
소스·버전·라이선스 배치도 맞춰야 한다. 예시 경로를 그대로 사용하면 안 된다.

수화물 선택 공급자는 DEPOSITION_RISK_KO의 source-build 조건을 따른다.
현재 확인한 PyPI thermopack2.2.3 wheel에는 hydrate API가 없어 단순히 최신 PyPI를
설치하는 것으로 수화물 연동이 준비되지 않는다. 요구하는 upstream 기능·소스 commit·
binary fingerprint를 확인한 별도 빌드를 준비하고, 실행 결과는 아직 미검증으로 취급한다.
이 의존성을 requirements-tested의 통과 환경으로 추가하지 않는다.

수치 실행 없이 수행한 코드/패키지 소스 검사, 데이터 디코딩, 정적 시그니처 생성은
실제 native 물리 계산이나 누리온 회귀의 통과 수에 합산하지 않는다.


## 제어기 교차 정적 검토 후 경계

이번 수정은 정적 source/계약 검토다. pytest·수치·native·MPI·PBS를 실행하지 않았다.
`python -m py_compile`은 source 문법 검사이며 plugin 또는 시험의 실행 검증이 아니다.

### 실제 수집·실행과 JUnit

각 rank는 별도 collection 명령 없이 **한 번의 pytest**를 실행한다. `-p hpc_tests`로
복사한 `scripts/hpc_tests.py`의 hook을 등록한다. 문자열에서 plugin 파일을 생성하지 않으며
동일 Python 파일을 Ruff·컴파일로 검사한다. child PYTHONPATH는 rank의 scripts/src/root와
고정한 owner import roots만 사용한다. 상속 PYTEST_ADDOPTS/PYTEST_PLUGINS는 제거하고
plugin 자동 검색을 끈다. 명시적으로 검토하지 않은 deselection이나 설치 plugin에 의해
시험 일부만 실행되는 것을 통과로 집계하지 않는다. 외부 pytest plugin이 꼭 필요한 시험은
현 controller 계약에서 별도 지원·고정한 뒤 사용해야 한다.

설치된 `_pytest/reports.py`에서 Item.user_properties가 phase report로 복사되는 경로와
`_pytest/junitxml.py`의 finalize에서 그 properties를 testcase XML에 기록하는 경로를
읽어 대조했다. pytest_collection_finish가 실제 selected node IDs와 SHA property를
남기고, pytest_deselected와 pytest_runtest_logreport가 deselection/각 phase outcome을
기록한다. pytest_sessionfinish가 capture.json을 저장한다. hook ordering이 달라도 child
process가 끝난 뒤 capture와 JUnit을 읽으며, 둘 중 하나가 없으면 controller incomplete다.
이 정적 대조는 다른 pytest version에서 실제 재현됐다는 증거가 아니다.

집계는 manifest assignment, 실제 testcase properties, capture의 collected IDs, 각 node의
setup/call/teardown을 대조한다. 누락 phase·미실행 node·deselection·중복 phase·JUnit/capture
outcome 불일치는 controller_incomplete다. 실제 pytest assertion/fixture 실패와 구분한다.
`controller_complete`는 이 기록 경계가 완결됐는지이며 물리 모델의 타당성 flag가 아니다.
collection 단계에서 모듈 자체가 skip되어 개별 node를 알 수 없으면 완전 coverage로
확인할 수 없으므로 controller incomplete다. --allow-skips가 그 누락을 채우지는 않는다.

정상적으로 끝난 exit=1의 실패 사례는 검증된 실제 counts에 포함하고 전체 gate는 실패한다.
원 pytest는 call failure와 teardown error를 같은 node의 두 testcase element로 출력할 수
있다. 이 원 구현도 정적으로 확인해 그 조합만 중복 허용하고 phase capture와 대조한다.
따라서 counts.tests는 실제 JUnit testcase element 수이고 collected_test_nodes가 고유한
실제 collected node 수다. 성공한 기본 no-skip 결과에서는 두 수가 같다. incomplete rank의
불완전/미검증 totals는 합산하지 않으며 그 원 JUnit/log/capture는 rank 폴더에 남긴다.
--allow-skips로 허용한 결과는 passed_with_allowed_skips로 구분한다. 기본 PBS는 허용하지 않는다.

### 소스와 외부 owner의 분리

manifest의 모든 file key는 canonical POSIX relative path와 lowercase SHA256이어야 한다.
생성된 `research/validation_queue.json`은 자체 manifest 경로와 digest를 저장하므로 구성 fingerprint에서 제외한다. 이를 포함하면 큐를 갱신할 때 큐가 가리키는 manifest가 다시 무효화된다. 그 외 허용목록의 설정과 연구 기록은 계속 고정한다.

중복 snapshot category나 중복/비정상 rank assignment를 거부한다. source map은 기존 path
문자열도 받지만 권장 형식은 다음과 같다.

```json
{
  "materials": {
    "project_root": "/REVIEWED/matintegrity",
    "import_root": "src"
  }
}
```

import_root는 project_root 안이어야 한다. 옛 src 문자열은 그 위에 pyproject.toml이
있으면 project 경계도 포함한다. owner Python/JSON, 설정·문서·license/notice, INP와
선택 native library suffix의 파일별 SHA를 고정하고 원 이름/상대 배치를 보존해 복사한다.
.git, venv 계열, artifacts, data, tmp, build/dist, __pycache__와 .env 파일은 제외한다.
owner 전체 checkout을 무제한 복사하지 않으며 owner당 50,000개/512 MiB 상한을 둔다.
raw·학습 데이터·비공개 관측은 이 경로에 끼워 넣지 않고 별도 검토한 data staging으로
관리해야 한다. 선택되지 않은 resource 형식이 필요한 owner는 명시적 자원 계약 확장이
필요하며 해당 모델의 구동 성공을 미리 주장하지 않는다.

rank 소스 copy, 원 source checkout, 격리된 owner copy와 원 owner, 환경 distribution
version 목록을 실행 전후에 대조한다. 바뀌면 source_or_environment_changed이며 pass가
아니다. 환경 목록은 package binary 재현의 완전한 증명이 아니며 실제 solver receipt의
native version/binary pin과 과학적 독립 검증을 대체하지 않는다.

중요한 기존 fixture 한계: test_alarm_exchange/test_phase_change_exchange 등에는
ROOT.parent/tmp/analysis/repos 상대 layout 확인이 있고, test_fabrication_exchange는
원 material checkout의 상위 layout을 사용한다. source_map/PYTHONPATH만으로 그 폴더가
존재한다고 꾸미지 않는다. 기본 no-skip gate에서 실제 누락이 드러나면 각 fixture owner에
검토 source root를 주입하는 public 계약을 요청하거나 필요한 별도 staging을 명시해야 한다.
또한 artifacts의 과거 consequence receipt를 읽는 시험은 원 자료를 별도로 준비해야 한다.
이 controller가 그것을 임의 생성하거나 skip을 성공으로 바꾸지 않는다.

### PBS와 bundle의 마감 경계

PBS 템플릿은 기존 RUN_ROOT를 재사용하지 않는다. 같은 경로가 있으면 MPI 실행 전에
중단하여 예전 rank receipt가 새 실행에 섞이지 않게 한다. 개별 assertion 실패 때문에
다른 rank를 취소하지 않도록 run 자체의 종료와 전체 aggregate gate를 분리하되, PBS의
최종 성공은 MPI launcher와 aggregate가 모두 성공한 경우에만 가능하다.

public bundle은 기존 suffix allowlist에 더해 private/artifacts/raw/processed/cache 경로와
.env를 제외한다. jobs JSON은 *.example.json만 포함하여 실제 비공개 source map을
묶지 않는다. 검토 안 된 임의 JSON의 내용까지 자동으로 공개 적합하다고 판단하지 않는다.
bundle에 실제 들어간 regular member bytes의 SHA를 다시 읽어 pre/post workspace pin과
대조한다. output은 artifacts 또는 checkout 밖에 두며 기존 output을 덮어쓰지 않는다.
성공 receipt가 없는 미완성 archive를 전송/검증 완료본으로 사용하면 안 된다.

새 회귀는 실제 수집 대비 거짓 coverage·숨은 deselection·미실행/teardown 누락·JUnit
phase 불일치, 실제 실패와 이중 failure/error 출력, 허용 skip 상태, rehashed path 공격,
owner license/native pin과 copy 격리, private bundle 제외, child 환경 정화를 다룬다.
모두 작성만 했으며 실행하지 않았다. unsigned manifest/receipt는 서명된 원격 실행
인증이 아니고, 실제 누리온 결과가 생기기 전까지 어떤 통과 수도 새로 합산하지 않는다.

## 원자료 검사 중 디렉터리 identity

`scripts/hpc_data.py check`는 파일 크기·SHA·파일 identity 외에 raw root와 각 기존 하위 디렉터리의 no-follow identity도 검사 시작 시 고정하고 완료 뒤 재확인한다. 검증 중 상위 디렉터리 항목이 추가·삭제되거나 디렉터리가 교체/링크로 바뀌면 파일 자체의 해시가 맞아도 `directory_tree_stable=false`, `verified=false`로 결과를 보류한다. 검사 후 사용 시점까지의 불변성, 저장소의 신뢰성, 재배포 허가를 증명하는 것은 아니다. 해당 경계는 합성 회귀로 작성했으며 실행은 누리온 대기다.

## 2026-10-30 worker-startup metadata scan

The wave25 route-pull and wave26 gas-thermal-transient workers reached their shard stage after the manifest, source archive, external source map, and source-pin checks. At 06:12 KST, both PBS records were `R`; neither had yet copied a rank receipt or JUnit out of node-local `/tmp`. This is an unknown test outcome, not a pass or a model failure.

Source review located a potential pre-test startup cost in `scripts/hpc_tests.py`: `run()` calls `environment()` before saving the rank receipt, and `environment()` queried 27 optional package versions one by one through `importlib.metadata`. On a shared package filesystem, those metadata lookups can block worker startup before any receipt is visible. The current two jobs have immutable controller pins and are left untouched.

For later campaigns, `environment()` now skips that scan by default and records `package_versions_collected: false`; setting `PIPELINE_HPC_RECORD_PACKAGE_VERSIONS=1` opts into the detailed scan. Runtime identity remains captured separately by the PBS artifacts (container, source archive, fixes, and wheel SHA-256 values). New controller SHA-256: `885f42be5a0cfa96006f3ad55047f9e73ad5064d4347363180fb8b9aa02f850b`. This correction is not yet executed on Nurion; only future waves using this exact controller can confirm whether worker startup improves. No local tests were run.

### 2026-10-30 progress diagnosis correction and live-case instrumentation

The interrupted route-pull rank receipt was already `started`, which means its environment snapshot completed; therefore the package metadata scan was not the observed blocker in that attempt. Its partial `pytest.log` contained four completed-case dots. In test order these correspond to three parameterized graph-reference cases and the route-to-planning binder; the next case is the first native MoorPy finite-EA solve. This suggests, but does not prove, that the remaining time was spent in the native solve. Because interruption occurred before JUnit/capture completion, none of those four partial outcomes is used as audited pass evidence.

The runner now supports `PIPELINE_HPC_PROGRESS_PATH` and publishes atomic per-case start/finish JSON records. `PIPELINE_HPC_VERBOSE=1` switches pytest to verbose node names. Wave28 job 24236509 uses this instrumentation with the PyYAML source-map; it is queued. Wave27 24236506 uses the owner source-map to rerun the eight native gas-thermal cases that were skipped previously. No local tests were run.

### 2026-10-30 long-running case recovery and instrumented reruns

The 06:40–06:42 KST poll corrected the earlier “stalled before tests” interpretation. Partial logs recovered by the PBS interruption trap showed that 24236506 had emitted 11 terse pytest progress dots; 24236509 had not completed collection; 24236520 had six named cases complete and was executing `test_independent_sample_support_order[40-55-left-left_to_right]`; 24236522 had fourteen named input/context cases complete and was executing `test_skew_linear_physical_flux_and_native_reactions[0.0]`. The four jobs were interrupted with PBS Exit_status=271 before JUnit/capture finalization. Their partial case outputs remain diagnostic only and add zero audited passes. No physical assertion failure was observed.

To make the next interruption actionable, `scripts/hpc_tests.py` now appends progress events and enables repeat Python stack dumps every 120 seconds during pytest. PBS interruption collection includes the event and traceback files. Controller SHA-256 is `30d80a033702897802b46fa75d98dea368797ac9fac4a67ff2e04cc9c50844cf`. It was transferred through the prescribed wrapper and verified on Nurion. Two corrected-source submissions are running: 24236530 (soil FEM; PBS SHA `ef40ca3017edc1926f1724f490343247d52f71efe078834a788806c2e79f8816`, manifest SHA `d837b4d239fd127c0a61e93eb50a9a51827a6f009e570d553b204d1fc9d7e84f`) and 24236531 (pressure wave; PBS SHA `7c92041cf3afe409e9a896b9c30018644feb09bf257c2fd0b8cc783f695c68dd`, manifest SHA `e8c17dc56ee3f819eca5c4bd702523f548241f092ef43abef65d89bd44f592ba`). Both have 24 source-integrity checks marked OK and identical controller SHA. At the first post-launch poll each was still at `pytest_launching`; no JUnit or physical result exists yet. Verified focused pass total stays 83 and capability qualification stays 0/173. Poll artifact: `artifacts/hpc/poll-20261030-0645KST.json`.

The subsequent stack traces from 24236530 and 24236531 identify a shared import bottleneck rather than a numerical solve: repeated dumps show importlib loading NumPy and SciPy files beneath `/scratch/YOUR_HPC_ACCOUNT/matintegrity-20261029-b42/.venv`. Inspection found that `hpc_tests.py` replaced the PBS-provided `PYTHONPATH` when it launched pytest, which removed its node-local package overlay. The controller now preserves that inherited path. The exact NumPy 2.2.6 and SciPy 1.16.3 wheels were downloaded from the official Python package file host and matched byte-for-byte against the local wheelhouse SHA-256 values (`fd83c0…` and `72d171…`). Their PBS scripts verify each hash, unpack them under node-local `/tmp`, and include the wheel hashes in runtime receipts. Diagnostic jobs 24236530/31 ended Exit 271 before JUnit and are not counted. Corrected jobs 24236550 (FEM) and 24236551 (pressure wave) were queued at 06:54 KST with controller SHA `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`; their physical outcomes are pending. No local tests were run. See `artifacts/hpc/poll-20261030-0654KST.json`.

### 2026-10-30 pinned numeric runtime results and FEM package follow-up

Pressure-wave job 24236551 finished PBS Exit 0. The strict summary records 50 collected nodes, 50 passed, no failures/errors/skips, complete JUnit/capture coverage and unchanged source/environment provenance. Its summary SHA is `9ba5c7efa353f6e22a736a98d0543699bf76035de70acddcd15001eb16902a18`; merged JUnit SHA `3848377fe8d09f21da3b61effc9917fc94d8b3439994c5c9b36c4049a1b8a1ff`. Gas thermal transient job 24236556 also finished Exit 0 with 20/20, zero failures/errors/skips and unchanged provenance; summary SHA `2fb916321ab269da935d80b5c2ed1f74391f3aee8c0f0d1b062e6c24f41420a1`; merged JUnit SHA `c5ca284ef39897cd81cea1b8efc85a4d347b419aab10284a1482cf06e437b77a`. Both strict gates report `engineering_or_field_qualification=false`: these are authored analytic/reference checks, not equipment or field qualification.

The FEM node-local numeric rerun 24236550 still stalled after reaching `_backend`, but its stack had advanced past NumPy/SciPy and was reading scikit-fem package source files through `importlib.metadata` from the shared venv. This second environment-I/O issue is distinct from the initial NumPy path override. A hash-pinned scikit-fem 12.0.2 wheel (`34cd891f80072c0c1eb759a2371e82f0eeec91ff7c08acab96d27b6edbbe1b05`) is now staged node-local; job 24236616 uses it. Route-pull job 24236557 is in the native MoorPy finite-EA reference case. At this poll, neither current job had a complete JUnit. Previous verified focused suite cases rise from 83 to 153, while capability qualification remains 0/173. Poll artifact: `artifacts/hpc/poll-20261030-0702KST.json`.

Correction at 07:05 KST: FEM 24236616 completed PBS Exit 0 with 54/54 passed, no fail/error/skip, complete controller summary, and unchanged provenance. Node-local scikit-fem removed the shared-vendor source hashing delay. Its summary SHA is `fc8653ebef8cb7387e2db52e8800dd9cf86a503e8838fc4b64e6ad9be6293220`; merged JUnit SHA `1258d80e371fe6af7d7bb71dbf8296034239f2800d3aef9ccd367c6f489e7eb4`. Combined with the already completed pressure-wave (50/50) and gas-thermal (20/20) runs, the recorded focused suite case count is 207; full capability qualification remains 0/173. Route-pull job 24236557 remains in its MoorPy finite-EA independent-energy case; no JUnit yet. Poll artifact: `artifacts/hpc/poll-20261030-0705KST.json`.

At 07:17 KST, route-pull job 24236557 had completed with PBS Exit 1 and a complete JUnit: 9 cases, 7 passed, 2 failed, no errors/skips. The two analytic MoorPy finite-EA cases failed because the solver's horizontal and vertical endpoint residuals each met its component-wise tolerance, while the adapter correctly rejected their combined Euclidean displacement residual when it exceeded the declared position tolerance. The adapter now requests half the caller tolerance from MoorPy and applies that tighter bound to each component, leaving the Euclidean endpoint gate unchanged. This is a numerical solver-tolerance correction, not a relaxation of the position acceptance criterion. Corrected source SHA-256: `12155907ff8da1f50a9849473e8ffe5d95b4b7cf2f3fccb7d28ed98a99477b4c`; pinned source-fix archive SHA-256: `2ed7a50b626bae56e6c688b06bb8f8a687a80776866aa94f2e3d3146f0c1ea30`; PBS SHA-256: `6765ace8c897988d0c1c25b96999a3c6fe00ec6342565fb5bb41dd6ba49d888a`. Corrected route-pull wave38 job 24236703 is running on one 40-CPU node with 4 MPI ranks and a 90-minute walltime. Its source checks and manifest preparation passed. At 07:25 KST the first analytic MoorPy case (500 N) had started but had not emitted a completion event; JUnit is not yet available. Focused passing-case total remains 207 and capability qualification remains 0/173. The earlier failures remain failed evidence and are not counted as a pass.

Correction at 07:28 KST: route-pull wave38 job 24236703 completed PBS Exit 0. The strict controller reports 9 collected nodes, 9 passed, no failures/errors/skips; every case setup/call/teardown passed, no deselections occurred, and the rank receipt states `provenance_unchanged=true`. The two finite-EA analytic force/extension/energy cases, vertical translation energy/work case, and endpoint-opening refinement case all passed. Summary SHA `c163a0e4c0fb2329f7776ec176b8cdc111428ee0424c989caaaf5c4276b34263`; merged JUnit SHA `df2c16e16b108acda7360d8446cc943bc1760527c1029c23abd2bea71034be85`; rank JUnit SHA `85b43b8659a33b003f770a2f8858ea6ed575d7dac2b88cb6cc67f7a1d099cf73`; receipt SHA `40649b67df3c6695d9e2d331cb6ea16f85710dd6bdb321b4e71ad8b0d9914ca7`; source-integrity log SHA `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime-files SHA `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`. The environment receipt records Python 3.12.5 and fixed thread limits but does not collect installed package versions (`package_versions_collected=false`); package inventory completeness is therefore a recorded limitation. Controller says engineering/field qualification false. Cumulative focused passing suite cases are 216; capability qualification remains 0/173. Poll artifact: `artifacts/hpc/poll-20261030-0728KST.json`.

At 07:29 KST, submitted wave39 job 24236718 for three independent physical validation modules: `test_thermal_coupling_analytic_validation.py`, `test_soil_moisture_physical_validation.py`, and `test_acoustic_clock_validation.py`. The copied wave38 runner passed `bash -n`; the 3-shard/3-rank substitutions were checked in the submitted PBS script. PBS SHA-256 is `869c958c9b392ae79690d268dc97726a56172b7e2809b252902f6adb92f3f01a`. Job is queued; no manifest, source check, test outcome, or JUnit has been produced yet. Do not count it in passing totals. Poll artifact: `artifacts/hpc/poll-20261030-0729KST.json`.

Correction at 07:30 KST: job 24236718 failed in staging, before tests, because its PBS script checked `$ROOT/wave39/hpc_tests.py` but only wave36 had that controller file. The source archives and their hashes checked successfully; no tests or manifest were produced. Copied the pinned controller (SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`) to wave39 and resubmitted the same 3-module/3-rank campaign as job 24236721. At 07:33 KST the corrected job was running, its source/controller integrity stage had passed, manifest and three rank progress records existed, and all ranks were at `pytest_launching`; no test outcomes were available. The infrastructure failure is not counted as a model failure or pass. Focused passing cases remain 216; qualification remains 0/173. Poll artifact: `artifacts/hpc/poll-20261030-0733KST.json`.

Correction at 07:37 KST: retry 24236721 completed PBS Exit 0. The controller recorded 14 collected nodes across the 3 planned modules, all passed, zero failures/errors/skips, and each rank receipt reports `provenance_unchanged=true`. This includes coupled closed-headspace adiabatic Poisson/ledger checks, constant-temperature soil mesh/timestep refinement, and acoustic clock cases. Summary SHA `2829d4a52974a263a5f18baa0264bb69dc7d50ef82f4799907953fbe4a801c65`; merged JUnit SHA `ddb17dd73338ca699bfda30dab822e91e59fe16fea2a4e031727656b0f8be21d`; rank JUnit SHAs `24d75e61c0da8fba105aac73fd56bd23f0c8a036c87c336d42ad4ffca0524b24`, `20025c46720a7304f2f7ce81a2d108585f1108be39259446980522de28201d2b`, and `7b029dc1464d90205f6aa676c4bc52714e7d8b65fd94aed27d336a2c7b8729c5`; rank receipt SHAs `0b0011be04e0b6c5f4d1a19eba50423d8cfae4d05768801452c46f53adb569ee`, `0a551f0ac4c328c204a4b6cce562a86b28ccf32b74e5461b7b3248071c53188b`, and `d186fb527e7e7e12e705c19709cdbadf93041fb29f34a7f083b55f8f75842b9a`. Source-integrity log SHA is `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime-files SHA is `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`. As in other recent targeted jobs, package version collection is false; runtime file pins/thread limits are recorded. Engineering/field qualification is false. Focused passing suite cases total 230, while capability qualification remains 0/173. Poll artifact: `artifacts/hpc/poll-20261030-0737KST.json`.

At 07:39 KST, submitted wave40 job 24236756 for vapor onset/collapse/rejoining, native dynamic ring/fracture mechanics, and thermal first-law/conservation/refinement tests. Three ranks are requested on one 40-CPU node with 90-minute walltime. PBS script SHA-256 `8b3a1df3aad4392b1e94f1e53d7bc3e7bf54d5f41383d3696c0a8282a18b84d9`; controller SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`. Job is queued; no manifest or test outcomes yet. It is not counted in passing totals. Poll artifact: `artifacts/hpc/poll-20261030-0739KST.json`.

### 2026-10-30 wave40 source-owner diagnosis and corrected rerun

Job 24236756 completed with PBS Exit 1 and controller status `incomplete_or_failed`: 16 collected cases, 14 passed, 2 failed, 0 errors/skips, and all three planned shards finished. Both failures were confined to the native dynamic HEM fracture tests and reported `ModuleNotFoundError: No module named 'lh2dt'`; the same shard's dynamic ring equilibrium test passed, while thermal conservation and vapor-event shards exited 0. Rank receipts all reported `provenance_unchanged=true`. This is a missing external owner source-map entry, not evidence of a failed physical assertion. The campaign contributes no new counted passes pending a clean rerun. Summary SHA `b58c6d0becb8c6cd3a35559e9f0e6cdc33dfda803086cede587f0620fe7f40d9`; manifest SHA `7c73877dbe4992e2b564f77e715477891fabb9008bf0c4be9045e9d7683a8d31`; merged JUnit SHA `2343b7860b5aad63e16c0fe9d8459737cfe26fad015d9374b2b8635e041a2b42`; source-integrity log SHA `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime-files SHA `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`. Environment was Python 3.12.5; installed package versions were not collected; engineering/field qualification remains false.


At 07:51 KST, wave41 rank1 thermal conservation completed 8/8 with pytest exit 0 and `provenance_unchanged=true` (JUnit SHA `36c52194aaaf49842bbb838616e09ac80ebd6d134db8ba539fe62a3b0ee363d5`); rank2 vapor events completed 5/5 with exit 0. Rank0 dynamic mechanics passed its three-grid ring equilibrium case and entered the independent constant-force fracture/energy case. PBS remained R; its complete rank0 JUnit and aggregate were pending. These partial shard passes are recorded, but the 13 outcomes have not been added to the audited cumulative count until the remaining shard completes and the controller aggregate confirms the full campaign. The campaign remains non-qualifying for engineering/field use.

Correction at 07:52 KST: wave41 job 24236770 completed PBS Exit 0 and controller status `passed`: 16/16 tests, all three planned modules/ranks complete, no failures/errors/skips. Dynamic mechanics rank0 passed 3/3, including both native HEM fracture cases that had failed to import in wave40; thermal rank1 passed 8/8 and vapor rank2 passed 5/5. Every rank receipt reports `provenance_unchanged=true`; each JUnit SHA matches its receipt. Summary SHA `98a8577f7d2aec8d6fe6c8330253d6fe8818a541d20323e437ab2825c5e15364`; merged JUnit SHA `69aa8424744b6161f85fbb4763ddffe4ef1962fdd06296e9be08a8fab20041e3`; rank receipt SHA-256 values `ec38299d25c88766393be441bb42d3a7964bb82617defb26b6a63a28ff746568`, `66bfc4c48e7aec537ac7ce52ea5c28f5b7f2470c410cff7d5f80fcf2fd5760af`, `cb076fc7bf3c419540e2fe458a7854dffe956fd0a4b521e18c1064c3c851e985`; source-integrity log SHA `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime-files SHA `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`. Python 3.12.5 and fixed thread limits are recorded; package version enumeration remains disabled and `engineering_or_field_qualification=false`. This adds 16 focused audited pass outcomes, bringing the tracked total to 246; the 173-capability qualification gate remains 0/173. Evidence: `artifacts/hpc/poll-20261030-0748KST.json` and `artifacts/hpc/verification-progress.json`.

At 07:53 KST, submitted wave42 job 24236778 with three more independent-reference suites: batch settling, native contact/component mechanics, and rotating-shaft plus inventory-transfer conservation. It uses the same pinned controller, three shards on one 40-CPU node, and a 90-minute walltime. PBS SHA-256 `2252af08ee66f719a4fb26c0bb80c427a470b4e71e06cd493fad0aaccf56bbf7`; controller SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`. PBS reported Q on the first poll; no manifest or test outcomes exist yet. Do not count it until complete receipts and aggregate are verified.

Correction at 07:57 KST: wave42 started on cpu0134. Source checks and manifest completed; rank0 began dilute Stokes settling grid-invariance, rank1 began finite-gap cantilever contact, and rank2 exited its transport-conservation pytest process successfully. Rank2 JUnit/receipt and other rank outcomes are still pending, so no cases are counted yet. In parallel, submitted wave43 job 24236782 for hydrate nonlinear physics, compressor/cavity dynamics, and fiber-metrology references (three shards, 40 CPUs, 90-minute walltime). PBS SHA `1f4d7caae2e3a9e9e09dfb4eb576d81e663c6b649aa45c017d11accb389c80b9`; controller SHA `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`. Wave43 queued behind currently allocated resources; no test results exist. Cumulative focused audited passes remain 246 and capability qualification remains 0/173.

At 07:58 KST, wave42 rank0 batch-settling completed 5/5 and rank2 transport-conservation completed 6/6, both with exit 0 and unchanged provenance. Their JUnit/receipt hashes are recorded in the poll ledger. Rank1 contact mechanics remains in its native solve and has not produced JUnit/receipt, so the full campaign is not yet countable. Wave43 has since started on cpu0066: source checks passed, its manifest was created, and all three ranks entered `pytest_launching`; outcomes remain unknown. No new aggregate pass count is claimed.

Correction at 08:00 KST: wave42 controller aggregate passed with 18/18 tests, 0 failures/errors/skips, all three ranks finished, and every provenance receipt unchanged. Rank counts were settling 5/5, contact/component mechanics 7/7, and transport conservation 6/6. Summary SHA `5276617918e2a5df567bf133a733b6eed0e5c47b149a990da8cfcb8dd87786b6`; merged JUnit SHA `3e274c7231f6a03084e8e8c978312ad5c586ef024d67480e0bb7191185e516e0`; manifest-file SHA `84a422766984d398b69a3f6c78bf0f2e81a7f09e1b9419bd55438ea471a20161`. Rank JUnit/receipt hashes match. Add 18 to the focused audited count (246→264). The summary still explicitly says engineering/field qualification false.

Wave43 job 24236782 completed all three modules with 15 nodes: 13 passed, 2 failed, 0 errors/skips. Both failures are the compressor's native backend trying to import `lh2dt` from `native_backend_metadata`; source-owner mapping was absent. Rank0's other four gas-cavity cases passed, rank1 metrology and rank2 hydrate nonlinear cases passed; all three receipts report provenance unchanged. This is environment-only failure evidence and the wave is not counted while a full corrected campaign is pending. Summary SHA `115ad1abfaf897d5a283c8f6b74fdd9393a01ce2a56fa9ca2537e718e1321926`; merged JUnit SHA `cfd1a979518ed5cf7fb6e7022c9dbb01755b326297278decdeb1a851aeddb76b`; source integrity SHA `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime files SHA `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`.

To correct the owner map, wave44 job 24236790 was submitted for the same hydrate, compressor/cavity, and fiber metrology modules, now merging the pinned `lh2dt_models` tree with the PyYAML mapping. PBS script SHA `9cc6a0ebf3f93c159588b44f74363e611f2ff517ca106a070783133bb7f6828e`; controller SHA `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`. At 08:02 KST it was R on cpu0134, with result artifacts still being staged. No new cases are counted until full aggregate and receipt verification. Evidence: `artifacts/hpc/poll-20261030-0802KST.json`.

At 08:04 KST, wave44 source integrity passed; its frozen manifest was written (SHA-256 `2d5103c7b2e9723f4d2f0fd07b33ed1853530bfe84ff841b8c73577a4ced2f3c`) and all three ranks reached `pytest_launching`. The rerun is now testing the owner-map correction; no physical results have yet been observed.

Correction at 08:07 KST: wave44 job 24236790 completed PBS Exit 0 with summary `passed`: 15/15 tests, zero failures/errors/skips, all three modules complete. Compressor/cavity rank0 passed 6/6 including both native compressor coastdown cases; fiber metrology rank1 passed 7/7; nonlinear hydrate rank2 passed 2/2. All receipts are finished, JUnit SHA values match receipts, and provenance remained unchanged. Summary SHA `0fee38adf58c3623801c668408d4a40394a8de51f426cd7e85f61bb317851955`; merged JUnit SHA `28958befdb5a37313cfd25b8c0d335eabd65c0b77365b54ede802e5c6cf7e2d4`; manifest SHA `2d5103c7b2e9723f4d2f0fd07b33ed1853530bfe84ff841b8c73577a4ced2f3c`; rank receipt hashes `838440e92082f67b85014e945d7b96e6378894954927cea47b37e79f8a93ee4f`, `de9723e8b4aaa228869fa7d1931a9936184f9e46508f524ac6a3b9f29618ece1`, `1df4a058fab43d5c41a0176a768a6a0d486cfa996d17857a029882b650e0684d`; source integrity SHA `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`; runtime files SHA `85856d4e97c80fa30a56f59285939d382cc9d445fb6c3e8cd1d8d73cf9045255`. This adds 15 focused audited cases, tracked total 279. Engineering/field qualification remains false and full capability qualification remains 0/173. Detailed poll: `artifacts/hpc/poll-20261030-0808KST.json`.

### 2026-10-30 project-wide suite campaign

To expand verification beyond the focused physical suites, wave45 job 24236795 was prepared from the pinned runner for the complete current test corpus: 169 test modules partitioned across 16 ranks on one 40-CPU node, with a 4-hour walltime. The run includes PyYAML and `lh2dt_models` source-owner maps and stages the hash-pinned NumPy 2.2.6, SciPy 1.16.3, scikit-fem 12.0.2, scikit-learn 1.7.2, and PyYAML 6.0.3 wheels under node-local Python paths. The scikit-fem wheel's verified SHA-256 is `34cd891f80072c0c1eb759a2371e82f0eeec91ff7c08acab96d27b6edbbe1b05`. PBS script SHA `9fae32b27c5edd14a7811a09e216c4ad35662fcef3ad84ba8ff1612036c24a8c`; runner SHA `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`. At 08:08 KST, PBS state was Q. At 08:11 KST, it was R on cpu0134, source integrity had passed, and manifest preparation was active. By 08:12 KST, the 86,175-byte manifest was created and all 16 worker progress records reported `pytest_launching`; no JUnit outcomes were yet available. This run is not counted until the full controller aggregate, receipts, source provenance, and JUnit hashes are verified. Existing targeted count remains 279; capability/field qualification remains false.

## 2026-10-30 later Nurion campaigns and current handoff

The local evidence ledger records the following completed focused campaigns. These are test-case results, not capability qualification. Detailed manifests, rank evidence, receipts, and hashes remain in `artifacts/hpc/verification-progress.json`.

| Campaign | Recorded result | Evidence limit |
|---|---:|---|
| Wave49 `24236850.pbs` fatigue, corrosion/wear, fracture arrest | 125/125; 3 ranks; no failures/errors/skips; rank JUnit hashes match receipts and source provenance stayed unchanged | Targeted regression evidence only |
| Wave50 `24236854.pbs` buried thermal, heated transport, pump transient | 63/63; no failures/errors/skips; rank provenance and JUnit receipt checks passed | Targeted regression evidence only |
| Wave51 `24236855.pbs` pipe/soil transient, pressure wave, valve transient | 100/100; no failures/errors/skips; JUnit and provenance checks passed | Targeted regression evidence only |
| Wave52 `24236858.pbs` lifting, pig motion, gas network | 88/88; no failures/errors/skips; JUnit and provenance checks passed | Targeted regression evidence only |
| Wave53 `24236879.pbs` tanks, slurry, water quality | 62/62 reported; PBS exit 0 | Raw JUnit and rank receipt files were not found in the inspected scratch folder |
| Wave54 `24236951.pbs` gas thermal, meter, tank breathing | 74 passed, 9 skipped, no assertion failures/errors | Strict no-skip gate rejected this run; skips were owner-map gaps |
| Wave55 `24236967.pbs` corrected owner-map attempt | Zero cases executed | Rank-size mismatch before tests; infrastructure/controller failure |
| Wave56 `24236970.pbs` corrected owner map and rank size | 83/83 reported; no failures/errors/skips; PBS exit 0 | Raw JUnit and rank receipt files were not found in the inspected scratch folder |

Across these eight records, 521 cases are reported passing in accepted completed runs; 376 have explicit rank JUnit/receipt hash and provenance confirmation in the local ledger, while 145 cases from waves53/56 lack raw rank evidence in the inspected location. Wave54's 74 passes are not counted as a clean campaign because 9 cases were skipped; wave55 executed no tests. None changes the formal capability gate, which remains 0/173 in the capability-ID scope. A separate 174-entry calculation/catalog scope is recorded elsewhere; the mapping between those scopes is not yet reconciled.

The latest whole-suite record is wave46 `24236825.pbs`: 169 modules, 3,427 JUnit nodes, 3,408 passed, 0 failures/errors, 19 skipped. Rank receipts, source provenance, and JUnit hashes matched; the strict no-skip gate returned exit 1. This is not a physical qualification result.

At 15:07 KST, read-only polling of both Nurion and Hangang CPU through the approved shared wrappers returned “No usable shared connection.” Queue/job state could not be refreshed, and no authentication was attempted. The local Hangang staging archive is prepared, but remote upload/submission is still pending an available shared connection. Until a fresh poll succeeds, treat the recorded 10:49 KST job states as the latest available evidence, not current scheduler state.


### 2026-10-30 19:27 KST — Nurion evidence refresh and Hangang handoff

The approved existing Nurion wrapper responded at `login04` as `YOUR_HPC_ACCOUNT`. Read-only `qstat -x -f` confirmed six project campaigns are terminal: 24236703 (9/9), 24236721 (14/14), 24236836 (1/1), 24236825 (full suite), 24236879 (62/62), and 24236970 (83/83). No new job was submitted during this refresh.

The full 16-shard suite 24236825 reports 3,427 JUnit cases: 3,408 passed, zero failures, zero errors, and 19 skipped. PBS Exit 1 is the configured strict no-skip gate. Eighteen skips are optional retained spatial-risk consequence fixtures; the remaining Potash source test passed separately in 24236836. The broad suite pass rate is 99.45%; it is regression execution evidence, not a physical qualification rate.

A runtime candidate crosswalk joins the 38 recent capability groups' static test candidates to JUnit and test-file hashes across six preserved campaigns. All 235 candidate references match the current workspace test-file hash to at least one HPC manifest, and all matched JUnit cases passed. Thirty-two candidate hashes in the older static audit differ from current files; the current files do match the executed manifests. The crosswalk does not establish that every capability has all four independent physical gates.

Remaining scope is material: 60 APIs span 38 recent capability groups; 22 APIs have source test candidates and 38 have none. Static source categories still lack independent-reference candidates for 48 APIs, conservation/balance for 49, convergence/partition for 53, and event/regime for 47. These gaps can overlap. Formal physical qualification therefore remains **0/173 capabilities**; the separate 174-entry catalog scope is still unreconciled. Package versions were not fully collected for the broad suite, and no equipment or field qualification is claimed.

Machine-readable evidence: `artifacts/hpc/poll-20261030-1927KST.json`, `artifacts/hpc/runtime-candidate-crosswalk-20261030-1927KST.json`, and `artifacts/hpc/verification-progress.json`. The local Nurion project and results archives are now transferred and SHA-256 verified as recorded in `../output/nurion-session-backup-20261030/backup-notes.md`. Since the user has transitioned new campaigns to Hangang, any newly prepared execution should use the existing Hangang CPU shared connection; this refresh did not submit a job.

## Hangang focused physical validation checkpoint — 2026-10-30

Hangang CPU job `1184309` completed successfully. The six-shard campaign executed six selected gas potential, thermal transient, batch transport, conservation, coupling, and transient test modules: 59 test cases passed, with zero failures, errors, or skips. The analytic gas-potential suite now covers one-, four-, and sixteen-cell discretizations against an independently calculated laminar pressure reference and energy/convergence checks.

Evidence is on Hangang at `/scratch/YOUR_HPC_ACCOUNT/pipeline-integrity-validation-hangang-20261030/runs/1184309`. The source archive SHA-256 is `7ce9a50844d16f53ce0f3ab14b99e738e2a335d32573794a4db1e15e91d96d6d`; manifest digest is `b09b443ef1f3fa63b833dbd79f420ec1f93e6032afa7742e76dbbc05f9f63745`; summary SHA-256 is `77049b2afc839bf159cab7b785fd9d55eb8d435774db347f3ad99472ac4f1fcd`; merged JUnit SHA-256 is `47c9695ad58c8b7f8a15ff0f352e2f87ffc0fe88deaead55ba0f94b6b2cdd18f`. Runtime was Python 3.12.12; the job's package freeze is recorded remotely and hashed in `artifacts/hpc/verification-progress.json`.

This passing focused campaign adds evidence for the tested interfaces only. It does not qualify the full model catalog or replace experimental/field validation. Formal physical capability qualification remains 0/173.

## Scope audit correction and gas-plenum validation addition — 2026-10-30

A scope reconciliation against the current capability catalog found 203 unique entries: 174 calculation capabilities and 29 procedure-required entries. The catalog has 37 `implemented`, 99 `interface_verified`, 38 `partial`, and 29 `procedure_required` entries. The validation progress record had a stale denominator of 173; it is now aligned to 174. The evidence scan found 164 test references in the catalog and no missing referenced test paths. This does not indicate that those tests provide physical qualification.

Formal physical qualification remains 0/174 (0%). Existing HPC passes are solver/regression evidence and are kept separate from qualification. A focused test module now covers the previously unresolved static-audit call reference for `evaluate_gas_potential_plenums`: independently calculated signed elevation work, reverse flow, segmentation, mass/energy closure and deterministic receipt replay. Three new numerical cases are scheduled in the seven-shard Hangang CPU campaign. No local test execution was performed.

## Gas-plenum analytic validation result — 2026-10-30

Hangang CPU job `1185036` completed all seven planned modules across seven ranks. All 65 JUnit cases passed with no failures, errors, or skips. The new gas-plenum module contributed three independent cases covering signed elevation work, both pressure-boundary flow directions, one/four-segment partitions, static energy/mass closure and receipt replay. Runtime was Python 3.12.12. Per-rank receipts, captures, JUnit, merged JUnit, manifest, and the package freeze are retained in `artifacts/hpc/hangang-1185036-evidence.tar.gz`; the remote result directory is `/scratch/YOUR_HPC_ACCOUNT/pipeline-integrity-validation-hangang-20261030/runs/1185036`.

The campaign used a base source archive SHA-256 `645c5170f922de1cbb10c8bcac4f3b7cea1ec0f773fbb75c3d0f73636f82147c` plus a test-only delta archive SHA-256 `b0cdeb8b5bd955ad0d456b0925f145fa942f8eab241fbbac433caf3c952b4cbc`. The effective test source SHA-256 is `9beb53a4b0a831a23ca24ee802b036fc1044cb1387e4253e6b68aa15f2395b57`; the manifest digest is `315323129eb240d8e95a62dab2661943685f7435f3083b5280a18f3a535932f6`. Source archive and delta each have separate in-job checksum records.

Earlier attempts were classified and retained: `1185007` executed zero tests because the script requested six ranks against a seven-rank manifest; `1185027` executed zero tests because its local document digest differed from the archived source snapshot; `1185014` ran 65 cases but the three new plenum cases used a pressure span outside the solver's local domain. Reducing the authored source boundary from 1.10 MPa to 1.03 MPa retained the 1.00 MPa sink, brought the cases into the declared calculation domain, and all three passed in `1185036`. These results verify bounded numerical behavior only. Formal physical capability qualification remains 0/174.
