# 새 PC·새 세션에서 연구를 재개하는 절차

## 1. 현재 기준을 읽기

1. README와 `CHANGELOG.md`에서 공개 버전 및 알려진 한계를 확인한다.
2. `research/public_validation_status.json`과 `research/physical_verification_223.json`의 완료 수와 적용 영역을 확인한다.
3. `docs/PHYSICAL_VERIFICATION_BACKLOG_KO.md`에서 미완료 모델의 우선순위·예상 작업시간·필요 근거를 확인한다.
4. `docs/DEVELOPMENT_HISTORY_KO.md`와 날짜가 붙은 STATUS/PLAN/CONTINUATION 기록을 읽는다.
5. 기존 실측 실패4건과 PR 자동P–T 제한을 별도로 유지한다.

공개0.1.0a1의 범위는223개 공학 방법,339개 변형 매핑,304개 API다. 제한 영역 독립 물리 프로필은198개이고25개는 미완료다. 구현률, 대표 실행시험, 물리 프로필, 경험계수 보정과 현장 검증을 같은 수치로 합치지 않는다.

## 2. 환경과 소스 준비

```bash
git clone https://github.com/lyullee/pipeline-safety-toolkit.git
cd pipeline-safety-toolkit
python -m venv .venv
# 사용 중인 OS에 맞게 환경 활성화
python -m pip install ".[engineering,thermo,structural,soil-fem,dev]"
```

검토할 모델에 따라 다른 extra와 외부 소유 엔진이 필요할 수 있다. `docs/GITHUB_INTEGRATION_MAP_KO.md`의 경계를 확인한다. 해당 소스를 실제 import 가능한 위치에 준비하고 버전·라이선스·체크섬을 기록한다. 외부 엔진이 없으면 환경 누락으로 기록하며 모델 실패로 집계하지 않는다.

물리 캠페인에서 필요한 외부 소스는 환경변수로 지정할 수 있다.

```text
PIPELINE_FORMULA_MATERIAL_SRC=<고정된 matintegrity/src 경로>
PIPELINE_FORMULA_LH2DT_SRC=<고정된 lh2dt_models/src 경로>
```

PowerShell에서는 `$env:변수명 = '실제 경로'`, POSIX shell에서는 `export 변수명='실제 경로'` 형태를 사용한다. 공유 작업 디렉터리가 다른 작업으로 수정될 수 있다면 검토된 별도 복사본을 고정한다. 환경 변수 값의 줄바꿈·인코딩과 실제 import 경로도 확인한다.

개인 PC의 체크아웃과 데이터가 포함되어 있지 않으므로 GitHub만 복제했다고 전체 연구 시험이 즉시 통과하지는 않는다. 공개 소스에는 재현 절차·실제 시험과 조건을 포함하고, 별도 준비가 필요한 자료를 명시한다.

## 3. 기본 공개 시험부터 확인

```bash
python scripts/run_public_checks.py
python scripts/run_examples.py
```

17개 파일276건의 부분집합과 예제 실행을 확인한다. 이 결과를 전체 물리 자격률로 바꾸지 않는다. 처음에는 import, 기본 단위, 레지스트리, optional dependency 경계부터 좁혀 확인한다.

## 4. 독립 물리시험 재실행

```bash
python scripts/run_physical_campaign_223.py
```

기본 실행은 `scripts/run_physical_campaign_223.py`와 `scripts/physical_profile_extensions.json`에 선언한 검토 프로필을 실행한다. 프로필별 scope/criteria/selectors를 먼저 읽고, 실제 API를 호출하는지 확인한다. 입력검사나 같은 계산식 복사만 있는 시험을 새 물리 프로필에 등록하지 않는다.

출력은 `artifacts/physical-campaign-223/<시각>/`에 저장된다. 각 모델의 JUnit, 로그, 환경, 실행 전후 소스 체크섬과 `receipt.json`이 있다. `latest.json`은 마지막 실행을 가리키는 포인터이며 그 실행이 통과했음을 보장하지 않는다.

