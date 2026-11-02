# 압력 이력에서 피로 손상으로 연결하기

`fatigue_exchange`는 공정 압력 이력을 기존 재료 프로젝트의
`workflow.pressure_fatigue`로 전달한다. 응력 변환·레인플로·수명·손상 누적은
matintegrity가 계산한다. 이 패키지는 형상·곡선·시계·근거·원 응답을 연결한다.
재료 엔진을 복제하지 않으며 계산된 손상에서 실패확률을 생성하지 않는다.

## 설치와 재현

matintegrity는 별도로 설치한다. 사용한 로컬 소스에는 Git 커밋이 없어 실제
소스 트리와 해당 모듈의 SHA-256을 기록했다. 임의의 공개 커밋으로 표시하지
않는다. 원 엔진의 의존성 rainflow 3.2.0도 실제 실행 환경과 해시를 보존한다.

```powershell
python -m pip install -e PATH_TO_MAT
python scripts/check_pressure_fatigue.py --materials PATH_TO_MAT
python examples/pressure_fatigue.py --write-job examples/pressure_fatigue.json
pipeline-integrity pressure-fatigue examples/pressure_fatigue.json --output artifacts/fatigue/result.json
```

전체 실제 호출의 요약은 `research/pressure_fatigue_results.json`, 입력·원 응답·
주기별 행은 로컬 `artifacts/fatigue`에 있다. 원 관측은 배포에서 제외한다.
설치·데이터 취득은 CONTINUATION과 DATA의 절차를 따른다. 이 API의 계약은
핵심 의존성으로 읽을 수 있지만 계산에는 별도 재료 엔진이 필요하다.
입력 근거가 미확정이면 선택 엔진을 호출하기 전에 needs_data를 반환한다.

## 입력 계약

| 객체 | 주요 입력과 의미 |
|---|---|
| PressureHistory | 자산·위치·시나리오·시계, 증가하는 경과 초, Pa 압력·기준, K 온도·valid, 두 수량의 기원, 출처·원 해시 |
| FatigueGeometry | 평균 반경·관벽 두께 m, 선언한 두께/반경 상한, 근거 ID |
| FatigueCurve | 기준 응력 **범위** Pa, 기준 주기 수·지수, 적용 가능한 응력 범위·온도·응력비 구간, 곡선 근거·종류 |
| PressureFatigueRequest | 이력·형상·곡선, 외부 절대압, 초기 손상·선언 손상 한계와 각각의 근거, 최대 표본 수 |
| RunContext | 같은 자산·시나리오·시계와 모든 입력 근거 |

평균 반경은 유체 반경과 다르다. 예를 들어 내경 0.2 m·두께 0.005 m라는
선언 형상의 평균 반경은 0.1025 m다. 입력 형상과 실제 설비의 대응을 검토해야
한다. 어댑터의 두께/반경 상한은 최대 0.1이며 더 좁게 지정할 수 있다.

온도는 피로 곡선 적용성을 판단할 위치의 재료 온도다. 공정 유체의 온도와
자동으로 같다고 두지 않는다. 압력만 실측이고 온도를 선언했다면 원 재료
이력의 origin은 model_prediction이다. 압력·온도의 원 기원은 따로 유지한다.
결과는 두 입력이 모두 측정됐어도 calculated_screening이다.

압력의 단위가 bar인 것만으로 기준이 확정되지 않는다. gauge는 대기 기준
절대압을 추가하고 absolute는 그대로 사용한다. unspecified 또는 대기압이
없는 gauge는 needs_data다. 외부 압력도 별도 절대압과 근거가 필요하다.
상수 압력 오프셋이 응력 범위를 바꾸지 않더라도 평균 응력·응력비는 바뀐다.

valid가 하나라도 거짓이거나 압력·온도가 None이면 전 구간의 집계를 보류한다.
결측을 정상값으로 채우거나 구간을 이어 붙이지 않는다. 창을 분할할 때 잔여
반주기의 취급을 검토해야 한다. 표본 시각은 보간·압축·주기 반복하지 않는다.
최대 표본 수 기본값은 100000이고 사용자가 3~1000000 사이로 지정한다.

## Python에서 사용

```python
from pipeline_integrity.fatigue_exchange import assess_pressure_fatigue
from examples.pressure_fatigue import make_case

request, context = make_case()
result = assess_pressure_fatigue(request, context)
result.check_integrity()
print(result.status, result.values.get("damage_increment"))
```

예제 모듈은 소스 배포에서 사용한다. 설치된 wheel만 쓸 때는 저장한 JSON을
PressureFatigueRequest·RunContext로 읽거나 위 표의 객체를 직접 구성한다.
고유한 이력·곡선으로 바꿀 때는 새로운 근거와 receipt를 생성한다. model_copy로
중첩 값을 바꾸고 기존 binding_digest를 유지한 요청은 계산 경계에서 거부한다.
해시는 변경 검출과 재현을 위한 것이며 관측의 진위나 시험 적합성을 인증하지 않는다.

원 재료 응답의 모델 ID·자산·위치·시계·전체 입력·해시를 대조한다. 계산 성공이면
초기 손상·증가량·전체 손상·한계 대비 여유의 산술 장부도 확인한다. 원 응답은
native_result에 보존하며 out_of_scope를 computed로 승격하지 않는다.

