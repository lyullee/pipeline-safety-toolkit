# 공통 기간의 사건 확률 연결

`period_risk`는 여러 설비의 기간 사건을 기존 HRA Boolean 엔진으로 연결한다.
원 `RiskProbability`, `FaultNode`, `JointState`와 HRA의 독립/공동 상태 계산을
재사용한다. 새로운 FTA 계산기나 수명·수리·연간 빈도 모델을 만들지 않았다.
기존 demand 전용 `HRARiskClient.fault_tree` 및 `annualize`의 계약은 유지한다.

## 재현

HRA 프로젝트가 Python에서 import 가능하면 다음 예제를 실행할 수 있다.
설치와 소유 소스 경로는 INTEGRATION_KO 및 CONTINUATION_KO를 따른다.

```python
import runpy
from pipeline_integrity.period_risk import evaluate_period_logic

cases, context = runpy.run_path("examples/period_risk.py")["make_cases"]()
result = evaluate_period_logic(cases["joint_or"], context)
print(result["status"], result["probability"]["probability"])
```

작성한 공동 상태 사례는 `exchange_checked`, 0.25를 반환한다. 공개 시편 자료까지
포함한 별도 검증은 프로젝트 루트에서 다음 순서로 실행한다.

```powershell
python scripts/acquire_data.py --only cmsx6_creep --workers 1 --maximum-seconds 90
python scripts/check_creep.py --materials PATH_TO_MAT --hra PATH_TO_HRA
python scripts/check_period_risk.py --materials PATH_TO_MAT --hra PATH_TO_HRA
python -m pytest -q tests/test_period_risk.py
```

마지막 스크립트는 앞 단계가 생성한 `artifacts/creep/primary_censored_fit.json`의
요청을 읽고, 새로 checksum을 확인한 workbook의 시편/응력/온도/종료/검열과
대조한 뒤 원 적합기를 다시 호출한다. 캐시의 예측 숫자만 재사용하지 않는다.
전체 요청·receipt는 로컬 `artifacts/period_risk`, 공개 집계와 실행 해시는
`research/period_risk_results.json`에 남긴다.

## 계약과 커스터마이징

| 계약/함수 | 역할 |
|---|---|
| `RiskPeriod` | 시계·정확한 시작/종료·공통 조건의 ID와 설명·근거 |
| `PeriodEventBinding` | 변경하지 않은 원 확률과 물리 범위/endpoint·원 시간창·시스템 연결 검토 |
| `PeriodLogicRequest` | AND/OR/NOT/VOTE 구조·사건 목록·상위 사건 정의·의존성·검토/자원 예산 |
| `bind_creep_period_event` | CREEP_KO의 완전한 조건부 파단 기록을 기간 사건으로 연결 |
| `evaluate_period_logic` | 계약과 근거를 점검하고 원 HRA를 호출해 연구 수준 결과 반환 |

`PeriodEventBinding.probability`에는 원 `RiskProbability`를 그대로 넣는다.
자산·scenario가 다른 사건은 `target_context_digest=digest(context)`로 명시한
시스템에 연결한다. `target_period_digest=digest(period)`는 조건 설명까지 포함한
전체 기간 정의를 고정한다. `mapping_evidence_ids`와
`period_asset_and_common_conditioning_reviewed`를 별도로 제공한다.
기본 검토 값은 False다. 원 확률의 조건이나 출처를 수정해 검토를 대신하지 않는다.

원 근거 ID는 원 context와 목표 context에 같은 내용으로 등록해야 한다.
기간·매핑·논리·의존성 근거도 목표 context에 있어야 한다. 원/목표 시계는
같아야 하며 초의 시간창은 정확히 일치해야 한다. 다른 시계의 offset·단위 변환,
기간 비례 조정, 조건 미상의 기본값은 이 API가 자동 수행하지 않는다.

기간이나 공통 조건·검토가 맞지 않으면 `withheld`, probability=None이다.
변조·근거 누락·잘못된 자산/기간 해시·시계·트리/상태 예산 오류는 ValueError다.
이 점검은 선택 의존성 import 전에 실행한다. 유효한 계산 요청에는 별도로
설치한 HRA 엔진이 필요하다. `client=`에 기존 `HRARiskClient` 또는 같은
명시적 api/provider 인터페이스를 주입할 수 있다.

## 생존 조건과 사건의 의미

같은 시간창의 두 수명이 각각 자기 설비의 생존만 조건으로 사용했다면,
두 수치를 곧바로 시스템의 공통 생존 조건 아래 확률로 볼 수 없다. 예를 들어
공유 환경이나 미지 공통 모수가 있으면 다른 설비의 생존 정보도 예측을 바꿀 수
있다. 적용자는 공통 초기 상태의 정의와 그 조건으로 원 확률을 사용할 수 있는
근거를 검토해야 한다. 조건이 달라지면 원 모델에서 다시 예측한다.

