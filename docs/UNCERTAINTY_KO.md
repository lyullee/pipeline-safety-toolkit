# 공동 불확실성 전파

`pipeline_integrity.uncertainty`는 같은 표본을 여러 계산에 전달한다. 각 계산의
입력·단위·상태·원 응답과 공통 draw ID를 보존하며 미계산 비중을 지우지 않는다.
확률분포 추정이나 현장 오류율 추정을 자동으로 수행하지 않는다.

## 설치와 역할

명시한 공동 사례의 평가는 핵심 의존성 NumPy/Pydantic만 사용한다.
Monte Carlo·Latin hypercube·scrambled Sobol과 역분포는 선택 설치
`pip install ".[engineering]"`의 SciPy를 호출한다. 별도 확률 엔진을 복제하지 않는다.
미검토 정의나 미상 분포는 SciPy를 불러오기 전에 `needs_data`를 반환한다.

| 계약/함수 | 역할 |
|---|---|
| `UncertainVariable` | ID·단위·measurement/input/model 층·근거·주변분포 |
| `Marginal` | constant/normal/lognormal/uniform/triangular/beta의 정확한 파라미터 |
| `JointCase` | 행 ID·원 짝을 유지한 값/결측·명시한 비중·근거 |
| `EnsembleRequest` | 방법·해석·의존성·표본 수·seed·검토 상태 |
| `generate_ensemble` | 봉인된 공동 표본 설계 |
| `EnsembleMetric` | 결과 의미·단위·선언 지지 범위·선택 임계값 |
| `EnsembleValues` | 각 스칼라의 값·상태·단위·동일 context·native 응답 |
| `EnsembleNode` | Python callable·버전·구현 SHA-256·설정·출력 정의 |
| `evaluate_ensemble` | 공유 표본 전달·가중 통계·쌍별 공분산·미계산 비중 |
| `verify_ensemble_result` | 원 design과 같은 deterministic node/provider로 전체 draw·요약 재생
| `registry_node` | 기존 등록 계산의 최상위 스칼라 입력 연결 |

## 해석과 분포

`interpretation`은 `scenario`, `epistemic`, `aleatory` 중 하나를 명시한다.
시나리오의 동일 가중치는 모집단의 발생확률을 뜻하지 않는다. 인식론적 HEP
분포의 평균도 실제 관측 오류율이 아니다. 한 설계 안에 혼합한 불확실성의
측정/입력/모델 층은 메타데이터이며 자동 분산 분해나 중첩 추론을 제공하지 않는다.
물리 시간·자산·시나리오는 context에 유지하고 표본 ID는
`context.metadata['ensemble']`에 둔다. 표본 순서를 SignalFrame 시계로 바꾸지 않는다.
이미 ensemble 좌표가 있는 context는 현재 API에서 거부한다.

모든 변수·정의·행·결과·임계값의 evidence ID는 context에 있어야 한다.
`definition_reviewed=False`가 기본이다. 분포를 선택했다는 이유만으로 실제
공정의 적합성이 검증되지는 않는다.

| family | 파라미터 | 의미 |
|---|---|---|
| constant | value | 입력 단위의 상수 |
| normal | mean, standard_deviation | 입력 단위의 평균/양의 표준편차 |
| lognormal | log_mean, log_standard_deviation | 자연로그 공간의 모수; 실제 단위 기준 수치의 로그 |
| uniform | lower, upper | 입력 단위의 유한 구간 |
| triangular | lower, mode, upper | 구간과 그 안의 최빈값 |
| beta | alpha, beta, lower, upper | 양의 형상 모수와 입력 단위의 구간 |

주변분포의 키를 추정하거나 유사한 이름으로 바꾸지 않는다. 정규분포가 음수
유량을 생성하면 물리 모델에서 그 표본을 보류한다. 양수 결과만 재추출하거나
분포 꼬리를 잘라서 정상 표본처럼 사용하지 않는다. 로그정규 scale과 역분포가
부동소수점으로 표현되지 않으면 설계를 `out_of_scope`로 반환한다.

## 의존성과 표본 설계

`joint_cases/paired_rows`는 행의 원 짝을 그대로 유지한다. 비중 합은 절대
1e-12 이내에서 1이어야 하고 실제 합도 결과에 남긴다. 자동 정규화는 없다.
각 행에 모든 변수 키가 있어야 하며 미상은 명시적으로 `None`이다.

