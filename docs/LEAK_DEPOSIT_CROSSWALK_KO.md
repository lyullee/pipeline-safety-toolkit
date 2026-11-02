# 유사 누수 시험 데이터 등록본의 내용 대조

## 목적과 출처

[Mendeley V1, DOI 10.17632/xw44wv2g88.1](https://data.mendeley.com/datasets/xw44wv2g88/1)와 [Mendeley V1, DOI 10.17632/tbrnp6vrnj.1](https://data.mendeley.com/datasets/tbrnp6vrnj/1)은 같은 저자와 같은 280개 시험 신호 구성을 설명한다. 두 출처는 CC BY 4.0을 표시한다. 관측을 두 번 반영하지 않도록, 파일명·크기 일치와 실제 센서 파일 바이트 일치를 분리해 기록한다.

- `xw44wv2g88.1`: 7z 717,637,500바이트, 공급자 게시 SHA-256 `c6d4a68901a145155a58ccb691769753cb7c0960416a375acac12128bedfd881`.
- `tbrnp6vrnj.1`: Mendeley 공개 API 집계 ZIP 1,080,066,320바이트. 로컬 SHA-256 `a8954b3cec0a0773a956177fed388fb891b25ef2cdf2d264b4b9557cf2a271a1`은 취득 파일을 고정하기 위한 값이며 공급자가 제공한 체크섬은 아니다.
- 집계 ZIP의 3개 중첩 센서 ZIP에는 가속도계 80개, 동압 80개, 하이드로폰 RAW 122개가 있다. 게시자 변환 Python 파일은 신원만 기록하고 실행하거나 배포하지 않는다.
- 두 등록본은 센서별 80/80/122개 파일의 상대 경로와 압축 해제 바이트 수가 모두 일치한다. 이 메타데이터만으로 파일 내용 동일성이나 실험 독립성을 판단하지 않는다.

## 재현 API

`pipeline_integrity.leak_deposit_crosswalk.compare_leak_testbed_deposits`는 양쪽 archive의 source pin과 도구 무결성을 확인하고 282개 원 파일에 대해 경로·길이·SHA-256을 비교한다. ZIP은 정규 파일만 허용하고, traversal·case collision·암호화·알 수 없는 자산을 거부한다. 중첩 ZIP은 임시 seekable 파일로만 열며 압축 해제 경로를 만들지 않는다. 외부 ZIP에서 중첩 ZIP으로 복사하는 동안 선언 크기와 600 MB 예산을 강제하고, 중첩 아카이브의 각 관측 멤버도 선언 크기와 128 MiB 예산을 초과하면 중단한다. 7z는 기존 `LeakMultisensorArchiveSession`으로 allowlist를 읽고 멤버별 별도 추출 프로세스를 사용한다. 각 파일은 임시 scratch 파일에 최대 1 MiB 블록으로 기록·해시한 뒤 즉시 폐기하며, 단일 파일 scratch 한도는 2 GB다. `batch_size`는 진행 확인과 묶음 경계에만 쓰며 멤버 출력을 합치지 않는다. 따라서 이 처리는 파일 내용의 무결성 경계를 보존하지만 여러 파일을 한 번의 압축해제 호출로 최적화하지는 않는다.

```powershell
python scripts/compare_leak_dataset_deposits.py `
  --raw-root data/raw `
  --output research/leak_dataset_crosswalk_results.json
```

상세 실행은 센서 기록 약 3.9 GB씩 두 등록본을 읽는 대용량 archive 대조이므로 누리온 CPU 작업으로 준비한다. [실행 템플릿](../jobs/run_leak_deposit_crosswalk.example.sh)을 제공하며 PBS 작업은 아직 제출하지 않았다. 이후 결과 레코드는 경로별 크기·SHA-256과 전체 digest만 포함하며 파형/압력 관측값을 복사하지 않는다. `byte_identical_member_set`은 두 공개 등록본의 디지털 센서 파일이 일치한다는 뜻이고, 독립된 실험·시행을 의미하지 않는다. 서로 다르면 별도 과학적 모집단이라는 결론도 추가 근거 없이는 내리지 않는다.

연구 crosswalk·패키지 catalog·취득 inventory가 출처 URL, 라이선스, asset 이름, 바이트, SHA-256 및 체크섬 출처에서 일치하는지는 다음 정적 감사기로 재검사한다. 이 도구는 signal archive를 열지 않으며 데이터의 내용 동일성을 판정하지 않는다.

```powershell
python scripts/audit_leak_dataset_crosswalk.py
```

요약 receipt는 `research/leak_crosswalk_integrity_status.json`에 기록한다. `--root`로 다른 checkout을 지정하면 catalog·inventory·crosswalk와 기본 receipt 경로가 모두 그 checkout을 기준으로 해석된다. `supplier_checksum_verified`와 `checksum_origin`을 분리해 사용자가 제공한 aggregate ZIP의 로컬 pin을 공급자 checksum으로 승격하지 않는다.
Crosswalk의 `expected_dataset_ids`는 감사를 받아야 하는 자료 ID 집합이며 감사기 코드에도 현재 기준 집합을 별도로 고정한다. crosswalk의 기대 ID 선언, 실제 record 집합, 코드 기준이 서로 다르면 메타데이터 행이 모두 일치해도 감사는 실패한다. 새 자료를 추가할 때는 crosswalk와 감사기의 검토 기준을 함께 갱신한다.

값 단위, 계측 교정, 공통 시계, trigger, 누수 시작 정답, 하이드로폰 RAW 해석 및 현장 정확도는 이 crosswalk의 대상이 아니다. 기존 `leak_multisensor_data`의 제한된 reader/adapter 계약을 유지하며 RAW 변환 코드를 import하지 않는다.
