# Pipeline Safety Toolkit

**Composable Python tools for pipeline engineering, model exchange and reproducible verification.**

배관의 유동·열역학·구조·손상·계측·운영 계산을 작은 모델과 명시적인 데이터 계약으로 연결하는 연구용 패키지입니다. 사용자 파라미터, 물성 공급자, 관측 데이터와 외부 엔진을 교체할 수 있습니다. 결과에는 단위, 적용 조건, 입력 출처와 실행 식별정보를 함께 남깁니다.

- **배포 이름:** `pipeline-safety-toolkit`
- **Python import:** `pipeline_integrity` — 기존 코드와의 연결을 위해 유지합니다.
- **공개 버전:** `0.1.0a1` — 연구용 알파 버전입니다.
- **Python:** 3.12 이상
- **라이선스:** MIT. 별도 라이브러리와 데이터의 이용조건은 각 출처에 따릅니다.

[시작하기](#설치) · [사용 설명서](docs/USER_GUIDE_KO.md) · [API 안내](docs/API_KO.md) · [223개 모델 목록](docs/MODEL_CATALOG_KO.md) · [개발 이력](docs/DEVELOPMENT_HISTORY_KO.md) · [검증 재개](docs/RESEARCH_CONTINUATION_KO.md) · [변경 기록](CHANGELOG.md)

## 현재 제공하는 것

| 영역 | 주요 기능 | 설명 |
|---|---|---|
| 유동·배관망 | 액체 손실, 가스 경로, 펌프 운전점, 밸브, 계측 | [API](docs/API_KO.md), [액체 수력](docs/LIQUID_HEAD_KO.md) |
| 과도·수송 | 압력파, 자유수면, 유체 재고, 분산 수송, 관측 역산 | [과도](docs/VALVE_TRANSIENT_KO.md), [배치 수송](docs/BATCH_TRANSPORT_KO.md) |
| 열·물성 | CoolProp 상태, 열교환, 상변화 경계, 지중 열전달 | [열 연동](docs/HEATED_TRANSPORT_KO.md), [토양 FEM](docs/SOIL_FEM_KO.md) |
| 구조·시공 | 프레임, 열·압력 하중, 좌굴, 인양, 지지, 국부 좌표 | [구조](docs/STRUCTURAL_KO.md), [좌굴](docs/BUCKLING_KO.md) |
| 재료·손상 | 부식, 피로, 크리프, 파괴·성장, 전기방식 | [재료 단면](docs/MATERIAL_SECTIONS_KO.md), [파괴](docs/FRACTURE_EXCHANGE_KO.md) |
| 계측·자료 | 교정, 단위·시계·압력 기준, 출처 고정 reader, 불확실성 | [계량](docs/METROLOGY_KO.md), [자료 계약](docs/JSON_BOUNDARY_KO.md) |
| 운영·위험 연결 | 제어, 인적 반응, 감시, 검사 계획, 위험·비용 교환 | [인적 반응](docs/HUMAN_RESPONSE_KO.md), [연동 지도](docs/GITHUB_INTEGRATION_MAP_KO.md) |

고정된 공학 방법은 **223개**, 구현 매핑은 **339개 변형**, 공개 연결점은 **304개 Python API**입니다. 기본 계산 레지스트리, 외부 adapter, 데이터 후보와 공학 방법의 개수는 다른 목록입니다. 모델을 찾을 때는 [모델 카탈로그](docs/MODEL_CATALOG_KO.md)의 ID와 API를 사용하십시오.

## 검증 상태와 사용 범위

| 구분 | 확인된 결과 | 해석 |
|---|---:|---|
| 코드 구현 매핑 | **223/223** | 선언한 방법과 실제 구현 경로를 정적으로 확인 |
| 모델별 대표 연결·기존 기준 재실행·전체 계산 캠페인 | **16,795/16,795건** | 해당 캠페인의 실행 결과. 전체 현장 조건의 승인 아님 |
| 독립 물리시험의 명시된 제한 영역 | **198/223개, 2,013/2,013건** | 독립 해·적분·보존·수렴·사건 등을 적용한 프로필 |
| 남은 독립 물리 기준 | **25개** | [우선순위 및 필요한 근거](docs/PHYSICAL_VERIFICATION_BACKLOG_KO.md) |
| 기존 실측 적합성 실패 | **4건 유지** | 관마찰2, 변형률 속도별 강도1, 크리프 수명1 |
| 공개 설치 시험 | **17개 파일, 276건의 명시된 부분집합** | 원자료와 외부 엔진 없이 재현할 기본 시험 |

물리시험의 완료 수는 **검토한 프로필을 가진 모델 수**입니다. 모든 변형, 재료, 경계조건, 경험계수의 보정 또는 현장 안전성을 검증했다는 뜻이 아닙니다. 수렴시험이 필요 없는 대수 모델에는 적절한 독립 적분·평형·단위·극한 기준을 적용합니다. 같은 식을 다시 계산한 결과를 새 실측 검증으로 세지 않습니다.

알려진 물성 제한으로 CoolProp PR 질소의 일부 자동 P–T 조회가 실패합니다. 같은 backend의 T–밀도 좌표로 확인한 결과와 이 제한을 구분해 [기록](research/physical_backend_limits.json)합니다. 오류를 피하려고 다른 물성식을 몰래 대입하지 않습니다.

최신 집계는 [공개 검증 상태](research/public_validation_status.json), 모델별 적용 영역·시험 선택자는 [물리시험 목록](research/physical_verification_223.json)에 있습니다. 과거 개발 기록의 174/202개 분모를 현재223개와 합산하지 않습니다. 상세 절차는 [공개 전 검증 안내](docs/RELEASE_VERIFICATION_KO.md)를 참조하십시오.

## 설치

기본 설치는 NumPy, Pydantic/Pydantic-core, Pint만 요구합니다. 필요한 해석 기능의 추가 의존성은 별도로 설치합니다.

### GitHub 소스에서 설치

```bash
git clone https://github.com/lyullee/pipeline-safety-toolkit.git
cd pipeline-safety-toolkit
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

GitHub Releases에 wheel이 올라온 경우에는 내려받은 `pipeline_safety_toolkit-0.1.0a1-py3-none-any.whl`을 `python -m pip install <wheel 경로>`로 설치할 수 있습니다. PyPI 등록이 완료된 뒤에는 `python -m pip install --pre pipeline-safety-toolkit`를 사용할 수 있습니다. 등록 여부는 실제 PyPI 페이지에서 확인하십시오.

### 필요한 기능만 추가

```bash
python -m pip install ".[engineering,thermo]"
python -m pip install ".[structural,soil-fem,dev]"
```

| extra | 사용하는 경우 |
|---|---|
| `engineering` | SciPy 수치해석, fluids/ht 계산 |
| `thermo` | CoolProp 열역학 상태 |
| `structural` | PyNite 프레임 및 선택 구조 모델 |
| `soil-fem` | scikit-fem 지중 열전달 |
| `data` | pandas, Excel, HDF5 등 자료 reader |
| `water` | WNTR 물 관망 |
| `scheduling` | OR-Tools 계획 |
| `differentiation`, `mooring`, `soil` | 선택 미분·계류·토양 엔진 |
| `dev` | pytest, build, Ruff |

기본 설치만으로 모든 선택 모델이 계산되지는 않습니다. 외부 소유 프로젝트가 필요한 모델은 별도 설치·버전 고정이 필요하며, 그 엔진을 이 배포에 복사하지 않습니다. [연동 지도](docs/GITHUB_INTEGRATION_MAP_KO.md)와 [사용 설명서](docs/USER_GUIDE_KO.md)를 확인하십시오.

## 첫 계산: 펌프 동력

```python
from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext

engine = create_registry()
context = RunContext(
    asset_id="P-101",
    scenario_id="rated-flow",
    clock_id="elapsed",
    origin="user_declared",
)
result = engine.evaluate(
    "equipment.pump_power",
    {
        "volume_flow_m3_s": 0.02,
        "density_kg_m3": 998.0,
        "head_m": 50.0,
        "pump_efficiency": 0.8,
        "motor_efficiency": 0.95,
    },
    context,
)
print(result.status)
print(result.values)
print(result.output_units)
```

계산 성공 상태와 출력 단위를 확인한 뒤 다음 모델로 전달하십시오. 결측값이나 해석 불가능한 상태를0으로 바꾸지 마십시오. 실제 값의 키와 단위는 해당 API의 결과 계약을 따릅니다.

## 공학 방법과 직접 Python API 찾기

```python
from pipeline_integrity import (
    engineering_method_specs,
    describe_engineering_method,
    get_engineering_method_callable,
)

methods = engineering_method_specs()
print(len(methods))  # 223
spec = describe_engineering_method("hydraulics.circular_pipe_friction")
print(spec)
function = get_engineering_method_callable(
    "hydraulics.circular_pipe_friction",
    api="pipeline_integrity.models.hydraulics.liquid_head_loss",
)
print(function)
```

한 방법에 API가 여러 개이면 필요한 API를 명시적으로 선택하십시오. 반환된 callable은 원래 함수입니다. 각 함수의 request, context, provider와 결과 형식을 하나의 범용 딕셔너리로 강제 변환하지 않습니다. [카탈로그](docs/MODEL_CATALOG_KO.md)에 허용된 연결점과 실제 소스 경로가 정리되어 있습니다.

## CLI와 저장된 workflow

기존 `pipeline-integrity` 명령을 유지하고 같은 기능의 `pipeline-safety-toolkit` 명령도 제공합니다.

```bash
pipeline-safety-toolkit code-progress
pipeline-safety-toolkit engineering-methods
pipeline-safety-toolkit engineering-method-describe pressure.nominal_membrane
pipeline-safety-toolkit workflow examples/pump_energy_workflow.json
```

JSON workflow를 사용하면 계산 순서와 입력 매핑을 저장할 수 있습니다. 파일로 저장된 값도 단위·시계·설비·압력 기준이 맞아야 합니다. 자세한 예는 [사용 설명서](docs/USER_GUIDE_KO.md), [공통 API](docs/API_KO.md), `examples/`를 참조하십시오.

## 사용자 데이터와 모델을 연결하는 원칙

1. **단위:** 모델 경계는 SI를 사용합니다. 기준 상태가 있는 표준 체적, gauge/absolute 압력과 온도 기준은 별도로 명시합니다.
2. **문맥:** 설비·위치·시계·관측 출처·물성 기준이 다른 기록을 조용히 합치지 않습니다.
3. **물성:** 물성 공급자를 주입할 수 있습니다. 요청한 유체·상태·버전과 반환값을 확인합니다.
4. **자료:** reader는 원 자료의 결측·부호·시각과 식별정보를 보존합니다. dataset 등록이 다운로드나 실측 적합성 통과를 의미하지 않습니다.
5. **교정:** 학습 자료와 비교 자료를 나누고, 실제 물성으로 식별한 값과 손실을 흡수하는 유효 계수를 구분합니다.
6. **위험 연결:** 수명, 손상, 요구 시 실패확률, 기간확률, 빈도와 위험 점수의 의미를 따로 지정합니다. 임의의 한 값으로 치환하지 않습니다.

## 시험 실행

### 공개 자료 없이 실행하는 부분집합

```bash
python -m pip install ".[engineering,thermo,structural,dev]"
python scripts/run_public_checks.py
python scripts/run_examples.py
```

이는 전체 연구 시험이 아닙니다. 일부 시험은 외부 소유 프로젝트나 별도로 준비한 실제 자료가 필요합니다. 무조건 `pytest` 전체를 실행한 결과를 공개 설치 검증과 혼동하지 마십시오.

### 전체 물리시험을 재현하는 경우

```bash
python scripts/run_physical_campaign_223.py
```

먼저 [재개 절차](docs/RESEARCH_CONTINUATION_KO.md)에 따라 외부 소스·환경·자료를 준비해야 합니다. 이전 로컬 증거가 있고 그 소스가 그대로 남아 있으면 `python scripts/continue_physical_campaign.py`로 검증된 통과 증거만 재사용할 수 있습니다. GitHub clone에는 개인 PC의 실행 산출물이 포함되지 않으므로 처음부터 재현해야 합니다.

## 저장소 구조

```text
src/pipeline_integrity/    실제 Python 구현과 검토된 카탈로그
examples/                 가정과 문맥을 지정한 실행 사례
tests/                    계약·계산·수치·연결·물리 기준 시험
scripts/                  공개 시험, 출처 검사, 배포 및 연구 재개 도구
research/                 공개 가능한 상태·범위·출처 메타데이터
docs/                     모델별 설명, 개발 이력, 검증 및 재개 안내
jobs/                     선택 원격 배치 템플릿; 계정·인증 정보 제외
data/                     안내만 배포; 원 관측과 변환 자료는 제외
```

## 개발과 재현성

- [사용 설명서](docs/USER_GUIDE_KO.md): 설치, 모델 선택, 파라미터, 데이터, 결과와 오류 처리.
- [개발 이력](docs/DEVELOPMENT_HISTORY_KO.md): 범위·구조·구현·자료·검증·수정의 시간순 기록.
- [연구 재개 안내](docs/RESEARCH_CONTINUATION_KO.md): 새 PC에서 준비할 것, 체크섬, 시험과 미완료 작업.
- [223개 모델 목록](docs/MODEL_CATALOG_KO.md): ID, 지원 범위, 실제 API, 제한 영역 물리시험 상태.
- [원본 API 설명](docs/API_KO.md), [상세 모델 안내 색인](docs/INDEX_KO.md).
- [기여 안내](docs/CONTRIBUTING_KO.md), [배포 절차](docs/PUBLISHING_KO.md), [변경 기록](CHANGELOG.md).

새 모델을 더하기 전에 기존 함수·공유 엔진을 찾으십시오. 모델 이름이나 wrapper를 추가한 것을 새 계산 구현으로 세지 않습니다. 코드·시험·환경·출처가 바뀌면 그 영향을 받는 증거를 재검토합니다.

## 라이선스·출처·안전한 해석

소프트웨어와 직접 작성한 설명은 [MIT](LICENSE)로 배포합니다. 제3자 라이브러리의 고지와 라이선스는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 따릅니다. 데이터는 각 공급자의 별도 조건을 적용하며, 등록된 자료가 전부 이 저장소에 포함되는 것은 아닙니다.

원 관측, 개인 환경, 계정·인증 정보와 비공개 참고 자료는 배포하지 않습니다. 경험계수, 재료 물성 또는 적용 규칙의 권리가 확인되지 않으면 사용자 제공 입력으로 남기며 임의의 보편 상수를 선택하지 않습니다. 문장 유사도 검사와 파일 제외 검사는 법률상 권리 검토를 완전히 대체하지 않습니다.

이 패키지의 결과만으로 설계기준 적합성이나 현장 안전을 승인하지 마십시오. 계산 조건과 모델 범위, 실측 근거 및 필요한 전문 검토를 확인해야 합니다.

## 인용

[CITATION.cff](CITATION.cff)를 사용하고, 재현에 사용한 버전·커밋·모델 프로필·데이터 식별정보를 함께 남겨 주십시오. DOI는 실제 Zenodo 등록 후 연결합니다. 아직 발급되지 않은 DOI를 표시하지 않습니다.