## 5. 이전 증거를 재사용하는 경우

```bash
python scripts/continue_physical_campaign.py
```

이 명령은 기존 로컬 receipt와 고정 외부 소스가 실제로 남아 있을 때만 유효하다. 새 clone에서 개인 경로를 추측해 채우지 않는다. 이전 결과가 없으면 전체 캠페인을 새로 실행한다.

재사용에는 생산 소스와 예제, 선택된 시험 파일, Python 및 관련 패키지 버전, 원 receipt와 JUnit/로그의 해시가 일치해야 한다. 변경된 프로필이나 실패한 모델은 다시 실행한다. 환경이나 공통 코드가 바뀌면 영향을 받는 이전 증거를 버전별로 보존하고 새 캠페인을 수행한다.

## 6. 실패 분류와 수정

| 분류 | 예 | 처리 |
|---|---|---|
| 코드 | 수지 불일치, 잘못된 부호, 잘못된 root/사건 처리 | 최소 실패 사례를 보존하고 구현 수정 후 관련 기준 재실행 |
| 기준·조건 | 독립식의 단위 오류, 적용 범위 불일치, 공간/시간 해상도 부족 | 근거를 설명하고 기준/해상도 보완; 원 실패 기록 유지 |
| 환경 | 선택 라이브러리 없음, import 소스 변경, 인코딩 경로 오류 | 환경을 고정하고 같은 기준으로 재실행 |
| 자료 | 물성·형상·시계·라이선스 부족 | 임의 값을 넣지 않고 미완료 상태 유지 |
| 실측 적합성 | 조건이 맞는 실측과 정한 기준에서 불일치 | 원 결과를 유지하고 원인·보정·독립 비교를 분리 |

실패를 지우거나 허용오차를 넓혀 숫자만 올리지 않는다. 수렴 부족이면 해상도를 높여 원래 기준에 도달하는지 확인한다. 수정 전후의 입력·출력·소스·환경을 비교한다.

## 7. 새 비교자료를 확보하는 순서

1. 모델의 물리 출력과 적용 영역에 맞는 실제 측정 또는 독립 고충실도 기준을 찾는다.
2. 출처·버전·라이선스·파일 크기·체크섬을 기록하고 원본을 별도 보존한다.
3. geometry, 물성, 단위, 경계, 계측 위치와 시간 정의를 점검한다.
4. 보정과 비교 표본을 구분한다. 이미 본 비교 결과를 새 미관찰 검증이라고 부르지 않는다.
5. 허용 기준을 결과를 보기 전에 고정한다. 자료의 불확도와 모델 목표를 근거로 설명한다.
6. 독립된 모델은 같은 준비자료로 묶되, 다른 모델의 통과를 대상 모델의 통과로 옮겨 적지 않는다.

## 8. 진행률 반영

```bash
python scripts/record_physical_progress.py
```

이 도구는 실행 receipt의 무결성과 source gate를 확인해 공개 상태와 미완료 목록을 갱신한다. 갱신 후 모델별 적용 영역과 남은 작업의 설명을 검토한다. 각 보고에는 완료 모델/223, 남은 모델, 시험 통과·실패·오류·건너뜀, 재사용 수, 실행 시간과 시간 단축 조치를 명시한다.

## 9. 공개 배포 재현

`docs/PUBLISHING_KO.md`를 따른다. wheel/sdist/source archive에 원자료, 개인 환경, 자격증명이나 비공개 문서가 들어가지 않았는지 점검한다. 새 배포명이나 버전 변경만으로 계산 API를 바꾸지 않는다. 공개한 tag/커밋, checksum과 실행 증거를 함께 보존한다.

독립 검증이 끝나지 않은 영역을 새 release에서 조용히 삭제하지 않는다. 알려진 제한을 유지하고, 개선된 내용과 아직 남은 내용을 명확히 구분한다.