## 물리와 계산 의미

현재 원 워크플로는 내부-외부 압력 차에 평균 반경/관벽 두께를 곱한 원주
막응력을 쓴다. 집계한 응력 범위 Δσ에 대해 선언한 관계
N=N_ref·(Δσ_ref/Δσ)^m를 사용하고 주기 수/n수명의 합을 초기 손상에 더한다.
수식과 계수는 합성 기준 또는 검토된 사용자 곡선이다. 재료별 규격 곡선이나
제조사 표를 포함하지 않는다. 선언한 응력 범위·온도·응력비를 벗어나면 원
엔진이 보류하며, 평균 응력 보정이나 피로 한도를 임의로 추가하지 않는다.

레인플로는 별도 MIT [rainflow 프로젝트](https://github.com/iamlikeme/rainflow)를
원 재료 엔진이 호출한다. [공식 패키지 사용 설명](https://pypi.org/project/rainflow/)의
주기별 범위·평균·개수·원 인덱스 형식을 확인하고 실제 3.2.0을 실행했다.
잔여 반주기는 0.5회로 유지한다. 라이브러리 사용을 규격 전체의 준수나
독립 재료 수명 검증으로 표시하지 않는다.

축방향/다축 응력·노즐·응력 집중·용접 잔류 응력·열응력·수소 영향·소성·
균열 진전은 이 연결의 계산 범위가 아니다. 하중 순서 효과나 단순 손상 합의
실제 파손 관계도 별도 검증이 필요하다. 손상은 1을 넘을 수 있고 여유는
음수가 될 수 있다. 이를 0~1로 자르거나 FTA 사건 확률로 전달하지 않는다.

## 액체 압력파 결과 연결

`pressure_history_from_transient`에 원 LiquidTransient 사양과 computed 결과,
관 ID와 start/end, 선언한 재료 온도와 근거를 전달한다. 함수는 전체·관별
체적 수지 판정과 원 사양·문맥 해시를 확인한다. 선택 끝점의 피에조미터 수두
H와 고도 z로 p_abs=p_ambient+ρg(H-z)를 구한다. 속도 수두를 더하지 않는다.
이 연결은 온도 해석을 새로 만들지 않는다. 압력은 simulation, 온도는
user_declared로 남는다. 원 계산이 보류됐거나 사양이 다르면 전달을 거부한다.

실제 기존 밸브 사례의 두 격자에서 집계 주기 수는 1.0, 손상 증가량은
약 1.94194144e-5로 일치했다. 이는 선언한 합성 형상·곡선과 해당 창에 대한
수치 비교다. 격자 두 개의 일치가 임의 파형의 표본 수렴·실제 피로 수명을
증명하지는 않는다. 파동 이후를 주기적으로 반복해 연간 손상으로 바꾸지 않는다.

## 수치 기준과 공개 관측 적용

합성 압력 2/4 MPa의 두 왕복, 평균 반경 0.1 m·두께 0.005 m를 사용했다.
원주 응력은 40/80 MPa, 범위 40 MPa·응력비 0.5다. 네 반주기의 합은 2회,
선언 N_ref=1000에서 손상 증가량은 0.002, 초기 0.1을 더한 값은 0.102다.
범위를 두 배로 하면 지수 3에서 증가량은 0.016이었다. 별도 thin_cylinder,
rainflow, basquin, miner를 실제 호출해 같은 기준을 확인했다. 온도·응력 범위·
응력비·두께비·외압의 원 보류도 실행했다.

기존 [UCI 유압 자료](https://archive.ics.uci.edu/dataset/447)의 첫 회차 PS1
6000표본을 읽었다. 출처는 Helwig·Pignanelli·Schütze, DOI 10.24432/C5CW21,
CC-BY-4.0다. 이 자료의 압력 기준·실제 관 형상·재료 온도·수명 곡선이
대응되지 않아 원 상태는 needs_data다. 외부 ZIP의 이전 취득 해시도 대조했다.

두 압력 기준·상수 온도·가상 형상·합성 곡선을 명시해 실행한 조건부 예시는
1521.5회와 약 0.01439364의 증가량을 반환했다. 두 경우의 범위 기반 손상은
같지만 응력비는 다르다. 같은 손상값이 실제 압력 기준을 식별한 것이 아니다.
이 값은 해당 실험 장치의 검증된 손상·예측 수명·HEP가 아니다. 센서 잡음,
100 Hz 밖의 변화와 실제 피로 유효 하중의 구분도 후속 검토 대상이다.

## 후속 연구와 기록

재현 시 source tree·history.py·rainflow·어댑터·검증 스크립트 해시, 원 런타임을
대조한다. 원자료와 구간별 결과는 공개 산출물에 넣지 않는다. 재료 엔진의
소스 변경 시 같은 기준과 원 보류를 다시 실행한다. PI-X04에는 다중 기원의
의미 보존·표본 해상도·분할 창의 잔여 주기·확률화에 필요한 별도 근거를
요청한다. 손상 계산 자체를 다른 세션에서 중복 구현할 필요는 없다.
