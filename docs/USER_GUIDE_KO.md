# Pipeline Safety Toolkit 상세 사용 설명서

## 1. 먼저 결정할 사항

계산할 설비와 문제를 정의하고 모델 ID, 실제 API, 적용 가정과 검증 영역을 선택한다. 고정 목록은223개지만 한 ID에 여러 계산 함수가 연결될 수 있다. 레지스트리의 짧은 계산 ID와 공학 방법 ID를 같은 것으로 해석하지 않는다. 모델별 목록은 [카탈로그](MODEL_CATALOG_KO.md)에 있다.

입력이 확보되지 않은 상태에서 계산값을 채우지 않는다. 예를 들어 유량을 모르면0유량, 재료가 불명확하면 탄소강, 압력 기준이 없으면 절대압으로 가정하지 않는다. 사용자가 시나리오를 위해 정한 값은 관측값과 구분하고 `origin`과 evidence에 그 성격을 남긴다.

## 2. 설치와 의존성

Python3.12 이상의 별도 가상환경에서 설치한다. 기본 패키지는 NumPy, Pydantic/Pydantic-core, Pint로 구성된다. 선택 기능을 실행할 때만 필요한 extra를 설치한다. 예를 들어 수치 수력·열 모델은 `engineering`, CoolProp은 `thermo`, PyNite 구조는 `structural`, scikit-fem은 `soil-fem`이다.

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install .
python -m pip install ".[engineering,thermo,structural,dev]"
```

같은 PC의 다른 프로젝트 환경을 그대로 섞는 대신 프로젝트별 환경을 사용한다. `requirements-tested.txt`는 과거 검증 환경의 목록이며 운영체제와 기능 범위에 따라 재현 절차를 검토한다. 외부 소유 엔진을 사용하면 해당 소스 버전·실제 import 경로·라이선스도 확인한다. [연동 지도](GITHUB_INTEGRATION_MAP_KO.md)를 따른다.

## 3. 모델 탐색

```python
from pipeline_integrity import engineering_method_specs, describe_engineering_method

for spec in engineering_method_specs():
    print(spec.id, spec.apis, spec.supported_domain)
print(describe_engineering_method("pump.pressure_or_head_duty_power"))
```

`engineering_method_specs()`는 검토된 공학 방법을 나열한다. `describe_engineering_method()`는 구체적인 설명·연결 정보를 제공한다. 여러 API가 있는 모델에서는 선택한 하위 기능이 문제에 맞는지 확인한다. 예를 들어 같은 압축 범주에 endpoint 효율 관측과 다단 압축 경로가 함께 있으면 필요한 계산 함수는 서로 다르다.

## 4. 기본 레지스트리 실행

README의 펌프 동력 예제를 실행하면 `Calculation` 결과를 받는다. 결과의 상태, 값, 출력 단위와 이슈를 확인한다. 키 이름만 보고 물리 의미를 추정하지 않는다. 하중의 부호, 좌표축, 압력차와 절대압, 체적유량과 질량유량을 구분한다.

```python
from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

ctx = RunContext(asset_id="pump-A", scenario_id="authored-check",
                 clock_id="seconds", origin="synthetic")
r = create_registry().evaluate("equipment.pump_power", {
    "volume_flow_m3_s": 0.01, "density_kg_m3": 1000.0,
    "head_m": 20.0, "pump_efficiency": 0.8, "motor_efficiency": 0.9,
}, ctx)
print(r.status, r.values, r.output_units)
```

이 입력은 작성한 예제 조건이며 실제 설비 관측이 아니다. 효율의 의미와 사용 지점도 명확히 해야 한다. 펌프 유체 동력, 축 동력, 모터 입력이 같은 값은 아니다.

## 5. typed API 직접 호출

레지스트리에 없는 복잡한 모델은 typed request와 공개 함수를 직접 사용한다. 실제 모델 함수와 request 형식은 카탈로그의 소스 링크에서 확인한다.

```python
from pipeline_integrity import get_engineering_method_callable
from pipeline_integrity.models.hydraulics import LiquidHeadLoss

