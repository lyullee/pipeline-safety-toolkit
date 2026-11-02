# 탱크 압력·기상 배출·순상변화 이력 API

구현 상태: 코드 및 검증 시나리오 작성. 수치·물리 검증은 누리온 실행 대기. 이 문서는 검증 합격서가 아니다.

## 범위와 소유권

`pipeline_integrity.tank_breathing`은 기존 `lh2dt`의 `StratifiedTank`, `Valve`, 열역학 속성 공급자를 연결한다. 별도의 시간 적분기, 밸브 상관식, 열역학 물성식은 만들지 않았다. 기하 변환은 기존 `tanks.lh2dt_tank_geometry`를 이용한다. 현재 수평 대칭 평판/타원 헤드 형상과 순수 Hydrogen/ParaHydrogen에 한정된다. 기하 교차검사는 부피에 관한 것으로, 젖은 면적이나 동특성의 독립 검증을 뜻하지 않는다.

두 유체 영역 및 두 벽체 영역의 보존 상태를 사용하며, 열전달 계수와 열용량은 사용자가 근거와 함께 제공한다. 초기 상태는 질량·내부에너지 직접 지정 또는 포화 압력·액체 체적분율 지정 중 하나다. 내부에너지의 기준은 모든 유입 상태와 동일한 런타임 물성 기준이어야 한다.

## 호출 순서

1. `TankBreathingClosures`, `TankBreathingPorts`, `TankBreathingVent`, `TankBreathingWindow`를 만들고 `TankBreathingRequest`에 조합한다.
2. `boundary_metadata(request)`를 실제 `RunContext.metadata['tank_breathing_boundary']`에 기록한다. 자산, 시계, 포트, 유체, 기하 및 운전 구간의 동일성을 확인하는 계약이다.
3. 다섯 `REVIEWS` 항목에 실제 검토 결과와 근거 ID를 연결한다. 근거는 context의 고유 `EvidenceRef`에서 선택한다. 미검토 입력은 `needs_data`이며 native 계산을 시작하지 않는다.
4. 실제 검토 시점의 `native_backend_metadata()`를 `owner_pins`에 보관한다. 소스, 라이선스, 런타임, 공유 adapter, 물성 기준 및 기하 공급자 식별자가 계산 전후 일치해야 한다.
5. `evaluate_tank_breathing(request, context)`로 기록을 만든다. `verify_tank_breathing_record`는 봉인만 검사하는 것이 아니라 현재 소유 모델로 재계산한다. 비용은 원 계산과 유사하다.
6. 성공한 진단 기록은 `to_signal_frames(record, context)`로 다른 평가 모듈에 전달한다. 신호 valid는 수치 가용성으로 압력 운전승인을 뜻하지 않는다.

`examples/tank_breathing.py`는 작성자가 만든 합성 입력이다. 기본 실행은 검토되지 않은 상태를 보여 준다. `make_case(reviewed=True)`는 검증용 입력 작성 편의 기능이며, 실제 설비 검토나 측정 근거를 대신하지 않는다. 예제에 사용된 계수나 허용오차를 설계값으로 사용하지 않는다.

## 경계와 출력의 의미

운전 구간마다 일정한 열유입, 주변온도, 액체·기체 유입 및 공정 배출을 지정한다. 값이 없으면 None, 알려진 무유량·단열 입력은 명시적인 0을 쓴다. 유입은 공통 HEOS 유체의 검토된 단상 상태여야 한다. 배출 엔탈피는 해당 native 영역의 상태에서 계산한다. 젖은 내부 혼합상으로 넘어간 영역은 현재 포트 계약으로 지원하지 않는다. native 물성 공급자의 포화 끝점 품질 처리도 source pin의 일부다.

벤트는 폐쇄 또는 구간별 지정 개도만 지원한다. 역류는 금지하며 하류는 지정된 단상 기체 상태다. 자동 개방·복귀 압력의 히스테리시스와 공기 유입을 포함하는 대기 호흡은 지원하지 않는다. 현재 PSV 소유 객체는 평가할 때 내부 개방 상태를 바꾸므로 RK4 중간 시도 상태에 직접 연결하면 사건 이력이 오염될 수 있다. 향후 소유 모듈에서 순수 평가/상태 확정 분리와 사건 위치 결정 계약을 제공해야 한다.

누적 경계 질량과 에너지는 원소유 RK4가 적분한 장부다. 벤트 누적 질량은 알려진 일정 공정 경계 질량에서 native 전체 경계 질량을 뺀 값이다. 폐쇄 벤트는 명시적 무배출 경계이므로 실제 벤트 누적량은 0이고, 질량 재구성 오차는 별도로 허용오차와 비교한다. 유체 전체 질량 변화 및 벽체를 포함하는 에너지 변화와 적분 장부를 교차 확인한다.

