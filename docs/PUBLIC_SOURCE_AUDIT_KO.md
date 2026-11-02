# 공개 소스와 연구 인수인계 정적 감사

`scripts/audit_public_sources.py`는 현재 source payload에서 공개 전 검토할 후보를 찾는다.
기존 `audit_release.py`의 wheel/sdist member 검사와 archive/workspace 일치 검사를 대신하지 않는다.
라이브러리·물리 계산·pytest·MPI/PBS를 실행하지 않으며 비공개 참고자료를 읽거나 원문과 비교하지 않는다.
감사 코드와 회귀를 작성했고 회귀 시험은 누리온 실행 대기다.

```bash
python scripts/audit_public_sources.py --output artifacts/public_source_audit/current.json
```

이 명령은 file text/hash와 정적 metadata만 검사한다. `--root`는 검사할 checkout을 지정한다.
대상 root의 Python을 실행하지 않고, auditor와 함께 있는 검토된 `hpc_tests.py`의 public payload
허용목록만 재사용한다. mutable 결과는 artifacts 또는 checkout 밖에 둔다.

## 검사하는 것

- HPC 공개 파일 허용목록과 현재 단순 MANIFEST.in directive의 투영을 합쳐 검사한다.
  두 경계의 차이를 표시하고 private/raw/관측·archive·외부 repository·로컬 job config 후보를 찾는다.
- credential 형태와 로컬 사용자 경로, 파일 URI, 제한된 로컬 자료 경로로 보이는 참고를 찾는다.
  실제 값이나 원문을 출력하지 않고 source relative path·line·rule·후보 SHA만 기록한다.
- 다른 확장자로 위장한 archive/document/executable magic, 장문의 무귀속 blockquote,
  vendored implementation 형태를 후보로 표시한다. 후보라고 자동 삭제하지 않는다.
- LICENSE와 dependency notices의 존재, catalog의 출처/귀속/버전/권리/origin/release-boundary
  필드, validation queue의 이전 실행 적용범위와 현재 검증 유보 정책을 확인한다.
- 로컬 Markdown 링크의 파일 존재와 source root 경계를 확인한다. anchor 의미나 HTTP URL의
  현재 접속·저작권·내용을 검사하지 않는다.
  fenced/inline code는 링크 문법에서 제외하여 배열 접근 뒤 함수 호출을 문서 링크로 오인하지 않는다.

NASA·Zenodo 등 외부 1차 출처 링크는 공개 URL이라는 이유만으로 라이선스를 추정하지 않으며, 별도의 권리 검토와 귀속 정보를 보존한다.

## 결과의 한계

passed는 선언한 자동 boundary 검사에서 blocking pattern이 없고 byte scan이 끝났다는 뜻이다.
review_candidates는 사람이 확인해야 하며 passed와 별개다. source 전체가 저작권 문제 없이
배포 가능하다는 보증이 아니다. `copyright_clearance_confirmed=False`,
`verbatim_original_comparison_performed=False`를 항상 유지한다. 원문과의 대조, 이미지 OCR·
그림의 저작/출처 판별, 표/계수의 이용권 판정, 숨겨진 secret 전수 탐지는 수행하지 않는다.
PNG는 header/magic과 hash만 검사하고 원본 이미지의 저작권을 인증하지 않는다.

제한된 문서 확장자 파일은 이름만으로 공개 payload 경계를 표시하고 내용 분석에 포함하지 않는다. 이미 public source로
선택된 파일은 bounded byte scan을 하므로 확장자가 잘못 붙은 자료의 magic 후보를 찾을 수 있다.
비공개 원자료·외부 checkout·private artifacts를 새로 읽어 비교하는 기능은 없다.

MANIFEST 투영은 현재 사용한 include/recursive-include/exclude/global-exclude/prune만 해석한다.
setuptools의 모든 기본 수집·plugin·빌드 동작을 재현하지 않으며 unsupported directive는 검토 대상으로
남긴다. 실제 wheel/sdist는 별도 built-artifact audit와 workspace byte consistency 검사가 필요하다.
허용 목록이 있어도 그 파일 안의 모든 연구 자료가 공개 적합하다는 뜻은 아니다.

기본 한도는 파일 50,000개, 파일당 16 MB, 합계 512 MB다. 예산을 넘으면 부분 text를 채택하지 않고
scan_complete=False/검사 실패로 기록한다. finding에는 비밀·로컬 경로 원문을 넣지 않는다.
조사 기록의 hash는 unsigned 식별자이며 공격에 대한 인증·과거 실행 증명이 아니다.

## 인수인계와 검증

이전 전체 회귀 결과는 그 당시 source snapshot의 실행 증거다. 새 정적 감사가 통과해도 현재
source에 대한 수치·현장 검증으로 승격되지 않는다. validation queue와 원 실행 receipt를 함께 읽는다.
새 회귀는 허가된 외부 1차 출처 링크 보존, private path/credential redaction, MANIFEST/HPC 차이,
제한된 문서 비열람, disguised archive, broken link, budget, 이전 실행/현재 유보 의미를 다룬다.
회귀 시험을 실행하거나 통과했다고 기록하지 않았다.


PHMSA Flagged Files는 보고 기준과 추세 변환을 포함한 공식 incident 자료다
([PHMSA 자료 안내](https://www.phmsa.dot.gov/data-and-statistics/pipeline/pipeline-incident-flagged-files)).
카탈로그 metadata는 public-access analysis only이며 별도 재배포 license가 확인되지 않았다.
따라서 acquisition은 `metadata_only`로 설정하고, 원본 취득·pointwise 재배포 권한을
추정하지 않는다. 기존 기록의 HTTP 403으로 원 archive도 로컬에 없다. 권리 근거가 확보되기
전까지 이 정책을 유지한다.
