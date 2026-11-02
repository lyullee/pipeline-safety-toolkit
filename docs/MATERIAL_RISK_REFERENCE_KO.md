# 기존 위험 엔진과 재료 신뢰도 원시 기능의 수치 대조

생산 위험 계산은 기존 `risk_exchange.HRARiskClient`와 `period_risk`를 사용한다. 재료 엔진에도 있는 `reliability.independent_gate`, `reliability.exclusive_paths` 때문에 새 FTA·ETA 구현을 만들지 않았다. 기존 `prepare_material_job`과 `evaluate_material_job`으로 두 원시 기능을 호출해, 원 응답과 기존 위험 API의 숫자를 대조하는 실행 예제를 추가했다.

이 비교는 동일하게 작성한 조건과 숫자의 수학 확인이다. 재료 원시 기능의 입력·출력만으로 사건의 물리 범위, 운전 기간, 생존 조건, 독립성 또는 실제 인적오류율을 얻을 수는 없다.

## 재사용 API

`examples/material_risk_reference.py`의 make_cases는 재료 원 입력과 별도로 선언한 `RiskProbability`, `FaultTreeRequest`, `RiskFrequency`, `EventTreeRequest`, RunContext를 반환한다. 사용자가 다른 값을 검토한 경우 기존 공개 계약을 사용해 새로운 요청을 만들면 된다. 새 게이트 클래스나 병렬 위험 프레임워크는 필요 없다.

```python
import runpy
from pipeline_integrity.contracts import digest

parameters, gates, rate, tree, context = runpy.run_path("examples/material_risk_reference.py")["make_cases"]()
assert parameters["OR"]["independence_declared"] is True
assert gates["OR"].events[0].source_kind == "declared"
assert gates["OR"].events[0].source_digest == digest(gates["OR"].events[0].source_payload)
assert not rate.observed_annual_frequency
assert len(tree.branches) == 3
```

재료 independent_gate의 parameters는 probabilities, gate(AND/OR), independence_declared다. 원 ModelSpec의 probability 의미는 `unscoped_event_probability`다. 이 값을 단위가1이라는 이유로 mission_probability, demand_failure_probability, POD 또는 HEP로 재표시하지 않는다. 별도의 사건·조건·기회·공통 기간 검토가 필요하며 기존 위험 계약을 따른다.

재료 exclusive_paths의 parameters는 initiating_frequency_per_s와 conditional_probabilities다. 조건부 경로는 서로 배타적이고 전체를 구성해야 하며 합은1이다. 원 출력 path_frequencies_per_s의 단위는1/s다. 예제에서는 1년을 명시적으로31,536,000초로 선언하고 같은 빈도를 기존 연간 위험 API와 비교했다. 이는 실제 달력 계산이나 관측된 반복 사고 빈도가 아니다. 기대 횟수/빈도와 기간에 한 번 이상 발생할 확률도 구분한다.

## 확인한 반례와 경계

서로 다른 독립 사건 A=0.1, B=0.2의 AND=0.02, OR=0.28을 두 원 엔진에 대해 확인했다. 반대로 동일 사건 A를 OR에서 두 번 참조하면 기존 FTA는0.1을 유지한다. 입력 두 개를 독립 복사본으로 해석하는 재료 원시 기능의 OR는0.19다. 이 차이는 의도적으로 보존하며 동일한 사건 ID를 확률 두 개로 늘리지 않는다.

독립성 미확인은 재료 모델의 needs_data로 남는다. 기존 위험 API에서 공동 상태가 필요한데 없으면 withheld로 남는다. 미상 확률을0으로, 불완전한 경로 합을 정규화하여1로 바꾸지 않는다. 명시 영/단위 확률, 영 시작 빈도와 원3경로의 빈도 보존을 확인했다.

실제 두 재료 모델의10응답과 기존 위험 API5사례, 독립 작성 기준16개·거부4개를 확인했다. 범위를 붙이지 않은 원 재료 확률에서 scoped RiskProbability를 자동 생성하지 않았고, 기존 HRA·FTA·ETA 소유자는 유지했다. 공개 집계는 `research/material_risk_reference_results.json`, 상세 원 receipt와 기존 위험 결과는 `artifacts/material_risk_reference`에 있다.

```powershell
python scripts/check_material_risk_reference.py --materials PATH_TO_PINNED_MAT --hra PATH_TO_HRA
```

원 Mat은 별도 MIT snapshot76Python소스, treeSHA `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`를 사용했다. 실제 HRA 체크아웃과 함께 실행 전후 revision/tree hash, 원 Mat의 LICENSE/고지/JSON 자원을 비교했다. 공개 기록의 해시를 따르며 개인 경로를 공개하지 않는다. 원 소스를 이 저장소에 복제하지 않았다.

PI-X02/04의 후속 수신 계약은 사건 ID·공통 기회·기간/생존·조건·분할 근거·차원/무차원과 중복 효과를 유지하는 것이다. 숫자가 같다는 것만으로 사건 의미와 근거가 같다는 판정을 내리지 않는다.