`cumulative_net_liquid_to_vapor_transfer_kg`는 signed 순상변화다. 양수는 순기화, 음수는 순응축이며 벤트 손실과 동일하지 않다. 증발과 응축의 각각 총량은 적분하지 않았으므로 `gross_evaporated_mass_kg`, `gross_condensed_mass_kg`는 None이다. `cumulative_vent_enthalpy_J`도 None이다. 전체 에너지 장부를 벤트별 에너지로 바꾸어 보고하지 않는다.

종료점 `endpoint_evaporation_rate_kg_s`와 `endpoint_vent_mass_flow_kg_s`는 순간 변화율이다. 이 값에 총 시간을 곱해 누적량으로 사용하지 않는다. 최종 압력, 초기/수락된 스텝의 최저·최고 압력, 사용자가 준 압력 한계와의 비교를 반환한다. RK 중간·조밀 출력의 극값 탐지는 하지 않는다. 실제 적분 시간, 요청 시간, 미적분 꼬리 시간을 별도로 반환한다.

## 검증과 후속 실행

`tests/test_tank_breathing.py`에는 검토 차단, 자산·시계·포트 바인딩, 지원하지 않는 물리 모델 차단, 기록 변조, 비유한 복사 입력, 포트 중복, 폐쇄계 질량·에너지 장부와 재실행 비교를 작성했다. 사용자 지시에 따라 이번 구현에서는 pytest·벤치마크·native smoke를 실행하지 않는다. Ruff 및 Python 컴파일 검사는 문법과 정적 품질만 확인한다.

검토된 계산을 요청했지만 선택적 native owner/backend를 찾지 못하면 `evaluate_tank_breathing`은 `out_of_scope`와 `native_called=False` 진단을 반환한다. 검토된 owner pin 불일치는 계속 입력 오류로 거부한다. 이 경계 회귀는 작성했지만 미실행이다. 누리온에서 공통 테스트 이관 절차를 통해 이 파일을 실행하고, 원소유 패키지가 실제 설치되었는지 확인한다. `importorskip('lh2dt')`로 건너뛴 native 검사는 합격으로 계산하지 않는다. 추가 qualification에는 시간 간격 수렴, 구간 분할 일관성, 열유입/벤트/충전/배출 시나리오, 포화·상경계, 압력 영역 끝점, 독립 측정 비교가 필요하다. 현재 구현은 전체 탱크 호흡 모델 완성이나 독립 물리 검증을 주장하지 않는다.

owner pin 조회가 성공한 뒤 개별 solver 모듈 import에서 backend가 사라지는 race도 구분한다. owner 진입 전에 `ImportError`가 나면 재탐색을 시도하지 않고 봉인된 `out_of_scope` receipt를 돌려주며 `native_called=False`를 유지한다. 이 회귀도 작성만 했고 미실행이다.

## 확장 요청

원소유 모듈에 사건 기반 PSV 히스테리시스, 다성분 headspace, 액적 동반 배출, 포트별 적분 엔탈피, signed 및 양·음 구간별 상변화 적분을 추가할 경우 기존 보존 장부와 같은 수락 스텝에서 함께 적분하고 상태·근거·소스 식별자를 반환해야 한다. 중간 평가에서 영구 상태를 바꾸지 않는 계약과 재실행 결정성을 우선한다. 임의의 종료점 사후 적분을 별도 중복 모델로 추가하지 않는다.

## 공개 개발 기준

정적 교차검토 보강: request event와 실제 `RunContext.scenario_id`의 일치, 그리고 closure/초기 상태를 포함한 전체 request digest의 문맥 결합을 검사한다. 초기 압력이나 열 closure 값을 변경할 때는 검토된 요청과 함께 `boundary_metadata`를 다시 생성해야 한다. 실제 `StratifiedTank.simulate`의 RK4 유체·벽 열 callback과 cumulative boundary mass/energy 출력키를 대조했으며 별도 종료점 사후 적분으로 native 장부를 대체하지 않았다. 관련 계약 시험은 미실행 상태이다.

문서·주석·예제는 새로 작성했다. 표준 수치표나 공급업체 표를 내장하지 않았다. native 의존성의 실제 라이선스·소스 식별자는 기존 공통 metadata 계약으로 보존하며, 배포자는 사용하는 소유 패키지와 의존성의 고지 조건을 함께 충족해야 한다. 임의 측정 자료나 외부 설비 데이터는 포함하지 않았다.

공식 기술 자료: [CoolProp 고수준 API와 기준 상태](https://coolprop.org/coolprop/HighLevelAPI.html#reference-states). 새로운 ODE 엔진을 추가하지 않은 이유는 [SciPy solve_ivp](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.solve_ivp.html)로 중복 구현하는 대신 현재 원소유 보존 적분을 그대로 활용하기 위해서다.
