# 공통 어댑터 JSON 작업 API

18개 어댑터의 입력 계약·호출·재생 검증을 같은 작업 형식으로 연결한다.
기존 24개 기본 계산 registry와 원 물리/통계 엔진은 그대로 사용한다. 이 API는
그 엔진의 계산식을 복제하거나 needs_data를 computed로 바꾸지 않는다.
새 작업 API와 회귀는 작성했으며 수치/CLI 실행 검증은 누리온 대기다.

## 탐색과 CLI

```bash
pipeline-integrity adapters
pipeline-integrity adapter-describe construction.route
pipeline-integrity adapter-run job.json --output result.json
pipeline-integrity adapter-verify result.json
```

adapters는 정적 allowlist를 반환하며 어댑터/원 엔진을 import하지 않는다.
adapter-describe는 선택한 입력 모델과 보조 payload의 JSON Schema를 만든다.
필요한 모듈은 import하지만 계산기를 호출하지 않는다. 원 라이브러리가 필요한
스키마에는 해당 선택 의존성이 설치되어 있어야 한다.

adapter-run/verify의 기본 파일 예산은 64,000,000바이트다.
`--maximum-input-bytes`로 명시 조정할 수 있다(최대1GB). CLI는 읽기 전에 크기를
확인하고 서비스도 adapter import/실행 전에 job byte 예산을 확인한다.
raw JSON과 기본 필드를 포함한 canonical 저장 job 모두 이 예산 안에 있어야 한다.
Python model/dictionary의 canonical 인코딩은 같은 compact UTF-8 표현을 사용하여
한글·지수 표기의 serializer 차이 때문에 replay 예산이 달라지지 않게 한다.
Python verify의 maximum_job_bytes는 embedded job의 예산이며 큰 원 result 전체에
새 제한을 거는 옵션이 아니다. CLI의 maximum-input-bytes는 실제 입력 파일 전체를 제한한다.
입력 JSON의 임의 모듈 이름·코드·실행 경로를 import하지 않는다.
중복 object key와 비유한 JSON 숫자는 조용히 덮어쓰거나 null로 바꾸지 않고 거부한다.

## Python과 파라미터

```python
from pipeline_integrity.adapter_jobs import AdapterJob, run_adapter_job

# request와 context는 해당 어댑터의 검토된 typed 입력이다.
job = AdapterJob(
    id="my-route-comparison",
    adapter_id="construction.route",
    parameters=request.model_dump(mode="json"),
    context=context,
)
record = run_adapter_job(job)
result = record["result"]
```

`parameters`는 원 입력 모델로 다시 검증한다. 잘못된 추가 필드·단위·cloned invalid
값·비유한 숫자는 원 계약/공통 직렬화에서 거부한다. context는 asset/scenario/clock,
원 근거와 검토한 boundary metadata를 그대로 전달한다. 파라미터를 바꾼 뒤 과거
boundary/근거/공급 영수증을 재사용하지 않는다. 서비스가 검토를 대신해 자동 재승인하지 않는다.

`examples/adapter_job.py --output authored_job.json`은 기존 합성 경로 예제로 job을
작성한다. 기본적으로 모델을 계산하지 않으며, `--execute`를 명시하면 실행과 재생
검증을 호출한다. `--reviewed`는 합성 예제의 검토 분기일 뿐 실제 현장 승인이 아니다.

## 공급자와 보조 이력

사용자 공급자는 Python에서 직접 등록한 객체를 별칭으로 연결한다.

```python
job = AdapterJob(
    id="candidate-location-study",
    adapter_id="leak.inverse_localization",
    parameters=request.model_dump(mode="json"),
    context=context,
    bindings={"provider": "reviewed-forward-model"},
)
record = run_adapter_job(job, providers={"reviewed-forward-model": provider})
```

등록되지 않은 별칭, None 객체, 해당 어댑터에 없는 역할은 실행 전에 거부한다.
JSON에서 임의 클래스를 만들거나 source 파일을 실행하는 loader는 없다. CLI에는
외부 Python 객체 registry를 주입하지 않으므로 bindings 작업은 Python API로 실행한다.

지원 역할은 센서 배치 backend, 위치/좌굴/파괴/고형화 provider, 가열 수송
property_provider다. 실제 허용 역할은 adapters/describe에서 확인한다.
원 공급자의 source/config/version/evidence·단위·적용범위 검증은 기존 어댑터가 수행한다.

