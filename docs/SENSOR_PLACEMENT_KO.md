# 공동 잡음을 보존한 유한 센서 배치

이 단계는 구현과 테스트 작성 상태다. 사용자의 지시에 따라 로컬 pytest/벤치마크를
실행하지 않았으며 실제 수치·통합 검증은 누리온 실행을 기다린다. Ruff/compile
검사는 계산 검증이나 전체 기술 완료를 뜻하지 않는다. 전체 배치 기술은 부분/검증
대기 범위로 유지해야 한다.

`sensor_placement.plan_sensor_placement(request, context)`는 같은 물리 운전점에서
검토된 forward Jacobian·모수 척도·전체 공동 측정 잡음과 유한 후보를 받는다.
선택한 센서들의 비가산 공동 정보 지표를 계산하고 기존 `scheduling`의 예산·
관측창 자원·달력/정수 CP-SAT 선택에 연결한다. 센서별 독립 점수나 새 최적화기를
만들지 않는다. 성능/탐지 확률·HEP·누설 원인은 추정하지 않는다.

## 입력과 통합

`SensorPlacementRequest`의 parameters/measurements는 기존 `MeasurementBasis`를
사용한다. 양은 coherent SI로 먼저 바꾸고 압력에는 absolute/gauge/differential
기준을 선언한다. Jacobian 행은 측정 ID, 열은 모수 ID와 정확히 일치하며 숫자는
해당 원 SI 출력/입력 단위의 미분이다. 모델 ID/버전·실제 source/configuration/
linearization-point hash와 근거를 보존한다. 이 도구가 외부 forward 모델을 보정하거나
이 미분을 임의의 운전점으로 전이하지 않는다.

각 후보는 실제 location/frame/좌표 또는 물리 port 대응, 관측창 resource 사용과
정수 quote/count budget을 갖는다. 모든 budget/resource에는0·양의 사용량·미상을
명시한다. 키 누락을 무상/무자원으로 가정하지 않는다. 금액의 정수 단위 정의도
입력한다. 이는 설치 공정의 공기/재실/자원 일정이 아니라 한 공동 관측창의 배치다.
시간은 기존 scheduler tick grid를 사용한다.

`boundary_metadata(request)`를 context의 `sensor_placement_boundary`에 넣는다.
자산·시계·관측창·후보 기하·측정/모수 기반·미분/잡음을 함께 결속한다. 모델/잡음/
기하/단위/관측창·budget의 검토5개와 실제 근거가 없으면 native 계산 전에 needs_data다.

## 정보와 선택의 의미

현재 잡음 법칙은 한 관측창의 모수 비의존 Gaussian 공동 공분산이다. 선택 부분
행렬의 off-diagonal을 유지한다. 알려진 전체 행렬이 있으면 먼저 SPD를 확인한다.
특이/음의 잡음을 작은 수·대각선·pseudoinverse로 고치지 않는다. 미상 상관은
독립이 아니다. 표준편차로 정규화한 correlation의 Cholesky와 native triangular
solve/SVD를 사용하며, 원 모수의 reference scale로 무차원 좌표를 구성한다.

모든 선언 모수를 함께 식별할 full local rank가 필요하다. Tikhonov/prior를
몰래 넣지 않는다. 결과의 normalized information/inverse-information은 선언한
국소 선형/잡음 모델에 조건부인 행렬이다. 현장 추정 공분산·사후분포나 검출 성능이 아니다.

| criterion | 최소화할 유한 메뉴 loss |
|---|---|
| D_opt | 음의 log determinant information |
| A_opt | trace inverse information |
| E_opt | 음의 최소 information eigenvalue |

모든 subset은 명시한 작업 budget 안에서 평가하거나 caller가 유한 메뉴를 제공한다.
한 전체 subset을 단일 관측 operation의 mode로 컴파일한다. loss 순위의 정수 비용은
그 메뉴들의 순서를 그대로 보존한다. 목적함수를 센서별 합으로 분리하거나 실수
점수를 임의 양자화하지 않는다. 동일 native loss에는 선언된 subset ID가 재현용
순서를 준다. 이는 물리적으로 우월한 tie-break의 근거가 아니다.

미상 sensitivity/quote/noise 메뉴나 미완성 joint covariance가 있으면 명목 선택을
남겨도 needs_data이며 전역 최적을 주장하지 않는다. `finite_menu_proven_optimal`
은 검토된 유한 범위, `global_design_proven_optimal`은 실제 모든 후보 subset을
평가한 경우만 참이 될 수 있다. 두 값 모두 해당 국소 수치 모델과 자원 제약에 한정된다.

## 재생과 검증 대기

```python
from examples.sensor_placement import make_case
from pipeline_integrity.sensor_placement import plan_sensor_placement

request, context = make_case()  # 기본 검토 전, solver import/실행 없음
record = plan_sensor_placement(request, context)
assert record["status"] == "needs_data"
```

검토된 작성 예제는 `make_case(reviewed=True)`로 구성한다. 실제 배치 권고/제조사
자료가 아닌 가상3포트의 감도·공분산·quote다. `verify_sensor_placement_record`는
현재 원 요청/잡음/정보·공유 scheduler 의미를 재실행한다. 파생 scheduler semantic
snapshot은 재현되지 않는 wall time만 생략하고 별도 digest를 구성하며 원 실행의
서명/시각적 이력 증명을 주장하지 않는다. native 시간제한으로 해/상태가 달라지면
이 replay는 과거 성공을 강제로 통과시키지 않는다.

`tests/test_sensor_placement.py`는 상관이 큰 두 센서보다 덜 정밀한 독립 후보와의
조합이 더 유용해지는 별도 스칼라 기준, 세 정보 지표·척도·rank/누락·자원 달력/
필수 센서/예산·재해시 반례, 실제 CP-SAT와 유한 mode 테스트 oracle을 준비했다.
**이 코딩 단계에서는 실행하지 않았다.** 누리온 sharding 대상에 포함하고 실행
로그/JUnit을 얻은 뒤에만 통과 수를 기록한다.

기술 API는 [SciPy Cholesky](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.cholesky.html),
[triangular solve](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.solve_triangular.html),
[SVD](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.svd.html)와
[공유 CP-SAT](https://developers.google.com/optimization/cp/cp_solver/)를 따른다.
설명·예제/테스트는 독립 작성했다.
설계 지표의 모델 의존성은 [NIST의 유한 실험 설계 설명](https://www.itl.nist.gov/div898/handbook/pri/section5/pri52.htm)도
참고한다. 이 구현의 공동 잡음 처리는 원 covariance를 명시적으로 whitening한
선언 모델에 한정되며 데이터에서 그 잡음 법칙을 추정하거나 검증한 것은 아니다.

후속은 원 forward 모델과 검증된 감도 export, nuisance/목표 모수 marginal 정보,
비선형·운전점/고장모드·복수 창의 공동 잡음과 drift·실제 기하/자원·관측/오경보
자료다. 큰 후보 문제의 sparse/convex·정수 설계 solver와 시간/계측자원은 소유
모니터링/역문제/작업 엔진에 확장 요청하며, 자료가 없는 prior/POD를 채워 넣지 않는다.