f = get_engineering_method_callable(
    "hydraulics.circular_pipe_friction",
    api="pipeline_integrity.models.hydraulics.liquid_head_loss",
)
# LiquidHeadLoss의 실제 필드와 명시적인 마찰/천이 정책을 확인한 뒤 request를 만든다.
print(f, LiquidHeadLoss.model_fields.keys())
```

함수에 `context` 또는 `property_provider`가 있으면 그것도 계약의 일부다. callable을 얻는 것으로 계산이 완료되지는 않는다. request 검증을 우회하려고 `model_copy()`만 호출한 불완전 값을 전달하지 말고 해당 공개 API에서 지원하는 검증 경계를 사용한다.

## 6. 단위·압력·시계

- 수치 모델 경계에서는 SI를 사용한다. 편의 변환 함수는 그 모델의 명시된 단위 계약을 따른다.
- gauge 압력은 기준 대기압이 필요한 위치에서만 절대압으로 바꾼다. 기준을 알 수 없는 관측을 절대 상태로 재해석하지 않는다.
- 표준 체적은 기준 압력·온도·압축성·조성에 의존한다. 실제 체적과 표준 체적 사이에는 질량 보존을 확인한다.
- 시간은 clock ID와 시작점, 지연·왕복 여부를 함께 기록한다. cycle을 시간으로 바꾸려면 알려진 운전 일정이 필요하다.
- 음수 하중·역류·음의 열유속은 모델이 지원하는 경우 물리 정보다. 절댓값으로 바꿔 손실·방출이라고 해석하지 않는다.

## 7. 열역학과 사용자 물성

CoolProp을 설치하면 `CoolPropProvider`와 `StateRequest`를 사용할 수 있다.

```python
from pipeline_integrity.thermo import CoolPropProvider, StateRequest
provider = CoolPropProvider(use_cache=False)
state = provider.resolve(StateRequest(
    fluid="Water", backend="HEOS", inputs={"P": 101325.0, "T": 300.0},
))
print(state.phase, state.density_kg_m3)
```

입력 pair와 backend의 지원 범위, 단상 요구를 확인한다. 상태량 원점이 바뀔 수 있는 엔탈피·엔트로피는 같은 물성 기준에서 비교한다. 라이브러리의 상태 조회 실패를0밀도나 가상 단상으로 치환하지 않는다. PR 질소의 알려진 P–T 조회 한계는 [기록](../research/physical_backend_limits.json)에 있다.

사용자 공급자는 기존 provider 프로토콜에 맞춰 요청한 유체·상태·버전과 일치하는 결과를 반환해야 한다. 공개 시험의 synthetic provider는 수학적 기준을 분리하기 위한 것으로 실제 유체의 보정을 대신하지 않는다.

## 8. 데이터 준비

원 자료는 `data/raw/`, 변환 자료는 `data/processed/`, 실행 산출물은 `artifacts/`처럼 Git에서 제외된 위치에 둔다. 공급자 링크·DOI·버전·라이선스·파일 크기·체크섬을 기록하고 원본을 덮어쓰지 않는다.

자료를 읽을 때 최소한 다음을 확인한다.

1. 관측 열의 단위와 계측기 위치, 관경/길이 등 형상.
2. 시각 정렬, 결측·부호·센서 포화, 평균에 사용한 구간.
3. 관측된 유량과 추정한 유량, 실제 상태와 기준 상태의 차이.
4. 보정 자료와 미관찰 비교 자료의 분리.
5. 재사용·재배포 조건 및 개인정보·상업적 제한.

현재 공개 소스에는 원 관측이나 다운로드한 대용량 묶음을 배포하지 않는다. 데이터 카탈로그에 등록된 파일이 로컬에 존재하거나 검증을 통과했다고 해석하지 않는다. 각 자료별 문서의 acquisition/staging 조건을 따른다.

## 9. 다른 모듈과 연결

기존 HRA·감시·재료·위험·수소/열역학 엔진의 책임을 유지한다. 이 패키지의 adapter는 문맥과 자료 의미를 보존하는 경계다. 기존 엔진의 구현을 복사해 새 모델처럼 세지 않는다.

연결 순서는 입력 계약 확인 → 소유 엔진 버전/출처 고정 → 계산 → 상태/단위/문맥 확인 → 다음 모델 전달이다. 기간확률과 요구 시 실패확률, 손상 비율과 실패확률, 위험 점수와 물리량의 구분은 후속 연결에서도 유지한다. FTA는 가능한 연결 방식 중 하나이며 이 패키지의 모든 물리를 FTA로 제한하지 않는다.

외부 엔진이 필요한데 없으면 환경 부족과 모델 실패를 구분해 기록한다. 원 프로젝트의 확장이 필요한 기능은 [확장 요청](EXTENSION_REQUESTS_KO.md)과 구체적인 입출력/검증 조건으로 전달한다.

## 10. 오류와 미계산 상태

기본 모델과 adapter의 상태 어휘는 다를 수 있다. `computed`, `computed_diagnostic`, `needs_data`, `out_of_scope`, `not_converged` 또는 예외가 나타나는 정확한 조건은 해당 API를 확인한다. 외부 엔진 실패·단위 충돌·물성 범위 이탈을 임의의0결과로 숨기지 않는다.

해석이 안 되면 입력 계약, 필수 evidence, 단위/기준, property provider의 반환 상태, 선택 라이브러리/버전, solver 범위 순서로 원인을 좁힌다. 계산 반복 횟수를 늘리는 것으로 잘못된 모델 적용을 해결하지 않는다.

## 11. 결과 보존과 재실행

입력 request와 문맥, 패키지/소스 버전, solver 설정, 반환 상태·단위·값, 이슈와 데이터 식별정보를 함께 보존한다. 실행 전후 소스 해시와 환경 정보, JUnit/로그를 기록하면 다음 세션에서 근거를 확인할 수 있다.

공개 GitHub에는 상세 실행 원자료를 넣지 않아도 되지만, 모델별 검토 범위와 시험 선택자 및 요약 증거는 남긴다. 비공개 산출물이 없어진 새 PC에서는 그 시험을 다시 실행해야 한다. 이전 통과라는 문장만으로 새 소스 버전의 검증을 대신하지 않는다.

## 12. 검증과 재개

[검증 재개 안내](RESEARCH_CONTINUATION_KO.md)에 독립 물리시험과 공개 설치 시험의 차이, 의존 소스 고정, 실패 분류, 체크섬과 미완료 목록을 정리했다. 현재198개 제한 영역 프로필의 통과는 모든223개 모델의 전체 자격을 의미하지 않는다. 남은25개는 [순서표](PHYSICAL_VERIFICATION_BACKLOG_KO.md)를 따른다.