확률 표본에는 양의 `samples`와 명시한 정수 `seed`가 필요하다. `independent`는
정의에서 독립을 선언한 경우에만 사용한다. `gaussian_copula`는 변수 순서와
같은 **잠재 표준정규 공간 상관행렬**을 받는다. 비정규 주변분포의 실제 Pearson
상관과 동일하다고 해석하지 않는다. 상관을 자료에서 자동 추정하지 않는다.

행렬의 대칭·단위 대각·[-1,1]·양의 준정부호를 검사한다. 대칭 제곱근으로
특이한 ±1 상관도 지원한다. 선언 허용오차 이내의 음의 고유값만 0으로
처리하며 개수·최소값·행렬 복원 잔차·제곱근을 결과에 공개한다. 허용오차는
최대 1e-10이고 기본 1e-12다. 유의한 음의 고유값을 가진 행렬은 고치지 않는다.

Sobol은 `scramble=True`와 `random_base2`를 사용한다. 표본 수는 2의 거듭제곱이며
처음 표본 생략·간격 추출·후처리 최적화를 하지 않는다. LHS는 기본 strength 1이다.
copula 적용 후 LHS의 원 주변층화 성질은 보장하지 않는다. 단일 설계의 표본들을
독립 시행으로 취급하는 이항 신뢰구간이나 Monte Carlo 표준오차는 출력하지 않는다.
재현 시 NumPy/SciPy 버전과 bit generator도 맞춰야 한다.

## Python 사용

실행 가능한 전체 예제는 `examples/coupled_uncertainty.py`다.

```python
import runpy
from pipeline_integrity.uncertainty import generate_ensemble, evaluate_ensemble

example = runpy.run_path("examples/coupled_uncertainty.py")
node, context = example["make_node"]()
request = example["make_request"](samples=512)
design = generate_ensemble(request, context)
result = evaluate_ensemble(design, (node,))
assert result["status"] == "computed"
cost = result["summaries"]["pump_lifecycle"]["keep_present_cost"]
print(cost["full_design_mean"], cost["unresolved_weight"])
```

등록 계산에는 `registry_node(registry, id=..., model_id=..., base_inputs=...,
bindings={'flow': 'volume_flow_m3_s'}, variables=..., metrics=...)`를 쓴다.
변수 ID를 최상위 수치 입력에 연결하며 단위 문자열까지 정확히 같아야 한다.
압력 기준 변경·자동 단위 환산·문자열 수식 실행은 없다. 준비 시 유효한
base_inputs로 실제 구현을 확인하므로 이 준비 호출도 모델을 실행한다.
구성 오류·선택 엔진 미설치 등은 연구 설정 오류로 드러낸다.

중첩 계약·여러 모델·외부 엔진은 `EnsembleNode`의 callable로 연결한다.
`evaluate(values, context)`는 `EnsembleValues`를 반환해야 한다. 원 엔진의
상태와 근거를 `native_payload`에 보존하고 값마다 상태를 지정한다. 하나의
출력이 미상이어도 다른 출력이 계산되었으면 그 차이를 표현할 수 있다.
미상 입력이 다른 모델에 무관하다면 해당 모델은 계산을 계속할 수 있다.
전체 변수 중 한 개가 결측이라고 모든 노드를 자동 보류하지 않는다.

callable의 예기치 않은 예외는 전파한다. 계산 실패를 무조건 잡아서 표본을
삭제하는 정책을 제공하지 않는다. 잘못된 물리 입력을 보류하는 처리는 모델
계약에서 명시해야 한다. 노드가 반환한 자산·시나리오·시계·draw context·출력
이름·단위가 다르면 설정 오류로 중단한다. 봉인 해시와 구성 변경도 검사한다.
해시는 변경 탐지용이며 전자서명이나 과학적 타당성 보증은 아니다.

## 결과의 미계산 비중

| 필드 | 해석 |
|---|---|
| known_weight | 유효 계산 결과가 있는 원 비중 |
| unresolved_weight | needs_data/out_of_scope인 원 비중 |
| known_weighted_sum | 알려진 결과의 원 비중 가중합 |
| conditional_on_valid_mean | 유효 비중만 조건으로 한 평균 |
| full_design_mean | 모든 양의 비중이 계산된 경우의 평균 |
| conditional_on_valid_quantiles | 유효 비중의 NumPy inverted_cdf 분위수 |
| full_design_mean_bounds | 선언 지지 범위와 미계산 비중으로 계산한 평균 경계 |
| threshold_weight_bounds | 알려진 임계값 만족 비중~그 비중+미계산 비중 |

