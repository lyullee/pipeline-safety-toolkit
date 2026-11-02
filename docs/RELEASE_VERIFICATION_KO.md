# GitHub 공개 전 검증과 재현

이 저장소는 Python 연구용 개발 버전이다. 구현 목록, 대표 계산 시험, 제한된 영역의
물리시험, 실측 적합성은 별도 상태로 관리한다. 최신 공개 집계는
`research/public_validation_status.json`, 패키지 바이트 목록은
`research/publication_source_manifest.json`에 있다.

## 확인된 범위

- 고정 공학 방법223개, 구현 매핑339개 변형,304개 공개 Python API.
- 대표 연결226건·기존 입력/계산식/극단값 재실행16,346건·전체 계산223건:16,795건 통과.
- 명시된 제한 영역의 물리시험198/223개,2,013건 통과. 나머지25개는 이 캠페인의 검토가 남아 있다.
- 공개용 독립 설치 시험은 별도17개 파일276건이다. 전체 연구 시험을 뜻하지 않는다.
- 기존 확장 연구 회귀의 실측 적합성 실패4건과 Windows 권한 미실행2건은 보존한다.
- 이번 공개 전 수정은 펌프/회전축의 선택 SciPy import를 실제 계산 시점으로 옮긴 것이다.
  펌프 관련24개 회귀, 기존 대표 캠페인과 물리시험을 재실행했다.

## 새 환경에서의 기본 사용

Python3.12 이상을 사용한다. Windows에서 `python -m venv .venv`와
`.venv\Scripts\Activate.ps1`, Linux/macOS에서 `python -m venv .venv`와
`source .venv/bin/activate`로 환경을 만든다.

```shell
python -m pip install .
pipeline-integrity code-progress
pipeline-integrity engineering-methods
pipeline-integrity engineering-method-describe pressure.nominal_membrane
pipeline-integrity workflow examples/pump_energy_workflow.json
```

기본 설치의 직접 의존성은 NumPy, Pydantic/Pydantic-core, Pint다. SciPy, CoolProp,
구조 해석기와 별도 소유 프로젝트는 해당 계산을 사용할 때 설치한다. 자료·단위·
설비·시계·압력 기준과 검토 상태를 지정해야 하며, 공급자가 없으면 결과를 만들어
반환하지 않는다. CLI의 단위와 검토 계약은 `docs/API_KO.md`를 따른다.

## 공개용 수치 시험

```shell
python -m pip install ".[engineering,thermo,structural,dev]"
python scripts/run_public_checks.py
python scripts/run_examples.py
```

`run_public_checks.py`에 실행 파일을 명시했다. 외부 MatIntegrity가 필요한 방사형
열전도 시험 등은 이 독립 실행 목록에 포함하지 않는다. 해당 시험은 원래 연구
목록에 그대로 남긴다. 공개 시험의 건너뜀이나 실패도 통과로 계산하지 않는다.
기존의 전체 `pytest` 실행에는 선택 의존성, 호환 소유 엔진, 별도 원자료와 참조
fixture가 필요하므로, 새 clone의 기본 명령으로 안내하지 않는다.

## wheel·소스 배포물 검사

```shell
python -m pip install build
python -m build --wheel
python scripts/check_isolated_core.py
python -m build --sdist
python scripts/check_publication_boundaries.py
python scripts/audit_release.py --check-consistency
```

새 core 전용 가상환경을 만들어 실제 wheel을 설치하며, 선택 라이브러리30개가
없음을 확인한다. 기존 core 설치 검사는 실제 계산·전달, 미상값과 의존성 누락
경계,223개 소스 바인딩을 확인한다. wheel 설치 후 자료 계약과 API가 저장소
소스를 대신 불러오지 않는지도 공개 전 별도 복사 환경에서 검증한다.

배포물에는 원자료, 변환 관측행, 개인 실행 환경, 인증 파일, 소유 엔진 소스와
비공개 실행 로그를 넣지 않는다. `tests/data/mechanics_input_baselines_223.json`은
이 프로젝트에서 작성한 합성 입력 fixture이며 명시적으로 포함한다. 임의의
`tests/data` 파일까지 허용하지 않는다. 원자료 취득법·라이선스·출처는 원래의
자료별 안내 및 `THIRD_PARTY_NOTICES.md`에 남긴다.

## 전체 연구 캠페인

실제 외부 소유 엔진이 필요한 경우 해당 프로젝트와 조건을 별도로 준비한다.
다음 환경변수는 호환 소스 checkout의 `src` 디렉터리를 가리킨다.

- `PIPELINE_FORMULA_MATERIAL_SRC`: MatIntegrity의 src.
- `PIPELINE_FORMULA_LH2DT_SRC`: LH2DT의 src.

```shell
python scripts/run_physical_campaign_223.py
python scripts/run_remaining_engineering_campaign.py
```

일부 전체 연구 시험은 원자료와 별도 참조 fixture도 요구한다. 과거의 로컬
참조 경로를 공개 자료가 이미 포함되어 있다는 의미로 해석하지 않는다. 원본
해시·라이선스를 확인하고 준비한 다음 실행하며, 자료 누락은 물리 통과가 아니다.
외부 소스가 다르면 기존 source pin을 지우거나 임의로 재승인하지 않는다.

## 알려진 검증 한계

관마찰2건은 같은 구간의 실측 손실을 공통 거칠기로 재현하지 못했다. 구간별 등가
거칠기 후보는 기존 비교에서 최대오차1.16%였지만, 이미 확인한 자료와 좁은 유량
범위의 탐색 결과다. 고정 지수 강도식1건은 해당 보정에서 부적합이고, 로그식이
더 넓은 예측구간을 갖는다는 점을 함께 기록했다. 크리프 수명1건은 계수 추정
불확실성까지 포함하는 추가 검토가 필요하다. 기존 실패를 새 합격으로 바꾸지 않는다.

Git은 Python/JSON의 정확한 바이트를 보존하도록 설정했다. 소스 pin은 바이트 해시이므로
포맷 일괄 변경이나 줄바꿈 변경에는 재검토가 필요하다. 자동 점검은 명시한 포함·
제외 경계의 확인이며, 모든 자료의 법적 권리나 현장 사용 적합성을 보증하지 않는다.

## CI와 스타일 상태

GitHub Actions는 Windows/Linux에서 새 core 설치와 명시된 공개 시험을 실행하도록
설정했다. 실제 원격 CI 실행은 push 후 해당 실행 기록으로 확인해야 한다.
본 작업에서 직접 실행한 운영체제는 Windows다.

구문·미정의 이름 검사는 필수 게이트다. 전체 Ruff 규칙 및 포맷 검사에는 기존
정적 경고가 남아 있으며 CI의 별도 advisory 작업으로 표시한다. 이를 전체 lint
통과로 보고하지 않는다. 경고의 정확한 수와 규칙별 집계는 공개 상태 파일에 남긴다.