`bind_creep_period_event`는 원 크리프 예측·MaterialReceipt·나이/기간 매핑을
다시 확인한다. 물리 범위·크리프 파단 endpoint·원 기간을 다른 값으로 덮어쓰지
않는다. 같은 학습 수명 모델의 의존 그룹과 원 연구 자격도 보존한다.

이 계산은 선언 기간에 사건들이 발생했는지의 정적 Boolean 결합이다. A AND B는
그 기간 중 두 사건이 모두 발생한다는 뜻이다. 두 설비의 동시 불능 시간,
발생 순서, 수리/재가동, 반복 고장 또는 그 결과의 누설을 계산한 것은 아니다.
상위 `top_endpoint_id`, `top_definition`과 `event_logic_reviewed`로 논리의 물리적
의미를 별도로 명시한다. 원 수명분포와 점추정의 한계는 그대로 남는다.

## 공유 사건과 의존성

트리의 같은 사건 ID는 여러 번 나와도 동일한 사건이다. 사건 표에는 한 번만
제공한다. 작성 기준 `A OR (A AND B)`는 A와 같으며 확률0.2다. 반복 등장한 A를
독립 사건처럼 다시 곱하지 않는다. 같은 물리 사건을 다른 ID로 제공하거나,
집계 사건과 그 집계에 이미 포함된 원 사건을 다시 넣으면 보류한다. 이 경우
원 사건으로 트리를 펼쳐 하나의 논리로 표현한다. 같은 방호 효과의 중복도 보류한다.

`declared_independent`에는 독립성 근거가 필요하다. 공유 dependency group이
있으면 계산하지 않는다. 독립 선언과 공동 상태를 동시에 줄 수도 없다.
`joint_states`는 기존 `JointState(states={사건ID: bool}, probability=...)`를 사용한다.
각 행에 모든 사건의 상태를 주고 중복 상태 없이 가중치 합1을 제공한다.
표에 없는 상태는 확률0이라는 선언이므로 그 의미를 근거와 함께 검토한다.
주변확률은 각 원 확률과 일치해야 한다. 원 모델이 공유된다는 이유만으로
상관계수나 공동 상태를 자동 추정하지 않는다.

출력은 `RiskProbability(measure="mission_probability", qualification="research")`다.
전체 입력·원 출처·근거·공유 그룹·이미 포함한 원 사건/물리 범위·기간을 보존한다.
그 집계 사건을 다시 사용할 때 원 공통 기간/조건을 바꿀 수 없다. 해시는 변경
탐지용이며 출처의 진위·가정의 타당성을 인증하지 않는다.

최대20개 서로 다른 사건의 원 열거 기능을 사용한다. 기본 상태 예산은1,000,000,
20독립 사건을 평가하려면 명시적으로1,048,576까지 늘려야 한다. 트리는 기본
10,000노드·깊이64로 제한한다. 이 경계에서 BDD나 대규모 동적 분석을 구현했다고
주장하지 않는다. 원 엔진의 추가 backend를 사용할 때는 그 자원 정책과 의존성을
별도로 연결해야 한다.

## 검증과 공개 자료 적용

작성한 독립 A=0.2/B=0.1 사례의 OR=0.28, AND=0.02, NOT(OR)=0.72,
1-out-of-2=0.28과 공유 사건=0.2를 원 엔진에서 확인했다. 공동 상태의
P(A AND B)=0.05 사례는 OR=0.25다. 독립 기준은 간단한 사건 대수로 작성했으며
새 확률 계산기로 원 엔진 결과를 대체하지 않았다.

공개 CMSX-6 원12시편9파단/3우측 검열을 다시 적합하고, 동일 재료의 가상
두 부품에1253.15K·200/201MPa를 선언해400000~600000초의 조건부 확률을 구했다.
이 부품과 공통 생존/새 재료 원점은 작성한 연구 가정이다. 실제 시험편 두 개의
동시 운전 관측으로 취급하지 않는다. 공유 학습 모델의 독립 계산은 보류했다.

이후 두 주변확률에 대해 겹침이 최대/최소가 되도록 선언한 두 공동 상태를
각각 원 엔진에 전달했다. 이들은 관측에서 추정한 의존성이 아닌 별도 가정
시나리오다. 결과는 고정 적합 모수 아래의 시나리오이며 모수 불확실성이나
실제 시스템 신뢰도 검증을 포함하지 않는다. 출처·권리·원본 무결성·한 온도의
식별 한계는 CREEP_KO와 DATA_KO를 따른다. 원/변환 관측 행은 배포에서 제외한다.

31개 새 자동 검사로 시간창·조건/해시·근거·다른 확률 의미·공유 모델·주변확률·
중복 사건·방호·예산과 크리프 source 매핑을 확인했다. 원 Mat/HRA는 실행 전후
해시가 같았다. 기존 demand 연간 환산은 새 상위 사건도 보류했다.

다음 연구는 관측 공동 의존성과 모수 불확실성, 공통 생존 조건의 추정·검증,
시간 순서·수리·물리 봉쇄 상실 모델이다. 이 정적 기간 연결만으로 공통 원인
모수나 현장 위험 평가를 완성했다고 집계하지 않는다.