0 비중 행은 명시적으로 skipped로 남기고 계산하지 않는다. 양의 비중의 실패는
없애거나 재추출하지 않는다. 선언한 지지 범위를 계산값이 벗어나면 원값을
보존하고 `out_of_scope`로 분류하며 해당 범위에 근거한 평균 경계를 취소한다.
conditional_probability는 단위 `1`과 명시한 [0,1] 범위를 요구한다.
expected_count_rate는 확률과 구분하며 1을 넘을 수 있다.

임계값 결과는 유한 설계의 비중 경계다. 현장 파손확률·기간 최대값·반복 사건
빈도로 승격하지 않는다. 쌍별 공분산은 두 출력이 동시에 유효한 원 행에
조건을 둔 가중 모집단식이다. 사용한 비중과 단위 쌍을 함께 반환하며 서로 다른
행을 임의로 결합하지 않는다. 미계산이 있는 공분산은 전체 분포의 공분산이 아니다.

## 실행 근거와 공개 데이터 적용

`python scripts/check_uncertainty.py --hra PATH_TO_HRA`는 다음을 확인한다.

1. 정규 유량/양정의 상관 0과 0.8에 대해 각각 Sobol 4096개를 기존 펌프 계산에
   전달한다. 독립 수식 E[QH]=E[Q]E[H]+Cov(Q,H)의 전력 평균과 비교한다.
2. 공유 512개 표본의 펌프→선언 전력 배율→기존 에너지/생애주기 계산을 실행한다.
   기존 HRA의 demand_frequency를 실제 호출해 가정 HEP와 연간 기회 30회를
   기대횟수로 바꾸고 조건부 손실 25REF를 동일한 두 대안에 연결한다.
   추가 비용이 같으므로 대안의 비용 차이가 유지되는지도 검사한다.
3. UCI447의 EPS1 100Hz·FS1 10Hz를 0/100/1100/2204회차별로 읽고 원 ZIP의
   수치 행 8개와 비교한다. 공통 [0,59.9]초만 사용한다. 전력 23964표본과
   유량 2400표본을 각 원 시계에서 적분한 에너지/체적의 행 짝을 보존한다.
4. 네 사례의 동일 가중 시나리오와, 짝을 풀어 16개 조합을 만든 명시적 반사실을
   비교한다. 후자는 실제 운전자료나 독립성 추정 결과가 아니다.
5. 한 체적을 미상으로 만든 사례에서 0.25 비중과 임계값 경계가 유지되는지 확인한다.

유압유 자료를 물 물성으로 바꾸거나 압력 기준을 추정하지 않는다. 센서 시계는
병합·재표본화하지 않는다. 네 회차를 모집단 표본·현장 오류 기회로 사용하지 않는다.
원자료와 회차별 변환 기록은 로컬 `artifacts/uncertainty`에만 남긴다.
공개 요약 `research/uncertainty_results.json`에는 집계·출처·해시·범위만 포함한다.

## 채택 근거와 남은 범위

[SciPy Sobol](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.Sobol.html)의
균형 조건과 [LHS](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.LatinHypercube.html)의
설계를 확인해 기존 선택 의존성을 사용했다. 가중 분위수는
[NumPy quantile](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html)의
inverted_cdf를 호출한다. 이 문서는 독립적으로 작성했으며 라이브러리 소스를 옮기지 않았다.
잠재 정규 확률 변환은
[statsmodels GaussianCopula 공식 설명](https://www.statsmodels.org/stable/generated/statsmodels.distributions.copula.api.GaussianCopula.html)도
대조했다. 현재는 QMC의 명시적 좌표에 기존 SciPy의 cdf/ppf를 적용하며
추가 copula 적합 라이브러리를 설치하거나 그 소스를 복사하지 않았다.
실험 자료는 Helwig·Pignanelli·Schütze의
[UCI447, DOI 10.24432/C5CW21](https://archive.ics.uci.edu/dataset/447/condition+monitoring+of+hydraulic+systems),
CC BY 4.0을 따른다. 원 배포물과 파생 회차 행은 코드 릴리스에 포함하지 않는다.

`uncertainty.coupled`는 partial이다. 관측 학습 행의 normal/lognormal과 잠재 상관
적합·별도 구간 평가는 [UNCERTAINTY_FIT_KO](UNCERTAINTY_FIT_KO.md)에 추가했다.
분야별 분포/전이·시간 의존·
공통원인 사건·중첩 aleatory/epistemic 추론·분산 기여도·희귀사건 오차·재료 결함과
전체 사고 결과 연성·현장 검증은 남는다. 일반 callback 제공을 이 항목 전체의
완료로 집계하지 않는다. 소유 엔진의 추가 계약은 PI-X02/04/05/06으로 요청한다.