검토된 파괴 궤적은 `payloads={"trajectory": trajectory.model_dump(mode="json")}`,
고형화 경계는 `payloads={"envelope": envelope.model_dump(mode="json")}`로 전달한다.
각 payload는 원 계약으로 검증하며 provider source_mode와 혼용해 대체하지 않는다.
다른 이름의 보조 데이터는 허용하지 않는다. 원 어댑터가 보류한 payload를 계산에
사용했다고 표시하지 않는다.

## 기록과 재생

반환 기록은 원 job·원 adapter result·서비스/어댑터/공통 소스 식별자와 digest를 담는다.
외피 schema는 선언된 키만 허용한다. 재해시한 임의 approval/qualification 키를 추가해
유효한 서비스 기록처럼 전달하지 않는다. 원 adapter result의 필드·상태·telemetry는 그대로 보존한다.
`engineering_qualification_conferred_by_dispatch=False`이며 원 result의 상태·단위·
근거·미지원 범위를 읽어야 한다. 코드가 호출됐다는 사실만으로 물리적 자격을 주지 않는다.

`verify_adapter_job_record(record, job=..., providers=...)`는 저장한 입력과 caller 입력,
현재 소스를 비교하고 원 verify 함수를 호출한다. 원 검증기가 True 또는 valid=True를
반환해야 한다. valid=False인 사전의 truthiness를 성공으로 보지 않는다.
CP-SAT elapsed time 등은 원 검증기의 정책을 따른다. 유효한 다른 배정이나 solver
시간한도 변화로 엄격한 재생이 실패할 수도 있다. 해시는 실행 신원이나 전자서명이 아니다.

재생 검증은 실제 원 계산을 다시 실행할 수 있다. 누리온에서 의존성과 provider를
준비한 뒤 수행하고, 실행하지 않은 회귀를 통과로 기록하지 않는다.


정적 배포 목록은 `python scripts/export_adapter_jobs.py`로 갱신한다.
실제 소스의 입력/payload 클래스·호출/검증 함수와 provider keyword를 AST로 대조해
`research/adapter_job_inventory.json`에 기록한다. 이 대조는 수치 호출이나 Pydantic
runtime schema 실행 검증을 대신하지 않는다.


## 교차 정적 검토 후 변경

18개 allowlist의 실제 class/evaluate/verify signature와 provider/payload 역할을 소스 AST로
대조했다. dispatcher가 실제로 넘기는 positional/keyword 수와 이름, 필수 keyword,
positional 중복 충돌을 확인한다. 동기 dict API이므로 async 함수는 static exporter에서
실제 호출 가능하다고 표시하지 않는다. 입력 클래스의 선언/함수 signature 대조는
Pydantic runtime schema나 과학 계산의 실행 통과가 아니다. 중앙 inventory 갱신은 별도 작업이다.

run/verify는 원 함수를 호출하기 전후에 서비스/adapter/shared source identity를 대조한다.
도중 source가 바뀌면 기록을 채택하지 않는다. 이 파일 hash 검사는 이미 로드된 객체의
전체 동작·mutable provider 상태·과거 실행의 진위를 인증하지 않는다. 공급자는 JSON
loader가 구성하지 않으며 Python caller가 명시 등록한 객체라는 경계를 유지한다.

원 verifier가 True 또는 valid=True를 주면 반환된 telemetry를 그대로 adapter_verification에
남긴다. 원 verifier의 허용된 solver-time 차이를 없애기 위해 무조건 result 전체를 재비교하는
새 규칙은 추가하지 않는다. valid=False나 bool이 아닌 truthy 값은 성공으로 승격하지 않는다.
원 adapter가 unused/withheld로 남긴 typed trajectory/envelope는 job에 보존하되 실제 계산에
사용했다고 표시하지 않는다. 같은 source_mode/provider 계약과 원 replay를 따른다.

CLI의 adapter-verify도 동일 strict JSON decode를 사용하고 adapter API의 AttributeError를
구조화된 실패(exit2)로 전달한다. 다른 기존 CLI 계산 경로의 의미와 registry는 변경하지 않는다.
새 회귀는 duplicate/nonfinite JSON의 import 전 차단, outer 승인키 재봉인, Unicode·지수 값의
동일 byte 예산, source 도중 변경, 원 telemetry 보존, 실제 typed 미사용 payload 및 static
exporter 호출 불가능 signature를 포함한다. 이번 검토에서 pytest·native·수치·CLI·exporter
실행은 모두 0회이며 Ruff/compile 및 AST 텍스트 대조만 수행했다.
