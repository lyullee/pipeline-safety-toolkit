# 밀도 없는 액체 head 손실 API

`models.hydraulics.LiquidHeadLoss`와 `liquid_head_loss`는 동점도 ν가 알려진 경우 밀도 없이 관 마찰과 고도 head 요구를 계산한다. ρ=1 같은 가상 물성을 입력하지 않는다. 관측 head 차만 있는 실험 자료를 불필요한 Pa 환산 없이 비교할 수 있다. 물성/장치 적용성, head 센서 기준과 boundary 검토는 소비자가 수행해야 한다.

## 입력과 출력

필수 입력은 `diameter_m`, `length_m`, `roughness_m`, `volume_flow_m3_s`, `kinematic_viscosity_m2_s`이다. 추가 입력은 기존 pressure API와 같은 minor_loss_coefficient, outlet_minus_inlet_elevation_m, gravity_m_s2, transition_policy 및 friction_method이다. 기본 중력은 기존 API의 수치 정책을 유지하므로 실제 자료의 다른 중력이 알려졌다면 명시 공급한다. 절대 거칠기는 m이고 상대 거칠기와 혼동하지 않는다. 총 길이 0의 lumped-loss 경우도 기존처럼 허용한다.

출력은 다음 6개이다.

- velocity_m_s: signed 평균 속도 m/s.
- reynolds: 속도 절댓값과 공급 ν로 구한 Reynolds 수.
- darcy_friction_factor: 기존 Fluids owner가 반환한 Darcy 마찰계수.
- dissipative_head_loss_m: 정방향 유량에서 양수, 역방향에서 음수인 마찰/국부손실 head 차 m.
- static_head_change_m: outlet−inlet 고도 차 m.
- required_inlet_minus_outlet_head_m: signed 손실과 고도 차의 합 m.

head-only 결과에는 절대압/차압 Pa, 질량 유량 kg/s 또는 동력 W가 없다. density나 dynamic viscosity를 `LiquidHeadLoss`에 끼워 넣으면 extra field로 거부한다. head는 같은 기준의 압력-head 차를 비교하기 위한 요구량이며 absolute/gauge 압력이나 실제 outlet 상태를 자동 결정하지 않는다. 수직 고도 차가 미상이면 0으로 추정하지 않는다.

## 하나의 물리 계산 경로

기존 `LiquidPressureLoss`의 입력 필드와 `liquid_pressure_loss` 결과 7개 키·부호는 유지한다. pressure 경로는 실제 공급 μ/ρ에서 ν를 구하여 head 경로와 같은 `_liquid_kinematic_loss`에 전달한다. 공통 경로가 단면·속도·Reynolds·Fluids Darcy 계수 및 단위 질량당 기계적 소산을 계산한다. 공개 head mapping은 이를 g로 나누고, 실제 밀도가 있는 pressure mapping만 ρ를 곱하여 Pa 및 소산 W를 만든다. head solver와 pressure solver의 마찰관계식을 두 벌로 만들지 않았다.

specific dissipation의 m²/s²는 단위 질량당 에너지 차이며 동력이나 실제 질량 에너지 흐름이 아니다. 마찰 소산을 외부 열원으로 다시 넣지 않는다. 지점별 열 상태나 온도 변화도 이 API가 예측하지 않는다.

## 부호와 영역 유지

Q는 inlet→outlet이 양수이다. 마찰 요구의 부호는 Q와 함께 바뀌지만 고도 차는 포트 기하에 고정되어 바뀌지 않는다. pressure 경로의 마찰 소산 W는 정·역류 모두 비음수이다. Q=0이면 velocity/Re/fd/마찰손실이 0이고 고도 head만 유지한다. 확정된 무유동 정역학은 μ/ρ가 부동소수점으로 표현되지 않아도 불필요한 Re 계산 때문에 제거하지 않는다.

기존 상대 거칠기 상한 0.1, 전이 구간 2300≤Re<4000의 기본 보류 및 명시 use_declared_correlation 선택을 동일 공통 경로에서 유지한다. 기본/선택 마찰법도 기존 Fluids 호출을 사용한다. 이 문서는 해당 correlation을 모든 유체·단면·다상·비Newtonian 영역에 확장하지 않는다.

입력의 nonfinite/모순은 typed contract에서 거부한다. 중간 속도/Re/마찰/소산 또는 mapping이 표현 범위를 넘으면 `out_of_scope`와 빈 값·원인을 반환한다. nonzero flow에서 속도/Re가 underflow했다고 zero-flow 물리 상태로 바꾸지 않는다. pressure와 head mapping의 표현 범위는 독립적이다. 예컨대 매우 작은 g 때문에 m 단위 head만 overflow해도 Pa가 유한한 pressure 계산까지 같이 거부하지 않는다. 수치 표현 실패를 물리 상관식의 새 적합 영역으로 해석하지 않는다.

## 연계·source 기록

기본 registry에 `hydraulics.liquid_head_loss`로 등록되어 CLI/registry 평가와 공통 불확실성 엔진의 `registry_node`에서 바로 재사용할 수 있다. 단위 metadata는 동점도 m²/s 입력과 head m 출력을 유지하고 density/pressure/power 결과는 등록하지 않는다. 운영점 또는 가상/실험 조건의 head 시나리오를 `uncertainty.evaluate_ensemble`에 연결할 때도 draw와 output 의미를 호출자가 선언한다. 그 결과는 선언 설계의 조건부 기술 통계이며 현장 실패확률·신뢰구간이 아니다. friction 자료 소비자는 원 source의 Q/ν와 검토된 기하·고도·중력을 head 경로에 공급할 수 있다. 실제 밀도가 따로 있을 때만 기존 pressure owner와 비교하거나 Pa/질량/동력 장부를 작성한다. 알려지지 않은 밀도에는 여전히 null을 유지한다.

원 모델과 helper는 같은 `models/hydraulics.py`에 있다. 직접 소비자가 runtime/source를 고정할 때는 공개 mapping 함수뿐 아니라 이 공통 owner 모듈의 전체 소스 SHA와 실제 Fluids 버전/소스를 보존한다. native 결과와 적용성/독립 검증은 구분한다.

## 현재 검증 상태

기존 유압 source·output keys·Fluids 호출과 registry field 집합을 정적으로 대조했다. 새 회귀 시험은 독립 Hagen–Poiseuille head 식, 공급 밀도를 사용한 pressure/head 일치, signed·무유동·고도, 전이 보류, turbulent Colebrook 잔차, 가상 물성 거부 및 mapping별 overflow/underflow를 포함한다. 기존 `tests/test_physics.py`는 변경하지 않았다.

Ruff와 Python 컴파일만 수행했다. pytest, Fluids 계산, 수치/native solver, driver 및 benchmark는 실행하지 않았다. registry discovery 및 `registry_node`를 통한 공통 불확실성 시나리오 회귀를 추가 작성했으며 누리온에서 실행할 것이다. 후속 누리온 환경에서 `python -m pytest tests/test_liquid_head.py tests/test_physics.py -q`와 기존 heated/slurry/friction 소비자 시험을 실행한다. 정적 확인이나 시험 작성만으로 수치·현장 검증을 완료 처리하지 않는다.
