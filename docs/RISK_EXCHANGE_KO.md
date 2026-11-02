# 인적신뢰도와 위험 엔진의 연결

이 패키지는 인적신뢰도 추정, FTA의 논리 계산, ETA의 조건부 경로 계산과
LOPA의 방호 계산을 다시 구현하지 않는다. 별도 설치한
[human-reliability-toolkit](https://github.com/lyullee/human-reliability-toolkit)의
엔진을 `HRARiskClient`로 호출하고, 기존 PRISM `DemandFailure`로 전달한다.
결과의 `exchange_checked`는 이 교환 경계를 확인했다는 뜻이다. 현장 정확도,
설계 승인, 방호의 IPL 인증을 부여하지 않는다.

## 설치와 처음 실행

기본 패키지 설치만으로 입력 계약과 JSON 작업을 작성할 수 있다. 실제 계산은
HRA 프로젝트가 필요하고 PRISM 전달에는 PRISM의 `lh2-tno`가 추가로 필요하다.
이 저장소의 선택 의존성에 해당 소스나 자료를 복제하지 않았다.

```powershell
python examples/risk_exchange.py --write-job artifacts/risk_job.json
# HRA를 별도 설치한 환경
pipeline-integrity risk artifacts/risk_job.json --output artifacts/risk_result.json
# 로컬 HRA 소스를 사용하는 자체 작성 예제
python examples/risk_exchange.py --hra PATH_TO_HRA
# 관측 근거 바인딩부터 PRISM까지 실제 확인
python scripts/check_risk_exchange.py --hra PATH_TO_HRA --prism-source PATH_TO_PRISM/lh2-tno/src
```

공개 요약은 `research/risk_exchange_results.json`이다. 마지막 명령은 원 추정값,
불확실성, 근거, 전체 요청과 받는 쪽 패킷을 `artifacts/risk_exchange`에 로컬로
남긴다. 원 관측 레코드나 방법별 참조표 전체를 코드 배포에 포함하지 않는다.
외부 소스의 취득 커밋과 실제 실행 소스 해시는 서로 다른 기록이다. 검증한
커밋은 `research/external_source_versions.json`에서 확인한다.

## 입력 계약과 의미

| 계약 | 핵심 입력과 해석 |
|---|---|
| `RiskProbability` | 사건 ID, 확률, 확률의 종류, 조건 ID와 설명, 기회 ID와 분모 정의, 실패 정의, 역할, 포함 효과, 방호 ID, 의존 그룹, 근거와 원 추정값 |
| `DemandExposure` | 같은 조건·기회·분모에 대한 연간 요구 횟수와 그 근거 |
| `RiskFrequency` | year^-1의 기대 빈도, 시작 조건, 사건의 물리적 의미인 endpoint, 이미 사용한 사건·방호·의존성·근거 |
| `FaultTreeRequest` | 논리 트리, 서로 다른 기본 사건의 근거표, 독립 또는 공동 상태 모형과 그 근거, 실행 예산 |
| `EventTreeRequest` | 부모 조건 아래의 분기, 다음 조건, 말단 결과, 빠짐없는 분할의 근거 |
| `ProtectionLayer` | 대응 확률 ID, 방호 종류, 독립 근거, 수동 대응의 실행 가능성 |

모든 계약에는 자산·시나리오·시계·자료 기원이 연결된다. 자동으로 다른 자산,
시나리오 또는 조건에 옮기지 않는다. 현재 FTA·ETA·LOPA와 연간 변환의 지원
범위는 요구당 실패확률이다. mission probability와 point unavailability를
요구당 실패확률로 바꾸지 않으며 해당 입력은 계산을 보류한다.

`source_digest`는 원 추정 자료를, 자동 생성되는 `binding_digest`는 연결한
조건·기회·문맥·방호와 계보까지 포함한 전체 계약을 확인한다. 중첩 딕셔너리
수정과 Pydantic `model_copy(update=...)`로 검사를 우회한 변경도 호출 직전에
재검사한다. 해시는 승인 서명이나 근거의 진실성을 입증하는 수단은 아니다.
실제로 검토한 새 조건을 적용할 때는 원 자료와 새 검토를 확인해 새 계약을
생성한다. 기존 해시만 다시 계산해 보류 사유를 지우지 않는다.

## Python에서 기존 HRA 추정값 연결

```python
from pipeline_integrity.risk_exchange import HRARiskClient, DemandExposure

client = HRARiskClient()
# estimate와 event는 기존 HRA 엔진이 만든 객체, context는 RunContext다.
probability = client.bind_human_estimate(
    estimate,
    event,
    condition_id="automatic_isolation_failed",
    opportunity_id="one_manual_isolation_demand",
    context=context,
    protection_ids=("manual_isolation", "manual_recovery", "manual_deadline"),
)
exposure = DemandExposure(
    opportunities_per_year=0.001,
    opportunity_id=probability.opportunity_id,
    opportunity_definition=probability.opportunity_definition,
    condition_id=probability.condition_id,
    evidence_id="reviewed:annual_manual_demand_exposure",
)
result = client.annualize(probability, exposure)
handoff = client.prism_demand(probability, exposure)
packet = handoff.native
record = handoff.record()
```

이 예의 요구 횟수는 예시 값이며 사용자가 검토한 노출 자료로 바꾼다. 연간
기대 횟수는 요구 횟수×요구당 실패확률이다. 한 해에 한 번 이상 발생할 확률과
다르며, Poisson 가정을 자동으로 추가하지 않는다. HRA 원본의 research 상태,
uncertainty, timing/recovery/dependency 포함 여부를 그대로 보존한다.

`protection_ids`는 호출자가 검토한 전체 효과 목록이어야 한다. 이미 포함한
회복이나 시간 효과가 어떤 물리 기능인지 이 연결부가 문장만으로 추론할 수
없다. 같은 기능에는 FTA·ETA·LOPA 사이에서도 같은 ID를 사용한다. 다른 ID로
같은 기능을 숨기면 의미상의 중복을 자동 발견할 수 없다. response 역할의
인적 사건에는 비어 있는 방호 목록을 허용하지 않는다.

PRISM 패킷은 요구 시 실패의 기대 횟수다. 자발적 누설·정상 벤트·봉쇄 상실의
빈도로 바꾸지 않는다. 요구량을 처리하지 못한 공정 상태가 실제 개구·방출로
이어지는 경로는 PI-X01/02의 물리 근거와 별도 계약이 필요하다.

## FTA, ETA, LOPA에서 확인하는 경계

FTA는 같은 사건을 여러 잎에 사용해도 근거표에 한 번만 둔다. 예제의
`A OR (A AND B)`는 원 엔진에서 A의 확률과 같아야 한다. 독립 모형에는
명시적인 근거 ID를 요구하고, 같은 의존 그룹을 가진 사건은 공동 상태가
없으면 보류한다. 공동 상태는 키·중복·합계와 각 기본 확률의 주변확률을
검사한다. 합계 허용오차는 절대 1e-12, 주변확률은 상대 1e-10이다.
생략한 공동 상태는 확률 0을 뜻한다. 최대 기본 사건 20개, 상태·트리 예산과
깊이 제한을 적용한다. 이는 공동 상태 전달이며 공통 원인 모수 추정은 아니다.

`JointStateCountTable`은 같은 grouping unit에서 관측한 완전 Boolean 상태표를
받는다. 각 행은 모든 event ID를 정확히 한 번 포함하고 양의 정수 count를 가지며,
중복 상태는 거부한다. condition/opportunity와 분모 정의, population ID, grouping
unit, evidence ID 및 원 payload SHA를 보존한다. `joint_states_from_counts`는 count
합으로만 정규화하고 사건별 주변확률과 receipt를 반환한다. beta-factor·copula·원인
모수·신뢰구간을 적합하지 않으며 표본 불확실성이나 현장 자격을 주장하지 않는다.

`fault_tree_request_from_joint_counts`는 이 상태표를 기존 `FaultTreeRequest`의
`joint_states`에 결합한다. event ID와 condition/opportunity/분모 정의가 같고 count
근거가 실제 event context에 있어야 한다. 관측 주변확률과 바인딩한 사건 확률이 다르면
기존 `HRARiskClient.fault_tree`가 보류한다. 누락 상태를 독립 또는 0-count 관측으로
자동 보충하지 않는다. 상태는 `research/joint_state_count_adapter_status.json`이다.
`verify_joint_state_count_receipt`은 원 count table로 정규화 결과를 다시 만들며,
FTA 요청은 `joint_state_receipt_digest`를 보존한다. count/source/정규화 의미를
바꿔 digest만 다시 만든 기록도 원표 재생 결과와 다르면 거부한다.

공통원인 모수·검열/노출·표본 불확실성·시간순서/수리 모델은 이 어댑터에 복제하지
않고 [PIPE-EXT-011](owner_requests/PIPE-EXT-011.md)로 HRA 소유 저장소에 요청한다.

ETA의 다음 분기는 바로 그 부모 경로의 조건 ID를 가져야 한다. 자동 차단
실패 후 수동 실패확률을 자동 차단 이전 조건에서 재사용하면 보류한다.
말단에는 성공·무해 결과도 포함하고 원 엔진의 분할 합계 검사를 유지한다.
시작 빈도와 말단 합계를 상대 1e-12로 대조한다. 말단 계보에는 이전 모든
사건과 방호·의존 그룹이 남으며 endpoint는 consequence_outcome이다. 결과
ID가 no_isolation이라고 해서 실제 누설이나 사망 결과를 계산한 것은 아니다.

LOPA는 protection_demand 경계에만 추가 방호를 적용한다. 원 엔진이 독립성,
공유 의존성 또는 수동 실행 가능성 때문에 보류하면 그대로 보류한다. 인적
response를 automatic/passive로 바꾸어 실행 가능성 검사를 피하지 못한다.
반환 빈도의 endpoint는 protection_failure_combination이다. 논리적으로 모든
선언 방호가 실패하는 조합을 뜻한다. 이 조합이 공정 제어 상실로 이어지는지,
다시 봉쇄 상실로 이어지는지는 각각의 물리·시나리오 근거가 필요하다.
정량 독립성은 근거 ID의 존재만으로 입증되지 않으며 사용자가 따로 검토한다.

## 실제 실행의 수치와 한계

HRA가 제공하는 SACADA의 선택된 simulator observed_count 한 건을 사용했다.
NRC 자료의 출처·재사용 조건은 기존 저장소의 고정 메타데이터를 따랐으며
이번 실행에서 원 서버의 권리 조건을 새로 확인했다고 주장하지 않는다.
출처 크레딧과 제삼자 자료 예외를 유지한다. 다른 분야 자료를 배관 현장
빈도로 간주하지 않는다. beta(0.5, 0.5) 사전분포와 분야 전이 검토를 명시해
research 추정값 0.014423076923076924를 연결했다. 독립 시행·현장 보정의
증거는 없고 원 자료에서 회복·시간 효과도 분리할 수 없다.

가정한 시작 빈도 0.01/year와 자동 실패확률 0.1 아래 ETA의 수동 실패 기대
횟수는 약 1.44230769e-5/year다. 동일한 수동 요구 노출 0.001/year를 사용한
HRA 원 연간 변환과 PRISM DemandFailure가 같은 값을 반환했다. 세 경로의
합계 잔차는 약 1.73e-18/year였다. 별도의 가정 LOPA는 5e-5/year이며 현장
위험 수치로 사용하지 않는다. 공유 전원 공동 상태의 AND 확률 0.1도 확인했다.

노출 누락, 잘못된 ETA 조건, 공동 모형 없는 의존성, 회복 중복, 실행 가능성
미확인, 상류에서 사용한 방호의 재사용은 보류 또는 원 엔진의 거부를 확인했다.
단위 검사는 spy를 사용해 이 정책과 위임을 검사한다. 소유 엔진의 수학을
spy로 검증했다고 주장하지 않으며 실제 수치 확인은 별도 실행 기록을 따른다.

## JSON 작업과 후속 개발

`run_risk_job(document, client=None)`과 `pipeline-integrity risk`는 같은 작업을
실행한다. operation은 fault_tree, event_tree, annualize, lopa, prism_demand다.
각 작업의 정확한 입력 키는 함수와 Pydantic JSON schema에서 확인한다.
Python 예제는 직접 작성한 가정으로 전체 FTA 작업을 생성하며, 해시를 가진
JSON을 손으로 편집하는 대신 원 파라미터로 새 계약을 생성하는 방법을 보여준다.

계속할 작업은 실제 배관 작업의 요구·오류·회복·노출 및 grouped 공동 상태 자료 확보,
소유 위험 엔진의 공통 원인 모수/불확실성 추정, 동적 사건과 밸브 완료·잔류 재고의 연결, 물리적인 봉쇄 상실 경로,
외부 위험 서비스의 원 계보 수용이다. 다른 엔진의 복제 없이 PI-X02/03/06을
이 입력·출력으로 확대한다. 전체 과제의 완료 상태는 STATUS와 기술 목록을 따른다.
