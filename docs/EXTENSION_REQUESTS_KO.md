# 다른 프로젝트에 전달할 확장 요청서

각 요청은 별도 세션에서 해당 소유 프로젝트를 수정할 때 사용한다. 이번
저장소의 구현과 요청 중인 기능을 구분한다. 우선순위는 공통 시계·수지·근거의
보존, 실제 자료 적용, 고급 모델 순서다. 새 저장소를 만들거나 기능을 복제하는
것을 완료 조건으로 삼지 않는다.

## PI-X01 — 물리 동역학·방출 엔진: 시계와 경계의 결합

대상은 lh2dt_models와 hydrogen-station-sim이다. 입력 계약은 이 저장소
`FlowBoundary`와 호환되는 자산·포트·시계·시간·방향·물성 기준·절대압·온도·
질량유량·엔탈피·상·근거 ID를 받는다. 명령 이력에는 event_id, command_time,
actuation_start, completion_time, clock_id와 구동 특성을 명시한다.

조사한 lh2dt_models에는 이미 `StrokeLimitedCommandedValve`, 상태 P/h 네트워크와
사고 이력 export가 있다. 이를 새로 만들지 않는다. 이번 어댑터는 실제 P/h 상태와
유한 스트로크 밸브를 호출했다. 확장 대상은 공통 시계·일반 유체·포트 경계·
재고 수지의 완전한 왕복 연결이며 기존 동작 기능의 복제가 아니다.

현재 래퍼에서 장치 팩토리 22종 입력 조회, 관 호출, 액체·2상 평형 및 작은 압력
변화의 시간 세분화를 실행했다. 조사한 팩토리는 알 수 없는 파라미터를 넣어도
ready를 반환할 수 있다. `EquipmentRequest` 기본 정책에서 이를 차단했으나,
소유 API가 완전한 필수·선택 입력 스키마와 실제 소비 필드 목록을 반환하고
알 수 없는 입력을 명확히 거부하도록 확장해 달라. 재현은
`research/equipment_factory_results.json`의 unknown parameter 항목을 따른다.

동적 스텝의 응답은 최종 시각의 미분 수지다. 구간 적분에 사용한 질량·엔탈피·
열 유량 누적량과 적분 오차를 별도로 반환해 누적 재고/에너지 장부를 닫아 달라.
래퍼는 세분화 일치와 미분 수지를 전체 방출 이력의 수지로 승격하지 않는다.

추가 재현은 `scripts/check_release_exchange.py`와
`research/release_exchange_results.json`에 있다. 다음 원본 문제를 수정해 달라.

- `DynamicHEMPipe.export_accident_history`가 `properties.fluid="ParaHydrogen"`이어도
  응답 `data.fluid`를 Hydrogen으로 반환한다. 실제 fluid·백엔드·에너지 기준을
  구조화해 반환하고 원본/수신 포트의 물성과 비교해야 한다.
- 500 kPa·초기 품질 0.1의 일반 수소 기준 사례에서 기체 분기의 밀도가 약
  4.562 kg/m³인 반면 주변 압력의 포화 증기 밀도는 약 1.332 kg/m³다. 혼합물
  밀도를 기체 분기 상태로 사용하지 말고 분기별 상태·엔탈피·면적·속도·에너지
  장부를 함께 닫아 달라. 기체 밀도만 바꾸고 속도나 에너지를 그대로 두면 안 된다.
- `impact_position_m=(2,0,0)`이 `(1,0,0)`으로 바뀐다. 위치는 크기를 가진 좌표이며
  방향 정규화 함수를 적용하면 안 된다. 비행 시간·도달 위치·분기 계산의 근거도
  반환해 달라. 현 래퍼는 원 좌표를 보존하고 불일치를 보류한다.
- 같은 기준 압력에서 초기 품질 1.0을 주면 phase_basis는 gas인데 반환 행은
  액체/2상 필드 형식이다. 포화 끝점의 상 분류와 실제 행 스키마를 일치시키고
  품질 0·중간·1 및 단상 상태의 각각을 검사해 달라. 현재 재현 파일에서
  `provider_phase_history_shape_mismatch`로 보류한다.

PRISM의 현재 방출 빌더는 위 유체명·기체 분기 사례를 형식상 수용했다.
PI-X02의 수신 경계에도 공급자의 실제 물성 기준과 기체 분기 상태 검사를 추가해
달라. 이 저장소는 원 응답을 수정해 통과시키지 않는다.

사고 export는 질량·정적 엔탈피 누계를 반환하지만 초기/최종 내부에너지와 벽
에너지·누적 열 교환 전체를 응답에서 재구성할 수 없다. 전체 state history 또는
검증 가능한 적분 원장을 추가해 달라. 지정 시각의 순간 폐쇄는 기존 유한
스트로크 장치·네트워크의 재고 방출과 구분하고, 사건 시각을 적분 격자에 보존해 달라.

수동 차단 명령 즉시 방출이 0이 되는 단순화를 제거한다. 밸브 운동과 차단 후
배관 재고를 계산하고, 미지의 2상·비등 상태는 명확히 보류한다. 이미 있는
재고·풀·열역학 코드는 재사용하며 신규 일반 배관 엔진의 범위를 분리한다.

완료 조건은 무방출 닫힌 계의 질량·에너지 보존, 사건 ID 재전달의 멱등성,
지연·복구·역류·구동 실패 시나리오, timestep 축소에 따른 수렴, 명령 시각과
완료 시각이 다른 기준 사례다. 결과는 누적 방출 질량·잔류 재고·수지 잔차·종료
이유·유효 범위를 포함해야 한다. 수치 허용오차는 기준 사례별로 문서화한다.

위 PI-X01의 추가 재현은 `research/valve_transient_results.json`과
`docs/VALVE_TRANSIENT_KO.md`에 있다. 원 `StrokeLimitedCommandedValve`를 재사용해
개도 궤적을 얻고 액체 MOC에 연결했다. 현재 원 생성자는 물성 객체를 가진
Valve를 요구해 조회 금지 공급자를 사용한다. 유체 계산과 독립된 공개 구동
팩토리와 직렬화 계약을 추가하고 command·target 관측 구간·수력 차단을
구분해 달라. 기존 구동 알고리즘 자체를 다시 구현할 필요는 없다.

합성 기준에서 목표 도달 직후 밸브 유량은 0이지만 하류 말단은 0.01 m³/s로
흐르고 0.5초 순유출량은 0.005 m³다. 닫힘 CdA가 남으면 밸브 유량도 남는다.
확장 완료 검사는 이 구분·지연·역류·병렬·격자·관별 수지를 유지해야 한다.
현재 액체 기준을 기체/다상 차단 후 방출의 검증으로 삼지 말고 해당 유체의
잔류 재고·에너지 원장과 별도 연결해 달라. 압력 하중·구동 토크 피드백은 후속
물리 모델이며 고정 스트로크 시간만으로 이를 검증했다고 표시하지 않는다.

정상 기체 관망의 추가 계약은 `gas_network.GasNetwork`, 예제 gas_network.json,
실제 16사례와 소스 해시는 `research/gas_network_results.json`을 따른다. 원
SteadyNetwork·Pipe·물성 클래스로 메탄·질소·선언 메탄/수소 혼합물, 열·혼합·
역류·병렬을 실행했다. 이쪽에서 해당 해법을 다시 구현하지 않는다.

다음 확장을 원 엔진에서 검토해 달라.

- 직접 노드 질량 인출·주입과 주입 엔탈피·물성 기준·근거를 받는 계약. 현재
  인출은 계산용 말단을 가진 질량 경계로 연결하고 그 말단을 물리 상태에서
  제외한다. 원 API의 직접 부하 입력으로 이 보조 표현을 대체해 달라.
- 압력 해석의 kg/s 잔차를 지정 스케일로 정규화하는 공개 옵션. 차원이 있는
  기울기 종료가 작은 유량의 물리 수렴을 대신하지 않아야 한다. 최종 SI
  수지 판정은 유지한다. 기준은 GAS_NETWORK의 질소 층류와, 더 작은 내경
  0.001 m·길이 100 m·101/100 kPa·300 K에서 엄격한 질량 허용값을 준 사례다.
- 관의 전체 에너지: m(h+v²/2+gz) 이송·열·기계일과 양쪽 단면·속도·수지.
  현재 원 Pipe의 정적 엔탈피와 입구 밀도 근사는 별도 모델 식별자로 유지하고
  고도·고마하·밀도 변화가 큰 범위에 맞는 모델과 자격을 구분해 달라. 현재
  래퍼는 고도차를 보류하고 임의의 엔탈피 수정으로 통과시키지 않는다.
- 원 성공 플래그가 참이어도 정체 접점의 엔탈피는 미정일 수 있다. 실제
  stagnant_unknown_enthalpy 사례를 재현하고 미결정 상태·초기 추정의 기원을
  명시적으로 반환해 달라. 잔차 0만으로 온도 해를 승인하지 않아야 한다.
- 기체 능동 장치의 버전 있는 P/h·포트·열·기계일·유량 부호·가스 조성·
  운전 영역 계약. GasLib의 압축기 구성과 밸브 상태를 기존 팩토리 경로로
  연결하되, 액체 펌프나 단순 지정 압력 증가를 기체 압축기 맵으로 바꾸지 않는다.

완료 조건은 지정 부하와 분기·다중 공급원·역류·0유량·정체 상태의 실제
왕복 호출, 원 총 에너지·질량 수지, 기체 상/도착 상태·같은 조성의 유지,
기존 구간 세분화 기준과 직접 부하의 일치다. 원 GasLib 전체 운전은 실제
조성·압력/유량 기준·열·장치 조건을 매핑한 뒤 별도로 대조한다. 단일 관의
선언 메탄 사례를 원 전체 지정 운전의 완료 조건으로 사용하지 않는다.

### 탱크 형상·관측·재고의 후속 왕복 계약

이쪽 `tanks.evaluate_tank_inventory`와 `lh2dt_tank_geometry`는 구현했다.
`research/tank_inventory_results.json`의 평판/타원 수평 형상 2종·각 17점은 원
HorizontalVesselGeometry와 체적만 대조했다. 원 수평 형상·DPT/압력 보정·층상
탱크 모델을 복제하지 않고 아래 API 확장을 기존 책임 안에서 제공해 달라.

- 형상 프로토콜에 내부 치수·높이 영점·체적/액위 단위·범위·끝점·역변환 잔차·
  버전/소스 해시·검토 근거를 반환한다. 수직·비대칭 끝단·보정표는 지원 범위와
  정확도를 명시한다. 보정표나 체적 교환을 wetted area 검증으로 확대하지 않는다.
- 정적 관측과 동적 상태를 구분한다. 자산·실험 상대 clock_id·time_s·원본 열과
  단위/영점 근거, 액체/기체의 유체·상·절대압·온도/엔탈피 기준을 유지한다.
  Q=0/1 끝점과 벌크 2상 혼합 밀도를 구분하고 미지의 양의 상 질량은 null로 받는다.
- 기존 DPT나 압력 계측 모델은 센서 단위·기준압·탭 높이·밀도/보정 근거·불확실성
  계약을 통해 이 관측에 연결한다. 액위 단위/영점 공백이나 깨진 메타데이터를
  자동 복원하지 않는다. 펌프 명령 %를 실제 유량으로 치환하지 않는다.
- 검토한 관측으로 동적 상태를 초기화할 때 가정·질량/에너지 변경·경계 유량·열·
  상변화·층상 효과를 기록한다. 정적 스냅샷을 시간 적분의 검증으로 사용하지 않는다.

인수 조건은 현재 2종/17점 체적 기준의 보존, 양방향 액위/체적·0/만충·범위 밖·
결측·미지 기체 질량·상/유체 불일치·시계 충돌의 실제 왕복 호출이다. 추가 형상은
별도 기준과 잔차를 제시한다. 공개 결합 탱크의 81액위/80명령과 원 단위 공백은
TANK_INVENTORY의 needs_data로 유지하며 조건부 3240개 결과를 현장 재고 교정으로
표시하지 않는다. 원 데이터의 실제 형상·영점·상태·유량을 확보한 뒤 수지 검증을 한다.

### 제어·계측·실제 장치 추종의 후속 계약

이쪽 PIDSession과 TransmitterScenario를 먼저 구현했다. 원 PID·계측·수소 기체
탱크·밸브의 폐루프 실제 실행은 CONTROL_EXCHANGE 및 control_exchange_results.json에
있다. 조건부 적분 방지와 미분 필터를 다른 엔진에 복제하지 않고 기존 소유 API를
확장해 달라. 현재 온도는 120초에서 약 +0.916K 오차가 남으며 성능 적합이 아니다.

입력은 SI 물리 axis/reference_id·기준압, 판단/관측/수신 가능 시각·clock_id,
관측 valid·근거·단위 있는 이득·명령과 실제 위치/유량 피드백이다. 수동/자동·
추종·고장/보류 모드, 실제 구동 위치와 PID 포화의 차이, 인터록·보호·수동
조작의 우선순위와 mode 전환 근거를 버전 있는 계약으로 제공해 달라. 추종
적분 보정이나 bumpless transfer는 명시한 알고리즘/매개변수와 상태로 반환한다.
스캔/전송 지연·reported clock·실제 시계 매핑 근거·드리프트·교정/잡음 추정도
선언한 고장 시나리오와 구분한다. timestamp를 맞추기 위해 원 행을 고치지 않는다.

인수 조건은 이번 native 포화/미분/역작동/checkpoint 재현, 명령으로 위치가
즉시 변하지 않는 밸브 기준의 보존, 압력/온도/유량 폐루프와 질량·에너지 장부,
수동/자동·보호 우선순위·실제 포화/추종·센서 공백·순서 역전·미도착·시계 충돌의
왕복 호출이다. 실제 운전 한계·추종/안정성·허용 오차와 독립 교정 자료를 먼저
정의한다. 공개 실험 800개 shadow 명령은 원 pump %를 대체하지 않으며 실제
조치·폐루프 검증·방호 성공으로 사용하지 않는다.

## PI-X02 — PRISM: 유도 빈도와 사건 중복 방지

고정 공개 버전에는 `lh2_tno.derived_frequency`가 없고 `DemandFailure`가 있다.
이쪽에서 실제 HRA→DemandFailure와 연간 기대 횟수 일치를 검증했다. 받는 쪽은
이 객체를 누설 빈도로 바꾸지 않으면서 `RiskFrequency`의 유도 빈도와 계보를
수용하도록 확장해 달라. 입력에는 initiating_event_id, scenario_id, rate의 수치·단위·노출,
조건부 사건·조건·확률의 의미, uncertainty, source_result_digest, 이미 적용한
방호 ID를 포함한다. HEP를 연간 빈도로 해석하려면 작업 요구 횟수가 별도
근거로 있어야 한다.

경로가 상호배타인지, 독립인지, 의존성을 반영했는지 표현한다. 정보가 없으면
독립 곱으로 자동 계산하지 않는다. 연구 추정값을 검토 완료 값으로 승격하지
않는다. 같은 감지·회복·차단 효과를 FTA·ETA·LOPA·결과 저감에 반복 적용하지
않도록 consumed_event_ids를 보존한다.

완료 조건은 조건부 분기 보존, 단위가 다른 빈도 거부, 누락 노출의 보류,
동일 사건 중복 검출, 구버전 입력의 명시적 변환, 폐쇄된 기준 사건수에서
조건부 확률 합이 1인 검사다. 임의 분기의 확률을 정규화해 오류를 숨기지 않는다.

구체 입력은 `risk_exchange.RiskFrequency`와 `RiskHandoff.record()`다. 보존 필드는
context, condition_id, endpoint, consumed_event_ids, consumed_protection_ids,
dependency_groups, evidence_ids, source_payload/source_digest/binding_digest다.
예제·현재 실제 결과는 `scripts/check_risk_exchange.py`와
`research/risk_exchange_results.json`을 따른다. protection_failure_combination을
uncontrolled_process_state나 loss_of_containment로 바꾸려면 해당 방호 실패
조합이 실제 공정 제어 상실·누설을 일으키는 경로,
조건부 확률과 그 근거를 추가해야 한다. 공개 예제의 가정 빈도를 기본값으로
채택하지 말고 원 research 상태와 포함 효과를 그대로 유지해 달라.

### 균열·cycle·사용자 경계 결과의 위험 의미

fracture_exchange의 resistance margin, cycles_evaluated, termination_reason,
declared_boundary_outcome과 native 계산 status를 받되 파손 확률·연간 누설 빈도로
직접 변환하지 말아 달라. computed는 저항 초과/경계 not_satisfied와도 함께 나온다.
보정 밖은 partial trajectory와 out_of_scope를 보존하고, curve 지지 범위 밖의
unknown을 경계 만족으로 바꾸지 않는다. cycle→elapsed/exposure 변환에는 별도
운전 회수·시계·기간·물리 사건 정의와 근거가 필요하다. Source FAD/Mode-I의
deterministic screening과 사람의 실패 사건·공통 원인·구성품 파손을 구분한다.

인수 조건은 정의/기간 누락·서로 다른 물리 location/하중 사례·소스 조건 불일치의
보류, 수명/한계값과 관측된 recurrent 빈도의 구분, 검토한 기회/노출·의존 모형을
제공한 경우의 실제 의미 보존이다. 원 request/receipt digest와 불명확한 결과를 유지한다.

## PI-X03 — 위험 엔진: 평가 결과를 의미와 함께 수용

기존 HRA의 FTA·ETA·LOPA는 이쪽 어댑터를 통해 실제 호출했다. 이 수학을 새로
만들 필요는 없다. 다른 위험 서비스·PHA 스냅샷은 이 버전 계약의 원 계보를
수용하는 경계를 확장해 달라. FTA의 공동 상태와 ETA의 부모 조건을 독립 확률
목록으로 평탄화하지 않는다. 요청/응답의 전체 필드는 RISK_EXCHANGE를 따른다.

FTA 기본 사건, ETA 분기와 LOPA 방호에 동일 평가 결과를 연결할 때 역할을
initiator/latent/mitigation 등으로 명시한다. 입력·근거·해시·검토 상태·시간과
불확실성의 원래 의미를 보존한다. 상대 우선순위 점수를 확률로 바꾸지 않는다.

완료 조건은 이미 회복을 반영한 HEP를 다시 회복 처리하지 않는 사례, 기회당
확률과 노출당 고장률의 구분, 검토 상태의 유지, 사용자가 제공한 상관 구조와
공통 원인 입력의 처리다. 구조적으로 다른 그래프를 단순 병합하지 않는다.

### PI-X03 추가 재현 — 기상 방향과 압축성 평면의 보존

기존 PRISM의 압축성 소스 상태 어댑터와 SLABx·열·폭연·위해도 계산까지
실제 호출했다. 수용 계약은 `ConsequenceCase`, `ConsequenceResult`,
`AnnualRiskRequest`이며 상세 입력·명령은 CONSEQUENCES, 수치는
`research/consequence_results.json`과 `scripts/check_consequences.py`를 따른다.
새 방출·확산·피해 모형을 이쪽에서 복제할 필요는 없다.

재현은 1 MPa·300 K 수소, 길이 1 m·내경 0.05 m, 1 mm 개구, 동쪽 방향,
높이 1 m, source horizon 1 s·20/40구간, 기상 풍속 2 m/s다. 원 저마하
경로는 압축성 입력을 거부한다. 기존 Birch 운동량·에너지 평면을 명시적으로
부착하면 270도 바람에서 전체 진단 계산이 실행된다. 원 방출 패킷은 유지한다.

180도 바람은 양의 풍하 속도가 없다는 오류, 250도 바람은 약 6.031%의
handoff geometry mass residual로 거부된다. 현재 `chain_worker.py`의
provider_handoff 경로는 속도를 풍하로 투영하고 원 half_width·depth를 같은
단면으로 넘긴다. 20도에서 1−cos(20°)는 약 6.03074%로 재현 오차와 일치한다.
단순히 source 질량·방향·폭을 바꾸거나 질량 허용오차를 늘려 통과시키지 말아 달라.

완료 조건은 경계면 법선·풍하 좌표 사이의 기하학적 변환을 명시하고 H2
질량률·운동량·에너지와 시계를 보존하는 것이다. 기존 엄격 수지 기준을
유지하며 0·±20도·횡풍·역풍을 포함해 검사한다. 횡풍/역풍에서 양의 풍하
상태가 필요한 경우 기존 벡터 근접장 모형을 재사용해 실제 상태를 구하고,
전이 영역에서 해결되지 않으면 명확한 보류 상태를 반환해 달라. 원 source
result digest·단위·물성·종료 근거와 평면 증거를 유지해야 한다.

연간 조건 270/180/250도의 가중치 0.6/0.2/0.2를 그대로 유지한 경로도 수용해
달라. 이쪽은 미계산 위해확률 0~1로 네 번 원 annual_risk를 호출해 범위를
계산한다. 완료값은 null이고 known_contribution과 uncomputed_conditions를
구분한다. 이 범위는 신뢰구간·전체 사고 위험이 아니다. 소유 위험 API가
미계산 위해도를 직접 표현하도록 확장할 때 이 예제를 회귀 기준으로 삼는다.

진단용 1초 source 종료와 3초 결과 시간창을 실제 누설 종료 또는 전체 사고로
승격하지 않는다. `annualized_declared_window_harm_rate`의 원 의미와 null을
유지하며, HRA DemandFailure를 LOC 빈도로 바꾸지 않는 검사도 함께 수행한다.

## PI-X04 — matintegrity: 스키마 검색과 반복 검사 적용

현재 부식 예측·다층 열전달·합성 관측의 두께 추세는 실제 호출을 통과했다.
소유 `ModelRegistry.list_models/describe`가 이미 있어 새로 만들지 않는다.
이번 `MaterialClient.models/describe`는 해당 레지스트리를 호출한다. 입력 단위의
기계 판독 필드와 현재 구현/검증 상태, `schema_version` 변경 정책을 안정적인
검색 계약으로 확정해 달라.

반복 ILI 적용은 결함 정합을 먼저 해결해야 한다. 데이터 계약에 검사 회차,
거리·시계방향·용접 ID, 관벽 두께, 깊이·길이·폭, 검출성·측정 오차·정합 신뢰도와
자료 해시를 넣는다. mm와 m를 혼합하지 않는다. 정합 실패·새로 검출·사라진
특징을 0 성장으로 채우지 않는다. 결과는 정합 수와 보류 이유를 포함해야 한다.

완료 조건은 비정합 결함의 성장률 계산 차단, 동일 원본 정합 재현, 가상 결함
이동·누락 기준 사례, 부식·피로·압력 이력이 중복 적용되지 않는 통합 예제다.
승인 기준·규격 표는 권한이 있는 사용자 공급자로 받는다.

구체적인 교환 계약은 `inspection.InspectionRun`, `InspectionRegistration`,
`InspectionMatches`, `InspectionTrack`, `CorrespondenceReview`와
`docs/INSPECTION_KO.md`를 따른다. 이쪽에 일대일 위치 정합과 명시적인 미대응
선택·국소 모호성 표시를 구현했다. 해당 구현과 재료 추세를 다시 복제하지 말고,
군집의 분할/병합·검출성·좌표 교정·계측 편향의 전문 모델이 필요하면 그 책임을
확장해 달라. 비용을 대응 확률로 사용하려면 독립 정답과 확률 교정이 필요하다.

원자료 검사 라벨은 실제 설비 나이가 아니다. 절대거리 원점이 다르고 특정
좌표의 단위 설명도 충돌하므로, 회차별 시간 근거와 좌표 등록을 명시해야 한다.
현재 실제 자료에는 정합 승인·측정 오차·초 단위 시각이 없어 성장 계산을
보류한다. 합성 정답의 실제 재료 호출과 이 보류 경로는
`scripts/apply_ili.py` 및 `research/ili_application.json`으로 재현한다.

완료 조건에는 최대 깊이 위치가 특징 안에서 이동하는 경우의 해석, 명목 관벽
기준 변경·보수 이력·음의 관측 손실률 처리, 같은 원본을 다른 행 순서로 읽어도
동일한 검토 경로를 추적하는 검사를 추가한다. 공급하는 계측 오차 한계와 재료
회귀의 통계 구간을 섞지 않고 원본 의미를 유지해야 한다.

압력 피로 추가 재현은 `research/pressure_fatigue_results.json`과
`docs/PRESSURE_FATIGUE_KO.md`다. 기존 workflow.pressure_fatigue·thin_cylinder·
rainflow·basquin·miner를 실제 호출했고 별도 피로 엔진을 만들지 않았다. 원
OperatingHistory의 단일 origin은 압력 실측·온도 선언을 구별할 수 없어
model_prediction으로 전달하고 이쪽 receipt에 두 기원을 각각 보존한다.

수량별 origin·출처·단위·위치 매핑을 원 이력의 버전 있는 필드로 수용해 달라.
벽 온도를 유체 온도와 자동 동일시하지 않아야 한다. 관측 해상도·결측 분할과
창 사이 레인플로 잔여 상태의 전달·중복 계상 방지 계약도 검토해 달라. 현재
래퍼는 결측 창 전체를 보류하며 원 압력 기준이 미확정이면 호출하지 않는다.

완료 조건은 mixed-origin의 왕복 보존, 창 분할/재결합과 단일 창의 주기 비교,
같은 창 중복 반영 방지, 표본 해상도를 바꾼 기준과 적용 영역 검토다. 현재
손상은 1을 넘을 수 있고 실패확률이 아니다. 확률이나 누설 빈도가 필요하면
별도 재료·결함·노출 자료와 검증된 관계를 제공하고 단순 치환을 거부해 달라.

검사 계획 연결은 `inspection_planning.plan_inspections`,
`examples/inspection_campaign.py`, `research/inspection_planning_results.json`을 따른다.
원 logistic_pod/무지시 갱신과 공유 CP-SAT을 실제 호출했고, 유한 메뉴의 위치·방법·
예약 시각·예산·자원을 연결했다. 점수는 미발견 가중량이며 물리 파손 확률이나
수리 효과가 아니다. 무지시의 조건부 posterior와 무조건 기대 기여를 구분했다.

공개 ILI에는 13개 보고 후보가 있지만 독립 정답/미검출·허위 지시·교정 곡선·
실제 시간/인력/사전 질량이 없어 현장 계획은 보류다. 가정 곡선을 붙인 전체
13개도 4개 크기가 지원 범위 아래였다. 별도 9개 가상 부분 문제는 그 보류를
대체하지 않는다. 이전·이후 도구의 미대응을 곧바로 검출 실패 표본으로 삼지 말아 달라.

추가 요청은 검사자·시편·방법·절차·설비·크기 정의·정답 출처·탐지/미탐/허위 지시·
피복의 공간 정의·교정 범위·반복 관측의 군집 ID를 보존하는 버전 있는 입력이다.
검출 응답에는 점 추정/하한의 의미·불확실성·지원 범위와 출처를 보존한다.
여러 회차의 검출·성장·수리 경로는 같은 결함 계보와 시계에서 공동으로 다뤄야 한다.
서로 의존하는 반복 검사 효과를 독립 POD로 곱하지 않는다. 양성 지시 후 조치와
그 실패도 검사 결과와 별도 모델로 받는다.

최소 인수 기준은 기존 단일 회차로의 환원, 동일 시편 재검사의 군집 처리/거부,
미확정 검출 범위의 보류, 영 가능도 관측의 posterior 미정 보존, 고정 시각의
조건부 검출과 성장 경로의 일관성, 희귀 상태의 수치 질량 보존, 실제 조치 없는
검사를 위험 감소로 승격하지 않는 검사다. 작은 계획은 독립 메뉴 열거와 비교하고
대규모 계획은 하한·격차·반올림 오차 및 실행 가능한 자원 일정을 함께 반환해 달라.

TUBERS의 추가 자료 근거는 `nde.load_tubers_waveform`, `evaluate_echo_thickness`,
`research/nde_data_review.json`과 NDE_DATA를 따른다. 231파형의 원 second/Volt와
434000표본을 읽고, signed 시각·232전압 결측·4개 비증가 시각을 보존했다.
318개의 원 영상 대응과 7개의 고립 마스크도 기록했다. 폴더/파일명의 주파수·
두께를 표본율·교정·독립 정답으로 변환하지 않았다. 원 초음파 두께 모델의
5개 교환 사례는 가정한 에코·물성의 연결이며 현장 두께나 POD 보정이 아니다.

원 재료 API에는 버전 있는 에코 pick/구간 해석·시각 양자화·소재/온도별 음속·
시간 영점·입사각·불확실성의 수용 계약을 추가해 달라. signed 취득 시계의 0점과
공정 시계의 대응은 별도 근거로 받고, 실측/모의/가정과 원 행·원본 해시를 유지한다.
결측/누락/시각 역전을 정렬·보간으로 숨기지 않는 검사가 필요하다.

검출 회귀 자료는 독립 시편의 실제 크기와 검출/미검출/blank·허위 지시 및
검사자·기기·절차·환경·군집 ID를 함께 받아야 한다. 완전/준완전 분리의 대안
추정법을 지원하면 편향 감소·사전분포/벌점·신뢰구간 의미와 수용 근거를 명시하고
기존 미확정 결과를 단순히 수렴한 값으로 승격하지 않는다. 작은 독립 기준·분리
사례·반복 관측·범위 밖 질의를 검사하고, 필요한 외부 구현의 라이선스는 별도 검토한다.

### 압력·고체 열·단면 프로필의 계약과 에너지 판정

이쪽 MaterialJob/MaterialReceipt·PressureSection·solid_thermal_frames·원 봉재
reader 연결을 구현했다. 원 7모델의4압력 단면/16단일 사례·77CSV 판독을 실제
확인했다. MATERIAL_SECTIONS와 material_sections_results.json을 재현 기준으로 쓴다.
원 탄성·열전도·단면 계산기는 이 저장소에 복제하지 않았다.

우선 수정 요청은 thermal.transient_conduction_1d의 무열 단열 기준이다.
examples/material_sections.py의 heat_case()를 그대로 원 API에 공급하면 균일300K·
양쪽 단열·발열0인데 최대 절대 에너지 잔차 약4.619e-10J, 상대 잔차1로 현재
out_of_scope다. gross 열과 저장량 변화가 거의0인 분모의 반올림 민감성을 재현했다.
실제 단위 J를 가진 사용자 절대 허용값과 상대 허용값을 함께 받는 판정 및
각 오차·스케일·판정 근거를 반환해 달라. 일괄 tolerance 확대나 원 상태의 덮어쓰기,
순열량 상쇄를 이용한 큰 오차의 통과는 완료 조건이 아니다. 인수 검사는 무열·
작은 양/음열·상쇄된 경계 열·발열·여러 체적/열용량·단계 크기에서 에너지 장부와
변경 전후 상태를 비교하는 것이다. 이쪽은 현재 원 상태를 보존한다.

기하와 투영 계약도 확장해 달라. FVM은 셀 평균·cell_edges·cell_centers·실제
면적/길이를 반환하고 단면 모델은 평면 조건·전체 물리 두께·점/구간 의미를 받아야
한다. FVM 중심점 양 끝만으로 단면을 만들면 두께가 줄 수 있다. explicit projection에
원/목표 기하·평균/점 구분·보존량·끝점/표면 값·오차·허용값·근거/해시를 반환해 달라.
단축/등2축 평면 응력, 원통 반경/장축과 평면 변형률은 서로 다른 모델로 유지한다.
압력과 열을 합칠 때도 응력 성분·좌표·끝단 하중·선형 탄성 범위와 재료 물성을
명시해야 한다. 현재 원통 압력·평면 열응력 기준의 개별 성공은 일반 연성의 검증이 아니다.

모델별로 추가 native context metadata가 필요하면 허용 필드/스키마·이름공간과
버전/소스 해시를 공개해 달라. pipeline의 자산·위치·시계·근거 해시를 덮어쓰지
않으면서 원 context validator에 필요한 명시적 자료를 전달할 수 있어야 한다.
봉재의 excess K·controller counts와 raw run·실제 형상/센서 위치·fit 조건도 보존한다.
같은 자료에서 fitted한 물성/열 입력을 독립 예측 정확도로 세지 않는다.

### 균열 입력·정적/성장/FAD와 실제 원시 시험의 확장

이쪽 ModeIRequest·CrackGrowthRequest·SectionOpeningMapping과 원 FAD의 strict JSON
gateway를 구현했다. FRACTURE_EXCHANGE와 fracture_exchange_results.json의20사례,
원 crack-arrest18CSV/79341행이 재현 기준이다. 원 형상/개구 하중·인성 정의/재료/
온도·calibration/회수·중간 궤적·종료 이유를 유지한다. Mat의 계산기는 복제하지 않는다.

실제 K provider에는 균열 방향·내외벽/깊이·길이/형상·벽/부재 기하·하중 좌표,
원 총응력과 crack-face pressure의 정의·분포·잔류/열/상호작용·해/소스 ID와 적용성
범위를 요청한다. 현재 scalar total-normal+uniform-face-pressure correction은
명시한 연구 변환이며 임의의 표면 균열·응력 구배의 일반 해가 아니다. 원 critical
depth가 geometry/wall 범위 밖일 때 raw 값과 unqualified 상태를 같이 반환해 달라.

성장 API에는 실제 C/exponent의 SI convention·ΔK/R/온도/환경 지원·threshold·
자격 있는 저항 정의·순서/계수화 근거·회수↔시간 관계를 보존한다. 감소하는 Y표,
혼합 모드·폐구·과대하중 지연·환경/시간 의존성은 다른 자격 있는 provider/모델로
구분한다. 순서를 유지하는 것만으로 하중 상호작용이 구현됐다고 표시하지 않는다.
보정 한계로 정지한 partial 결과는 전 requested blocks의 완료가 아니다.

원 FAD에는 K·reference-stress 해·정규화 강도·인성의 물리 조건과 실제 load/location/
material/source digest를 수신 경계에서 확인하는 계약을 요청한다. 이쪽은 하중/
location·근거 ID를 검사하지만 native caller 전체를 대신하지 않는다. 사용자 허가
경계·cutoff·근거와 interpolation/support를 보존하고 표준 곡선을 이 저장소에 넣지 않는다.

raw C1/C2는 signed machine/extensometer/force·source cycle index·filename 온도와
한 header 표기 결함을 유지했다. 실제 시편 기하·균열 길이·동적 reduction·기기
교정·실제 온도/유효성과 개시/정지/tearing의 정의를 검토해야 한다. 이를 검토하기
전 KIC·Paris calibration·실제 dynamic arrest·service lower bound를 생성하지 않는다.
후속 개시/성장 자료도 원 시편·단위·검열/조건·권리를 확인한 독립 자료로 확보한다.

인수 검사는 현20사례·숫자 전체 대조의 유지, 정의/재료/온도/기하/응력/하중·시계
불일치의 보류, geometry 한계·zero/compression·threshold·R·toughness/calibration/depth
종료의 실제 왕복이다. 자격 있는 K/reference-stress·일반 균열면 압력 해와 실제
성장/파괴 결과는 별도 독립 검증한다. 원 공개 source release/tag·스키마·라이선스·
해시와 설치 경로도 제공해 GitHub만으로 소유 엔진 의존성을 재현할 수 있게 해 달라.

## PI-X05 — 모니터링: 물리 잔차와 평가 프로토콜

현재 SignalBatch의 기존 metadata에 시계·스트림과 추가 문맥을 전달할 수 있다.
물 성분 수송의 실제 전달에서 `source_result_digest`, `asset_id`, `scenario_id`,
`quantity_meaning`, `input_context_origin`, `parameter_source_id`를 보존했다.
새 metadata 저장소를 중복 구현할 필요는 없다. 이 필드와 `port_id/property_basis`의
버전이 있는 계약을 정하고 탐지 결과까지 유지해 달라. 입력은
`water_quality_batch` 및 `research/water_quality_results.json`의 합성 기준 전달을 따른다.
단위·샘플링·결측·에피소드 변화에서 검출기를 재설정하는 정책을 명시한다.

실제 음향 기록의 7창×4특징도 기존 SignalBatch로 전달했다. 입력은
`delay_feature_frames`와 `research/acoustic_results.json`을 따른다. 단위는
시간차 s와 상관/봉우리 차이/코히어런스 1이다. 이 특징은 누설 확률이 아니다.
물리 시계 보정이 미확정인 apparent_delay를 정정된 도달시간으로 승격하지
않아야 한다. 결과 해시·입력 laboratory·동일 기록의 창 그룹을 metadata로
유지하고 분할·평가 시 같은 실험의 인접 창이 양쪽에 섞이지 않게 해 달라.
품질 보류 창의 valid=False도 이후 탐지 결과와 함께 추적해야 한다.

신규 배관 알고리즘은 질량수지 잔차·압력파·음향 도달 시간 등 기존 검출기와
다른 물리 정보가 필요할 때 추가한다. 최신 모델은 동일한 시간·실험 분할에서
강건 점수/PCA와 비교하고 학습·교정·평가를 분리한다. 누설 정답·미래 신호·
동일 실험의 인접 표본이 훈련으로 누출되지 않게 한다.

완료 조건은 유효 결측 보존, 채널·단위·시계 불일치 거부, 사건 단위 precision/
recall·지연·시간당 오경보·미검출·조건별 불확실성이다. 점수를 확률로 쓰려면
별도 확률 교정과 검증을 요구한다. 경보 자체를 HEP로 출력하지 않는다.

현재 37채널의 초기 실행에서 robust_z 교정 임계값이 약 2.04×10⁶으로 컸다.
훈련 채널의 0-MAD·준상수 구간을 진단하고, scale_floor와 정규화의 의미,
채널별 기여와 적용 보류 정책을 노출해 달라. 경보 1건이라는 집계만으로
기법의 적합성을 판단하지 않는다. 이 문제는 기존 모니터링 프로젝트의
스케일 정책에서 검토하고 여기에서 검출기를 중복 구현하지 않는다.

### PI-X05 추가 — 동압 취득·교정·센서쌍 원근거

새 `PIPE-EXT-010`은 동압 CSV의 원 취득/교정 소유자와 모니터링 ingestion 경계에
전달할 계약이다. 센서별 archive/member SHA, 저장 열, 정확한 Pa 변환식과 압력 기준,
좌표→초 변환식·시계 원점, nominal frequency와 실제 hardware channel/교정 근거를
한 버전 객체로 요구한다. 파일명·출판 주파수·같은 prefix 좌표로 이 값을 만들지 않는다.

P1/P2 공통 trigger·offset/drift·불확실성·정렬 정책, 측량한 센서 위치와 좌우/노드
역할도 별도 근거 ID로 받아야 한다. 누출 작동/실제 발생, 배경 유량 transient와 검출한
특징 도착은 서로 다른 event field다. 변경한 source/mapping/trigger/role/event는
qualification→trace bridge→NPW request의 모든 기존 receipt를 native 특징 탐색 전에
무효화해야 한다. 상세 전달 문서는 `docs/owner_requests/PIPE-EXT-010.md`다.

수락에는 complete P1/P2 원파일 재생, 지원 진폭·기준압·시계의 독립 교정 대조와
held-out known-event의 도착오차·오탐/미탐 평가가 필요하다. 현재 두 prefix와 공개 장비
metadata만으로 이 요구를 만족했다고 표시하지 않는다.

## PI-X06 — HRA: 관측 상황과 동작 완료의 구분

원 관측 바인딩, research 상태와 includes 보존, 기회 노출 변환 및 원 엔진의
회복 중복 거부는 실제 확인했다. 이 검증을 다시 구현하지 말고 배관 작업의
요구·실패·회복·시각·작업조와 현장 노출을 가진 자료 계약을 확장해 달라.
현재 타 분야 시뮬레이터 관측의 분야 전이는 연구 예제이며 배관 보정값이 아니다.

현재 시계·압력 수두·valid를 SignalSeries로 전달할 수 있다. 상황 신호가
작업 조건으로 해석되는 변환에 mapping_id·version·근거·검토 상태를 요구한다.
물리 위험 수준과 인간의 실패확률 사이에 임의 선형 관계를 두지 않는다.

감지·인지·진단·명령·물리 완료의 시각과 회복·의존성 반영 여부를 물리 엔진에
전달하는 교환 예제를 추가한다. 완료 조건은 시계 불일치 거부, 미지의 신호를
정상 조건으로 바꾸지 않는 검사, 감지 실패와 인간 실패 중복 계상 방지,
동일 HEP가 요구 횟수 없이 연간 빈도가 되지 않는 검사다.

액체 밸브의 실제 구동 궤적 161개를 원 SignalSeries로 전달했고 unit=1,
elapsed 시계와 origin=simulation을 확인했다. 현재 원 객체에는 수량 의미·
채널명·자산·소스 해시를 보존하는 필드가 없어 이쪽 receipt를 별도 보관한다.
버전이 있는 quantity/channel/asset/source metadata를 추가해 개도 0~1과 HEP
0~1을 구별해 달라. 재현은 `research/valve_transient_results.json`을 따른다.

구동 지연은 장치 상태이며 그 자체가 인적오류 확률은 아니다. 명령 성공·
목표 개도·공정의 안전 상태를 별도 정의하고, timing을 이미 포함한 HRA 추정에
같은 지연 효과를 다시 곱하지 않는 왕복 예제를 제공해 달라. 승인된 현장
자료와 의미 매핑이 없으면 계산 궤적에서 HEP를 생성하지 않는다.

작업 계획의 추가 계약은 `schedule_exchange.HRAPlanningBinding`과
`planned_hra_steps`, 예제 operations_schedule.json, 실제
`research/scheduling_results.json`을 따른다. 원 Task·ordered_steps·
analyze_task와 실패 사건 정의를 호출했고 별도 HRA 작업 엔진을 만들지 않았다.
원 4단계와 역할·선행 관계를 명시적 계획으로 연결했으며 정성 후보 5개를
변경하지 않았다. 원 실패 사건의 조건·기회 정의에도 확률을 부여하지 않았다.

계획/관측 구분이 있는 단계 시각·clock_id·0점·단위와 역할-자원 pool의
버전 있는 수용 계약을 추가해 달라. TaskStep.deadline_s의 기준을 신호/단계
시작/계획 0점 중 무엇인지 명시하고 anchor_event_id를 받을 수 있게 한다.
현재 래퍼는 기한을 자동 latest_finish로 변환하지 않고 별도 검토로 보존한다.
전제 충족·차단·실제 완료가 검증됐다고 표시하지 않는다.

역할 자원 예약은 자격·개인 의존성·회복 성공을 증명하지 않는다. 모드와
달력·실제 인력 대응의 근거를 유지하고 계획의 촉박함이나 최적 비용을 HEP로
바꾸지 말아 달라. 검토된 상황 매핑을 적용하더라도 원 추정이 timing을 이미
포함했는지 확인해 같은 영향을 다시 곱하지 않는다. 최소 인수 검사는 시계
불일치·기한 기준 누락·planned→observed 승격·사건 조건 변경의 거부, 역할
매핑과 원 HRA 정성 결과·기회 정의의 보존이다. 원 Task에 계획 필드를 넣을
때는 현재 strict 스키마에 임의 키를 추가하지 말고 버전 정책을 확정해 달라.

시간·복구 연결은 `response_exchange.HumanResponseClient`,
`examples/human_response.py`, `research/human_response_results.json`을 따른다.
원 인지 단계·로그정규/Weibull/KM·기한·완료 구간·복구·FTA·연간 환산을 실제
호출했다. cue와 limit은 같은 context 원점의 경과 초이며 원 HFE의 기한은 그
차이다. 공개 SACADA의 분리 불가능한 timing/recovery는 중복 적용을 거부한다.

현재 배관 어댑터에는 근거가 붙은 이산 기한 시나리오 집계가 추가되었다. 각 시나리오는
가용 시간과 선택적 조건부 반응시간 모델을 한 행으로 보존하고, 기존 원 HRA 고정
기한 결과에 검토 가중치를 적용한다. 공동 분포의 수집·추정·검증은 하지 않으며,
사용자가 제공한 자료 분할·가중치·근거의 책임을 유지한다. 세부 API는 HUMAN_RESPONSE_KO를 따른다.

후속 원 엔진에는 확률적인 사용 가능 시간과 필요한 시간의 **공동** 입력,
기한 내 복구, 경쟁 실패를 지원하는 버전 있는 API를 요청한다. 주변 분포만
있을 때 독립을 기본값으로 가정하지 말고 공동 표본·의존 모델·명시적 독립
근거 중 하나를 요구한다. cue/limit/완료/복구 시각의 기준과 조건부 모집단,
trial/작업조/설비/시나리오 ID·출처·중단 이유·오반응을 보존해야 한다.
시뮬레이션의 시각 변동과 파라미터 불확실성도 다른 층으로 기록한다.

최소 인수 기준은 고정 한계의 기존 결과로 환원, 공동 표본의 조건 보존,
기한 뒤 복구를 성공으로 인정하지 않는 검사, 잘못된 반응의 중단 재분류
거부, 미확정 꼬리 밖 결과 보류, 시계/모집단 변경 거부, 포함 효과 중복 거부다.
현재 단일 효과 API에 순서만 바꿔 timing과 recovery를 연달아 적용하는 것으로
공동 모델을 대체하지 않는다. 파라미터 추정·적용성·불확실성 수용 기준과
독립 자료 검증을 문서화하고 원 계산 엔진의 구현을 이 저장소에 복제하지 않는다.

### 제어 명령·계측 가용성과 HRA의 증거 경계

PIDSession의 decision/measurement/available 시각과 status·reason, 원 SignalFrame
origin·clock·기준/근거 및 actuator_action_or_safety_authorization=False를 보존해
달라. 이번 81표본 native SignalSeries는 원 실험 관측이다. 800개 계산 명령과
드리프트/지연/시계 오류 시나리오를 observed 조작 완료·대응 기한·작업자 실패율로
승격하지 않는다. 데이터 공백 때 반환한 고정/이전 명령도 방호 성공이 아니다.

수신 HRA에서 계획된 명령, 수신 확인, 실제 위치·유량·보호 상태, 사람의 확인/
복구 관측을 별도 증거로 받는다. 공정 시간 한계와 작업 기한의 근거를 명시하고
제어·센서·사람의 동일 실패 경로를 중복 계산하지 않는다. 인수 검사는 시계 충돌·
조건/기한 누락·미도착·명령만 존재·관측된 완료·공통 원인의 실제 왕복과 보류다.

### 고체 온도와 주변 대비 온도차의 신호 의미

원 봉재의1937표본을 native SignalSeries로 전달했지만 unit=K만으로 절대 온도와
주변 대비 차이를 구분할 수 없다. thermal_rod_frames의 channel 이름·temperature_basis·
원 source/run/member hash·clock과 명시적 ambient 근거를 함께 받을 수 있는
신호 의미 계약을 확장해 달라. 절대 온도 한계와 excess K를 자동 비교하지 않는다.
계산된 셀 평균 온도는 simulation이고 원 관측은 laboratory다. 원 평균/점/표면·
위치·관측/계산 근거를 보존하고 임계값·기회·사람의 실패 사건으로 바꾸기 전에
별도 바인딩을 요구한다. 인수 조건은 기준 누락·ambient 불일치·다른 실험 시계·
셀/센서 위치 충돌의 보류와 명시한 대응 관계의 실제 왕복이다.

## PI-X07 — SAGA와 통합 서비스: 버전이 있는 분석 요청

기존 호출 경로 차이를 정리하고 API 버전·요청 스키마·오류·근거 반환 형식을
고정한다. 검색·분석 후보와 승인된 수치 계산 결과를 구분한다. 완료 조건은
API 계약 검사, 지원하지 않는 버전의 명확한 오류, 출처·모델 버전 추적,
실패·미지 응답의 정상 결과 승격 방지다.

## PI-X01/04 추가 — 프레임 하중 경로와 재료 응력 복원

수신 대상은 물리 장치/배관 엔진과 MatIntegrity다. 이쪽은
pipeline_integrity.structural의 FrameRequest·FrameNodeLoad·FrameLineLoad·
FrameSignalLoad 및 solve_frame/frame_signal_frame을 먼저 구현했다. PyNite
선형 계산·25검사·14사례·8수치 기준·원 signed 하중32snapshot을 실제 실행했다.
현재 부재력은 원 로컬 축/부호를 유지하며 국소 응력과 현장 승인으로 바꾸지 않는다.

이후 FrameInitialStrain과 FrameLoadContribution·prepare_uniform_thermal_frame·
prepare_pressure_frame·solve_loaded_frame을 구현했다. 같은 원본 프레임에 준비한
열/압력 기여를 조합하고 실제 부재력·노드 수지를 복원한다. 26새검사·20사례·
원 온도차16snapshot을 확인했다. 온도는 균일/상수 계수, 압력은 독립 직선 관의
total cap와 Poisson 축변형률을 지원한다. 나머지 공급자 확장의 현재 기준은
examples/frame_loading.py와 FRAME_LOADING_KO다.

PI-X01의 기계적 하중 제공자는 asset/clock/time, node/member/port ID, 검토한
좌표와 하중 경로, 전역 힘[N]/모멘트[N·m]/선형 하중[N/m], origin·근거와
적용 범위를 반환해 달라. 압력은 각 면의 기준·유효 면적·끝 조건과 외부
압력의 취급을 명시해야 한다. 닫힌 관의 축응력·끝단 추력·지지 반력에 같은
작용을 중복 적용하지 않는다. 유동 기세·중량·지지와 유체 상태의 같은 시각을
확인하고 예측 시간 보간·좌표 회전·상대압 추정을 자동 수행하지 않는다.

온도 교환은 절대/주변 대비 온도차·무응력 기준·구간 평균/분포·E/α의 상태와
근거를 구분한다. 열 고유변형률은 단순한 실제 외부 집중하중과 다르다.
등가 절점 하중만 넣고 원 부재력을 그대로 읽으면 자유 팽창의 응력이 잘못
남을 수 있다. 현재 균일 축 ε0에 대해서는 등가 힘 제거와 실제 부재력을 복원한다.
원 공급자는 온도 구배/초기 곡률·상태별 E/ν/α·무응력 기준·단위/원 시계·
근거와 적용 영역을 버전 있는 API로 제공해 달라. 비균일 열/곡률은 아직
지원하지 않으며 이미 구현한 균일 축 경로로 대체하지 않는다.

압력 공급자는 각 면의 absolute/gauge+ambient·실제 끝 조건·단면·노드/포트
위치·공유 재고와 실제 시간 이력을 연결해 달라. 현재 일치한 원통의 σr+σθ를
읽어 -ν(σr+σθ)/E를 적용하고 cap traction을 별도 실제 하중으로 둔다. 분기·
엘보·유동 기세·기하 강성·벽 변형과 유체의 양방향 피드백은 지원 밖이다.
원 닫힌 원통의 σz를 복원 σz에 또 더하지 않도록 포함 효과를 명시해 달라.

PI-X04의 재료 응력 복원은 명시한 단면 위치·내외벽/원주 좌표·로컬 축·
N/Vy/Vz/T/My/Mz의 부호와 근거를 받도록 확장해 달라. 명목 굽힘/축/비틀림과
압력의 total/effective stress·열 구속·잔류 응력의 포함 여부를 분리한다.
엘보/티/벨로즈 유연성·국부 응력 집중의 공급자에는 기하·적용 영역·판/근거·
단위와 재배포 조건을 요구한다. 원 변형 에너지·좌표계·응력 원인을 지우고
von Mises 한 값으로 대체하지 않는다. 계산한 응력을 곧바로 인성·HEP·파손확률로
변환하지 않는다. 지반/간극/마찰·좌굴/동역학도 현재 선형 엔진의 결과와 구분한다.

최소 인수 기준은 자유/완전 구속 직선 관의 균일 온도, 무압/내외압 끝 조건,
단면 회전과 부호 반전, 단순/탄성 지지, 조합의 지점 이동 의미, 부재 끝 해제,
열/압력의 중복 거부와 미확정 하중의 보류다. 물리 자격 주장에는 독립 실험의
형상·단위·경계·시계·교정과 사전에 선언한 오차 기준이 추가로 필요하다.
작성 기준만 통과한 연결과 실제 시험체 검증을 함께 기록해 달라.

## PI-X02/05/06 추가 — 전력·작업·위험 손실의 비용/배출 근거

이쪽의 LifecycleStudy·CostEvent·EmissionEvent·ValuationSource 및
EnergyValuation·ScheduleValuation·ExpectedLossValuation을 먼저 구현했다.
examples/lifecycle_study.py와 LIFECYCLE_KO·research/lifecycle_results.json을
수신 계약으로 사용한다. 25새검사·9사례·전력24000원값·실제 일정5작업·원 HRA
기대횟수6회/년을 연결했으며, 시장 가격·실제 duty·수명·손실은 가정으로 남는다.

PI-X05의 신호 공급자는 quantity type, point sample/interval mean/cumulative
meter 의미, 관측 창의 시작/끝·유효 구간·원 시계·signed import/export 의미와
단위·교정 근거를 전달해 달라. 현재 point-linear EPS1은59.99초만 적분한다.
관측하지 않은 마지막0.01초를 60초 cycle의 평균/구간값으로 임의 채우지 않는다.
독립 cycle을 연결하거나 sample 간격을 운전 횟수로 바꾸지 않는다. 인수 조건은
0교차·결측·긴 gap·원 시계·정확한 구간 에너지의 실제 왕복이다.

PI-X06의 작업 공급자는 계획/실제·취소/부분 완료·작업/대기/중단·작업조/자산·
시계와 관측 source ID를 구분해 달라. 현재 일정 objective는 통화가 아니며
별도 고정/기간 요금을 쓴다. 실제 작업의 타임라인과 비용 지급 시각/원점
매핑을 독립 근거로 받고, 겹친 자산 중단 구간의 union과 실제 손실/매출 경계도
제공할 수 있게 요청한다. 계획 makespan이나 원 Task duration을 실제 downtime으로
승격하지 않는다. 최소 인수는 모드 변경·중복 과금·취소·미완료·시계 충돌과
관측/계획의 의미 보존이다.

PI-X02의 위험 공급자는 연간 기대 count의 year 정의·조건·endpoint·소비한
사건/방호와 배타적 결과 partition을 보존해 달라. 조건부 손실 공급자는
loss component·통화/가격 기준연도·실질/명목·조건부 평균의 모집단과 지급
시점/기간·포함한 복구/정비/중단/credit·적용 근거를 명시한다. 현재 같은 빈도
binding+손실 성분의 기간 중복만 차단한다. 다른 부모/자식·공유 사건의 비용
배타성은 자동 증명하지 않는다. 포트폴리오 합산 전 disjoint endpoint/partition
또는 명시한 joint loss 근거를 요구하는 계약을 확장해 달라. 기간 확률·불가용도를
기대 count로 바꾸거나 평균 손실을 손실 quantile로 표시하지 않는다.

위험/작업의 모델 불확실성과 재무 요금/서비스 수명의 공동 시나리오는 shared
scenario ID·가중치·의존 근거와 함께 제공해 달라. 주변 분포만으로 독립을
가정하지 않는다. 원 생애주기 계산은 선언 재고의 비교이며 전체 투자/정비
최적화나 독립 현장 경제성 검증은 후속이다. 원 근거/자격과 명시적 source
매핑이 일치한 실제 호출을 완료 조건으로 요청한다.

## PI-X02/04/05/06 추가 — 공동 표본·원 짝·불확실성 의미

이쪽 수신 계약은 uncertainty.EnsembleRequest/EnsembleNode/EnsembleValues와
UNCERTAINTY_KO다. 각 노드는 같은 design_digest/draw_id를 받고 물리 시간과
분리한다. 기존 HRA의 demand_frequency와 펌프/에너지/생애주기를 실제 호출한다.
외부 저장소를 이 세션에서 수정하거나 자체 HRA/재료 엔진을 복제하지 않는다.

PI-X02는 HEP 분포가 모수 불확실성인지 운전조건 혼합인지, event/condition/
opportunity·포함한 회복/의존성·근거/자격·표본 ID를 보존해 달라. 같은 HEP의
반복 draw를 새 오류 기회나 관측 시계로 해석하지 않는다. 연간 기회·year 정의와
조건부 손실을 명시하고 공유 원인/배타적 endpoint를 갖춘 joint 결과를 요청한다.
PI-X04는 물성·결함·측정 편향의 동일 시편/결함 행 ID와 공간/시간 공통 변수를
유지하고, 모델 형식 오차와 원 측정 오차를 구분해 달라. 압력/온도/형상 단위와
모수의 유효 지원 범위·분포 적합 근거·재사용 조건·보류 상태를 제공한다.

PI-X05/06은 원 센서/작업/실제 기회의 paired row ID와 계층·시계·교정·원본 hash를
제공해 달라. 주변 데이터 열을 따로 섞거나 서로 다른 속도의 신호를 암묵적으로
보간하지 않는다. 같은 회차의 공통 관측 구간을 제시하고 결측/미완료를 보존한다.
변수의 독립은 명시적 근거로 선언하고 Gaussian copula 행렬이 잠재 정규 상관임을
기록한다. 실제 변수 Pearson이나 시나리오 가중치를 모집단 확률로 표시하지 않는다.

인수 조건은 같은 draw ID/원 context의 실제 왕복, 미상/범위 이탈 비중 유지,
외부 엔진 내부 오류의 전파, 수정된 단위/자산/시계의 거부, 완전 상관과 원 행
짝 보존 기준이다. 모델의 선언 분포 전파와 독립 자료 기반 추정/검증을 구분한
결과 및 버전/소스 해시를 제공해 달라. 미계산 draw를 삭제하거나 재추출한 평균은
인수하지 않는다.

## PI-X02/04/05/06 추가 — 학습 원 그룹·조건·고정 평가

이쪽 uncertainty_fit의 JointObservations/DistributionFitRequest/DistributionUse와
UNCERTAINTY_FIT_KO를 수신 계약으로 요청한다. 원 그룹·조건·원 순서·원본 해시와
명시한 학습/평가 ID를 받고 normal/lognormal/순위 상관 적합과 별도 평가를 구현했다.
원 유압2205회차·128학습/64평가와 모수/상관의 holdout 불변성, 안정/정상안정19/10행의
자료 부족을 확인했다. 다른 세션에서 기존 엔진을 복제하지 않고 계약을 확장한다.

PI-X05/06은 acquisition/실험/운전 구간의 실제 block ID와 시각 및 상태 라벨의
확정/미확정을 제공해 달라. 같은 부품 조건 라벨이 같은 독립 실험을 뜻하지 않는다.
원 행의 짝과 품질·미완료를 유지하고, 중복/인접 작업·공통 교정/작업조를 그룹에
표현한다. 최소 인수는 원 그룹 겹침 거부, 순서 gap 보존, 다른 운전 조건 보류,
미상 학습 행을 지우지 않는 실제 왕복이다. source row index를 초로 바꾸지 않는다.

PI-X02의 원 HRA 추정기는 기존 모수/사후 분포 및 기회/성공/실패 자료의 의미를
재사용해 표본 ID·원 estimate/event/condition/opportunity·근거/자격과 함께 전달해
달라. 계획 작업 완료나 물리 신호 회차를 HEP 관측으로 바꾸지 않는다. 여기의
normal/lognormal 적합은 기회 횟수의 별도 HRA 추정기를 대체하지 않는다.
PI-X04는 동일 결함/시편·검열/미검출·교정 편향·원 실험 그룹과 지원 범위를
보존한다. 평가 자료로 적합값/척도를 변경하면 새 평가 자료가 필요함을 기록한다.

완료 기준은 원 엔진의 실제 호출·버전/소스 해시·고정 split 재현·미상 비중 보존,
학습 모수를 바꾸지 않는 holdout 변경 검사와 criteria 실패/자료 부족의 보류다.
진단 점수 통과·적합 status·독립 검증/현장 자격을 별도 필드로 유지해 달라.

## PI-X04 추가 — 전기화학/방식의 원 경계와 전위/부분전류

수신 계약은 ElectrochemicalRequest/SurfaceCurrentReduction과 ELECTROCHEMICAL_KO다.
이쪽에서 원7모델 실제 호출·10독립 기준·22검사·원24요약/면적 보류와12조건부Fe를
확인했다. 원 엔진을 복제하지 않고 strict MaterialJob으로 원 스키마/버전/단위를
고정했다. 다른 세션에서는 기존 원 API를 확장하고 실제 왕복으로 검증해 달라.

전극/표면 공급자는 기준전극 명칭·전해질·온도·노출 면적·표면/피복·스캔/시간·
분기·극성·저항 보상과 원 근거/단위를 제공해 달라. Ω와Ωm2를 구분하고 area가
없으면 추정하지 않는다. 원 signed net current와 partial dissolution/효율·전자당
당량/모재/피복 혼합 용출을 별도 계약으로 보존한다. 전기방식 요구 전류를 손실
전류로 바꾸거나 unsigned 로그 전류에서 부호를 복원했다고 주장하지 않는다.

기존 곡선 기반 혼합전위 공급자는 각 실제 전극의 면적·공통 기준·원 branch/point ID·
겹치는 전위 지원·전류 보존·복수/비유일 평형·미해결 동적 안정성을 보존해 달라.
선형 공급자의 범위 이탈을 임의 외삽하지 않는다. 전기방식 공급자는 양극 고갈/
전류 출력 한계·구조/전해질 저항망·차폐/간섭·보호/과보호 전위와 원 기준의 공급자
판/조건을 명시해 달라. 현재 전하/순간 전류 예산의 satisfied는 전위 자격이 아니다.

최소 인수는 원 면적/기준/부분전류 미상 보류, 부호 반전과 순전류≠용출량,
full-area 밀도의 bare fraction 중복 거부, f와j의 동시 변화/구간 내부 최대,
전극 평형의 current residual·support/조건 충돌과 원 해시/시계 보존이다.
원 실험과 조건부 재료/효율 가정의 계산, 실제 현장/규격 자격을 구분해 기록한다.

## PI-X04 추가 — 검열 크리프 수명과 변동 조건

수신 계약은 CreepRequest/CreepDomain/CreepSequence/CreepRiskMapping과 CREEP_KO다.
이쪽에서 원7모델·29새검사·12기준·CMSX-6 12시편9파단/3검열·140032행을 실제
확인했다. 원 시편/trace 판독기와 구성식·적합기를 복제하지 않고 원 receipt와
지원/확률 의미를 보존했다. 한 온도의 활성화에너지 적합은 needs_data였다.

원 공급자는 물리 시편·용해/열처리/방향·명목 초기 응력과 true stress·하중/응력
제어·설정/실측 온도·fresh material/하중 종료 원점·종료 사유·검열을 버전 있는
스키마로 제공해 달라. 반복시각/미검출·고장기구 변경·시편 종속성과 실제 관측의
불확실성을 보존한다. 이력 샘플을 독립 시편으로 만들거나 시험 종료를 파단으로
채우지 않는다. 실제 강관/고온 운전 재료의 전이 근거를 별도로 제공해 달라.

모수 불확실성은 기존 원 적합기에서 시편/시험군 단위 재표본·추정 실패 비중·
지원/식별성·검열 설계와 함께 제공해 달라. 점추정 모집단 산포와 분리하며
검열이 있는 표본의 단순 행 복제나 지원 밖 분위수 외삽을 자동 허용하지 않는다.
다온도 적합·변동 응력/온도·누적 손상·크리프–피로 확장은 원 모델과 기구별
근거/검증 사례로 제공해 달라. 현재 시간비0.15는 기간확률이 아니다.

최소 인수는9파단/3검열의 유지·한 온도 활성화에너지 보류·제어/온도 기준
불일치·원점/지원 이탈·모수 불확실성 전파와 실패 비중 보존·원 해시 왕복이다.
독립 작성 기준과 별도 실제 시험/평가를 구분하고 현장 검증 공백을 기록해 달라.

## PI-X03/06 추가 — 생존 기간확률의 의미와 로컬 연계 책임

원 HRA의 일반 Boolean 함수는 단일 기간 사건을 이미 계산한다. 이 패키지의
HRARiskClient는 demand 전용이며 별도 다사건 공통 기간 어댑터를 이쪽
period_risk에 구현했다. PERIOD_RISK_KO의 실제6기준·공유 모델 보류·명시적
두 공동 시나리오를 수신 계약으로 사용한다. HRA 엔진과 FTA를
새로 작성하거나 기존 demand 의미를 완화할 필요는 없다.

외부 위험/HRA 서비스는 mission_probability·정확한 시작/종료·시작까지 생존·
재료 나이 원점·물리 endpoint·조건·원 모델 digest·의존 그룹·이미 소비한 사건과
방호 계보를 수용하는 metadata 확장을 검토해 달라. 같은 수명 모델의 여러
사건을 자동 독립으로 처리하지 않고 공동 상태/공통 모수의 근거를 요구한다.
연간 빈도가 필요하면 재생/교체·복구·반복 노출 모델과 검증을 별도로 명시한다.
인수는 기간/나이·조건 불일치 보류, p/demand/frequency 구분, 공유 모델 의존성,
원 파단에서 봉쇄 상실로의 미검토 전환 거부와 출처/자격 보존이다.

현재 구체적 수신 형식은 RiskPeriod/PeriodEventBinding/PeriodLogicRequest다.
target_context_digest와 target_period_digest는 전체 근거/context 및 조건 설명을
포함한다. 같은 condition ID 아래 설명이 달라져도 원 검토를 재사용하지 않는다.
원 source 확률은 변경하지 않고 시계·시간창·공통 조건 매핑을 별도 근거로 준다.
관측 공동 상태/공통 모수의 추정을 확장할 때는 research/period_risk_results.json의
가정 공동 시나리오와 구분하고 학습 원 그룹·실패 비중·독립 평가를 함께 제공해 달라.

## PI-X04 추가 — 압력 시험·몰 재고와 변형/계측 근거

수신 계약은 PressureTestRequest/GasDecaySpec/MolarGasState와 PRESSURE_TESTS_KO다.
이쪽에서 원4모델·12수치 기준·23새검사·공개10이력/310행의 보류/가정 시나리오를
확인했다. 원 구성식과 thermo/단위/MaterialJob을 복제하지 않았다.

공급자는 각 시험/기체/상태의 실제 체적과 압력/온도/시간 의존 변형, 고립 경계·
펌프/유입·대표 기체 온도/구배·대기압/기준 상태·조성/습도·센서 교정·분해능과
공분산·지연을 제공해 달라. 미검출/반복 시험·원 시계/결측·열/압력 안정 조건을
보존한다. 데이터 설명에만 있는 reference/ambient 열을 만들어 넣지 않는다.

원 가변 체적/혼합·기체 방출/용해/투과 공급자를 확장할 때 순 재고 감소와 누설
경로 유량을 구분하고 불확실성/미계산 비중을 제공해 달라. 몰밀도 입력 또는
R 관례를 스키마에 명시해 물성 Z와 원 R의 불일치를 피한다. 현재 이쪽은
Dmolar에서 Z_native=P/(rho_molar R_native T)로 연결하고 원 몰수 수지를 대조한다.

시험 규칙 공급자는 원 판·적용 조건·허용치/시험 배율·국소 고도/게이지 기준과
검토 권한을 제공해 달라. 계산한 하중·이상 팽창 일·진공 부하를 시험 합격·
강도·폭발 피해량으로 자동 변경하지 않는다. 인수는 냉각만의 압력 감소·음의
순 유출/배경·기체/액체/혼합 조성 미상·용적 변형/계측 공분산·중간 결측/
시간 공백·원 checksum/receipt/R 기준의 보존과 독립 시험을 포함한다.

## PI-X04 추가 — 용접 전기/열·조성·형상·절차 근거

수신 계약은 WeldingRequest/WeldWaveformSpec/WeldThermalSpec 및 WELDING_KO다.
이쪽에서 원8모델/22기준·EBW7cooling/5fullband/26기준·Tandem4원기록/명시창4개를
확인했다. 원 signed 채널과 일치한 시간창·actual거리/열효율·assay기준·용융 질량·
센서 위치/절차 band·원 receipt와 미상값은 그대로 보존한다.

공급자는 원 U/I의 동시성·극성/measurementport·clock drift/latency·공백·스위칭의
음의 전기 교환, 이동 경로와 속도/길이 기준·가변 열효율·calorimetry/소재 근거를
제공해 달라. 평균 U×평균 I나 와이어 이송량으로 입열·용융 질량을 대신하지 않는다.
조성은 masspercent/massfraction·검출한계/미상·열처리 상태·소실/산화·편석을 구분한다.
기본 강종 표에서 미지 원소를0으로 채우지 않는다.

온도/센서는 위치·응답 지연·교정/오류·가열/재가열·냉각 가지와 목표 교차를
제공한다. 전체 noisy curve에서 행을 삭제해 비증가로 만들지 않는다. geometry는
좌표/축·내외면·sampling·용접 제외영역/기준면·하중 복원 근거를 제공한다. 경도는
방법/힘/유지·단위/불확실성·영역/원 위치와 미측정영역을 보존한다.

원 예열/수소/야금/균열/품질 모델과 승인 규칙 확장은 원 판/기준 공급자로 제공해
달라. 이쪽의 지표/규칙 만족은 전체 규격·용접부 합격·파손확률이 아니다. 인수는
미지 조성/효율/거리·원근거/시간충돌 보류, 음의 전기 교환·gap/결측 보존,
다중 열 교차/재가열·좌표/기준면 범위 이탈·경도 unknown영역과 같은 원 실험
그룹을 유지한 독립 평가·실제 원 API 왕복을 포함한다.

## PI-X06/03/01 추가 — 경보에서 command와 공정 endpoint까지

이쪽 수신 계약은 AlarmReplayResult, ResponseTraceSpec/SampledEndpointRule,
HumanResponseResult, ValveMotionResult다. RESPONSE_TRACE_KO의 원3엔진 사례와
ALARM_SOURCE_KO의864공개행을 먼저 재현한다. source-bound activation은 기록
전이일 뿐 true onset이 아니며 공개JSON만으로 원 frame 검증을 복원하지 않는다.

PI-X03은 event_id·원 detector/policy/version·sourceclock·onset bracket와 실제
발행/전송/수신·usable cue시각을 별도로 제공해 달라. 다중경보 병합/억제·래치
해제·누락·긴 공백·활성 기억을 보존한다. source score가 calibrated probability가
아니면 그 의미를 유지한다. 실제 clock offset/drift와 불확실 지연도 근거를 붙인다.

PI-X06은 endpoint를 correct_command_issued/target_attained/process_rule_achieved로
구분하고 조건/기회/보호 효과 ID와 timed/recovery/dependency 포함 이력을 받게
해 달라. 필요한 확장은 공동 process deadline·command completion·actuator lag,
기한 내 복구·경쟁 실패·잘못된 반응/중단·검열의 공동 likelihood/시뮬레이션이다.
현재 untimed recovery와 deadline 모델을 단순 곱해 구현하지 않는다. 인수는
공통 clock 이동, 명령/공정 기한 교체 거부, 동일효과 재소비 거부, 공동 상태의
확률합과 조건부 모집단·실험 그룹 보존을 포함한다.

PI-X01은 실제 목표 위치 뒤 잔류 유동·재고·역류·차단 누설·공백·물성 범위를
포함한 공정 endpoint를 별도로 전달한다. command성공을 완전 isolation으로
인정하지 않는다. native관망 기준에서 target약0.3초에도 downstream0.01m3/s가
남았고 한계1초의 흐름 조건은 미달했다. 해당 사례·no-command·slow/late·reopen·
closed leakage·센서결측과 질량/에너지 수지의 수용 기준으로 왕복 검증한다.

## PI-X04/02 추가 — 사건 강도와 생존 기간의 원 근거

수신 API는 PhysicalReliabilityRequest, PhysicalFrequencyMapping,
WeibullPeriodMapping이며 PHYSICAL_RELIABILITY_KO의 원2모델9사례/18기준을 따른다.
같은 물리 사건 정의·설비/모집단·실제 at-risk exposure·시작/중단·포착/누락·
수리/재진입과 lifetime/검열을 구분한 기록을 제공해 달라. 공정 시계열의 행 수나
사고 건수만으로 완전 노출을 대신하지 않는다. 자료가 없으면 원 작성 예제만 실행한다.

원 gamma posterior mean intensity와 posterior predictive occurrence probability를
구분한다. 향후 predictive가 필요하면 prior/노출·constant intensity 지원 범위와
계수 uncertainty를 보존한 소유 API를 제공한다. 빈도 yearbasis·physical endpoint와
생존 나이·calendar interval은 호출자가 검토하고 원 receipt digest로 고정한다.
HRA demand HEP와 period probability의 직접 혼용은 거부한다. 불완전 포착,
시간가변 강도·군집/공통원인·covariate/검열 기전은 별도 검증 확장으로 남긴다.
인수는0사건prior유지·분할노출일치·초/년·기간합성과 event/condition/source pin
불일치 거부를 포함한다. 새로운 gate/FTA/exclusive-path 엔진을 추가하지 않는다.

## PI-X04 추가 — 제작/체결·압흔·인장 자료의 부분 출력과 실험 축

수신 계약은 FabricationRequest/FabricationResult, HardnessRequest/Conditions,
CharacterizationRequest와 각 MaterialJob/receipt다. FABRICATION_KO·HARDNESS_KO·
CHARACTERIZATION_KO의9원모델 수치 기준과 새3자료 가이드를 먼저 재현한다.
규격/제조사 표나 proprietary 계수를 배포하지 않고 원 파라미터·조건·검토·
source version/hash를 공급한다. 계산 status와 사용자 요구/절차 자격을 구분한다.

체결에는 실측 torque와 preload의 같은 시험/구간·각도/시간/phase 매핑, 실제
nominal/thread/인장 면적·윤활/표면·nut factor의 지원 범위와 계측 근거가 필요하다.
현재48시험은 전체 angle grid가 모두 달라 동일 좌표의 힘을 자동 보간하지 않았다.
원 CSV속도rad/s와 논문rpm, nominal 마찰계수와 K를 따로 보존한다. 이를 해결할
reviewed 정합/불확실성 프로토콜·원 instrument/프로토콜 근거를 제공해 달라.
preload만 계산할 때 인장 면적 미상이 응력과 구분되어 보류될 수 있도록 원 API의
부분 출력 의존성을 명시하는 확장도 검토해 달라. 평균 K를 모든 체결·재체결에
전이하거나 peak torque와 다른각도 peakforce를 같은 관측으로 맞추지 않는다.

벽 예산은 nominal-minus-tolerance→forming loss→future allowance의 순서와
이미 측정한 postfabrication wall의 별도 경로를 유지한다. 같은 손실을 두 번
빼지 않는다. 허용 굽힘/타원화·성형박화·체결 joint stiffness/외력/relaxation과
실제 설계 요구벽은 별도 검토/공급자 모델로 받고 선언 strain/preload만으로
기밀성·내압·현장 합격/인적오류 감소를 출력하지 않는다.

압흔에는 실제 test force/길이·ball/diamond 기하·dwell·표면·시편/재료 상태와
계측/불확실성을 제공한다. 원49.3N과49.03N을 nominal HV5힘으로 교정하지 않는다.
source label의 방법 이름 충돌·NN표면·보고 반올림을 보존한다. 가정한 기하
진단을 원 시험의 메타데이터로 덮어쓰지 않는다. 이후 weld traverse와 연결할 때도
같은 위치/방법/하중·기준 단위와 unknown영역을 유지한다.

인장은 source force/area, reported gauge strain, machine travel을 구분하고
extensometer/zero·necking·분기/재장착·원 시계와 source행을 제공한다. 현재20LIS의
time중복/역전122와 strain비증가10039·음의 보고strain20064를 수정하지 않았다.
지원되지 않는 음의strain에서도 알려진 force/area의 stress만 별도 출력할 수
있도록 원 입력별 의존성/상태를 명시하는 확장이 필요하다. strain 복원은 공급자
근거와 명시한 새 변환모델/버전·불확실성을 가진 별도 기록으로만 수행한다.
source reported mE/roundedRp0.2는 독립 재료 검증값이 아니다. cyclic 계수·잔류
수명·같은 조건의 baseline/exposed 짝이나 true stress 자격을 새로 만들지 않는다.

인수에는 exact source units/rows/digest·N/m/mm/µm/%변환·0과None/invalid구분,
source geometry/재료/시편/시험/clock불일치·pin변조 거부·loss중복방지·완료/요구
미충족 분리와 원 모델 왕복을 포함한다. 데이터 정합의 오차를 HEP/POD/파손
확률로 사용하지 않는다. 실험·시편 그룹을 분리한 독립 검증은 별도 수행한다.

## PI-X04 추가 — 원 Mat 배포와 재현 경계

현재 이쪽이 실행한 Mat는 제공된 local source이며 원격주소/commit을 추정하지
않았다. 앞선 source69파일의 SHA와 모델별 live schema/version은 당시 실행 증거에 있다.
원 Mat를 다른 세션에서 공개 배포한다면 실제 저장소/배포URL·버전·허용 라이선스·
동일 source해시·필요 선택 의존성을 확정해 이쪽 설치 안내/검증에 전달해 달라.
확정되지 않은 주소를 사용하거나 원 소스를 이 저장소로 복제하지 않는다.
배포 인수 기록에는 저장소/정식 다운로드 URL, tag 또는 commit, package와 모델
버전, wheel/sdist 또는 source tree의 SHA-256, 필요한 catalog JSON의 manifest,
MIT 원 라이선스·외부 고지, Python/선택 의존성·설치 명령을 포함해 달라.
주소·버전·권리·자료가 누락된 상태를 사용 가능한 공개 배포로 표시하지 않는다.
배포 패키지와 검토 source root의 model/schema/단위 차이는 자동 승인하지 않고
아래 실제 CLI로 재현해 달라. 다른 호스트가 개인 절대경로 없이 같은 버전과
핀을 선택할 수 있어야 한다. tree SHA의 동일성은 런타임 전체의 잠금이 아니므로
환경·전이 의존성 기록도 함께 제공한다.
그 배포가 준비되기 전에는 core 단독 실행과 원 Mat가 필요한 계산을 구분하며
후자는 명시적인 ImportError로 남긴다. 세션 대화 없이도 이 제한과 소유 경계를
알 수 있게 source권리/버전과 재현 명령을 함께 유지한다.

## PI-X04 추가 — 검사 truth·기하·반복 하중과 소스 고정

검사 네 모델과 피로 세 모델의 실제 연동은 각각
[INSPECTION_STATISTICS_KO](INSPECTION_STATISTICS_KO.md)와
[CYCLIC_FATIGUE_KO](CYCLIC_FATIGUE_KO.md)를 따른다. 검사 15사례·24기준과
피로 원 응답 16개·10기준을 원 MaterialJob/Receipt로 재현했다. 이쪽에서
통계 추정·TOFD식·격자 군집·재료식·solver를 새로 만들지 않는다.

검사에는 별도 trial-series 원 계약을 제공해 달라. 필요한 입력은 불변
source/행 hash, physical specimen/결함/검사자/절차 개정/조건/blank ID,
알려진 결함 크기와 불확실성, 검토된 binary truth와 detection,
calibration/holdout source-group, 반복 cluster와 원 시계·순서다. 같은
시편의 여러 영상이나 반복 획득을 독립 기회로 늘리지 않는다. 미상 truth·
미검출·blank false call과 완전 분리·점별 confidence·비유계 target 크기를
구분해 반환해야 한다. clustered 반복 검사에는 기존 독립 모델과 구분되는
소유 모델과 독립 검증 자료가 필요하다.

TOFD 획득 연결은 같은 frame의 lateral/backwall/diffraction pick·근거와
송수신 probe/wedge·측정 index 간격·시편 velocity/wall basis를 원 계약에
바인딩해 달라. 비인과/미상 pick, 다른 frame, 비대칭 경로와 뒤집힌 tip 순서는
보류해야 한다. 비등간격 두께 map에는 실제 셀 위치·면적·좌표계·원 순서·
valid mask·계측 불확실성·연결 정의가 필요하다. 미상 영역을 sound cell로
채우거나 signed net loss를 gross 손실·검사 포착률로 바꾸지 않는다.

반복 하중에는 원 파라미터별 engineering/true 응력·engineering/logarithmic
변형률·일 켤레·cyclic/monotonic·재료 조건·모집단·교정 지원과 근거 digest를
원 job 자체에 고정하는 계약을 제공해 달라. amplitude/range와 cycle/reversal을
명시하고 평균응력·노치·환경 효과의 포함 계보를 반환한다. 여러 cycle의
명시 경계·중단/결측 창·반주기 잔여 상태와 streaming 기록이 필요하다.
겹치는 창이나 이미 반영한 효과를 두 번 누적하지 않아야 한다. 원 응답의
경로 일·닫힌 일·미상·수명 구간 이탈을 유지하며 누적 손상·크리프–피로
상호작용은 별도 원 모델과 실험 근거를 가진 후속 작업으로 제공한다.

원 배포에는 모델별 안정적인 구현 digest, package 자료·라이선스·고지,
정식 배포 위치와 필요한 의존성을 제공해 달라. 현재 공통
`material_source_guard`는 실제 loaded package/evaluator/registry와 전후
소스 tree를 검사한다. 최종 검증은 MIT 고지를 유지한 private snapshot의
76 Python 파일, tree SHA
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`를
사용했고 snapshot·개인 경로는 공개 배포에서 제외했다. 과거 실행 hash를
덮어쓰지 않고 원 소스 변경 검출·잘못된 loaded root·schema/version/input/
receipt 변조·예외 종료의 불변 검사를 인수 조건에 포함한다.

부식·마모의 `CorrosionWearRequest`/`LossDomain`과 record 검사 API는
[CORROSION_WEAR_KO](CORROSION_WEAR_KO.md)의 원 세 모델로 실제 연동을
확인했다. [실행 결과](../research/corrosion_wear_results.json)는 원 응답
12개·작성 스칼라 기준 11개·거부 5개를 기록한다. 이 기준은 적합·재표집·
물리 꼬리의 독립 실험 검증이 아니다. 공개 구리 센서의 CSV 단위/배율·
물리 노출 원점과 AFM scratch별 실제 경도·계수 지원·분포 면적·시편 식별은
아직 미확정이다.
[자료 검토](CORROSION_SOURCE_KO.md)의 열네 보류는 source reader의 성공과
원 수치 모델 호출 0회를 구분한 인수 사례다. 같은 면적·노출·표면 모집단의
독립 block maxima 없이 Gumbel을 적합하거나 반복 시간을 독립 시편으로
분리하지 않도록 원 metadata와 상태 경계를 확장해 달라.

## PI-X02 추가 — 검출·경로 일·cycle 수명의 사건 의미

수신자는 원 POD의 `detection_given_flaw_and_procedure`, 이항 response 종류,
TOFD/격자 기하 기술량, signed 경로 일과 cycle 수명을 원 context/receipt에
묶어 보존해야 한다. 이 값에서 HEP·기간 최초고장·관측 방출 빈도를 직접
만들지 않는다. 실제 사건 정의·동일 자산/위치·기간·생존조건·운전 횟수와
공동 의존·불확실성의 별도 모델을 제출해야 위험 입력으로 검토할 수 있다.
점별 POD와 영역 관측률, 공간 크기 초과와 기간 최초고장은 다른 의미다.
인수에서는 의미·기간·조건 누락의 보류와 같은 효과의 중복 반영 거부를
확인하고 교환 계약 검사와 독립 현장 검증을 별도 기록한다.

## PI-X04 추가 — 조성 구간·충격 검열과 assay 계보

`materials.chemistry_reconciliation`, `materials.pitting_composition_index`,
`materials.impact_transition`의 실제 연계는 [재료 품질](MATERIAL_QUALITY_KO.md)을
따른다. 이쪽에서 조성 비교나 천이 적합을 다시 구현하지 않는다. 원 13응답·
독립 작성 기준 18개·거부 9개는 소프트웨어의 수치 기준이며 실제 강종 자격이 아니다.

원 수신 계약에는 physical specimen/scope·heat·동일 assay·생산 lot·원 certificate/
verification record·분석법·검출한계·구간 해석·질량 분율/백분율·요구 조건판과
이용 권한·source digest를 유지해 달라. 명목 강종이나 동일 원소 이름만으로
batch 대응·미측정 원소·명칭 동등성을 만들지 않아야 한다. 검열 구간을 상한 점값으로
바꾸지 않고, 조성 정합의 `unknown`과 불일치, 명시 `False` 비교 정책을 구분한다.

pitting에는 Cr·Mo·N의 같은 assay 대응과 계수의 모수 지원·의미를 전달한다.
지수에서 실측 부식률·임계 온도·수명을 생성하려면 별도 소유 모델과 실험 근거가
필요하다. impact에는 원 관측 ID·시편 치수/형상·방향·재료/열처리/조사 조건,
K·J·미파단/우측 검열·측정 범위·시험기 불확실성과 원 그룹 분할을 보존한다.
완전 관측용 적합과 검열 likelihood는 다른 모델로 구분하고 shelf/수치 식별,
관측 온도 밖 crossing의 `None`과 모수/예측 불확실성 범위를 반환해야 한다.

현재 BAM 6시편은 한 온도·미파단이고 보조 조성 선언의 실제 시험 lot 대응이
미상이다. 원조건의 세 요청을 보류하는 것이 인수 기준이다. 독립 검증에는
새 물리 시편·다른 assay 또는 별도 시험 그룹이 필요하며 같은 원 실험의
반올림 결과표를 새 정답으로 추가하지 않는다.

## PI-X04 추가 — 수소 관측·경계와 유한 슬랩의 보존 계약

`transport.semi_infinite_diffusion`, `transport.hydrogen_trap_equilibrium`,
`transport.hydrogen_slab_kinetic`의 기존 원 해법을 사용한다. 실제 16응답·
독립 기준 21개·거부 4개와 두 격자의 선택 수치 대조는
[수소 수송 가이드](HYDROGEN_TRANSPORT_KO.md)를 따른다. solver·trap 물성표나
전류 역추정기를 이 저장소에 중복 구현하지 않는다.

원 관측 변환에는 불변 시편/두께·활성 셀 면적·표면 처리·전해질·원 시계와
실제 충전 시작 원점·배경/검출기 응답·전자수/효율의 근거가 필요하다. 총 전류,
전류밀도, 수소 원자 몰 flux, 표면 격자 농도와 전체 수소 농도는 각각 다른
물리량으로 보존해야 한다. 기체 P/T·전류·진공에서 표면 용해/반응 경계나
농도를 자동으로 만들지 않는다. AHSS 두 전류 이력의 원조건 6요청은 유효 면적·
농도/D/포획/초기 상태 부족으로 보류하며 두 시트를 독립 시편으로 늘리지 않는다.

매개변수 수신에는 material/lattice/population·온도/모수 지원·양의 상대 결합
에너지·원자 몰·점/셀 평균·격자/total 분할을 함께 전달한다. independent rate
이력과 binding-ratio 모드를 구분하고 caller가 제공한 release 빈도를 덮어쓰지
않아야 한다. 양쪽 표면의 inward flux 부호·누적 교환·셀 재고·질량 잔차·
시간 오차·solver 종료 상태와 부분 궤적을 보존한다. 공간/시간 세분화·독립
슬랩 기준·포획 반응 균형·실제 source-group holdout 검증은 서로 구분한다.
응력 구배·움직이는 trap 집단·비평형 표면·취성/균열 결합은 새 원 모델과
별도 보존식·실험·수신 계약이 필요한 후속 범위다.

## PI-X04 추가 — 감육 프로파일과 공급자 용량 정규화

원 `structural.profile_capacity`의 nodal-window scan은
[감육 용량 가이드](PROFILE_CAPACITY_KO.md)의 원 12응답·독립 기준 10개·거부
4개로 확인했다. 수신자는 원 관측/검사·axial point·좌표계·축 방향·기준 벽·
측정 오차·물리 전체 범위를 유지해야 한다. 2D map을 한 트랙으로 줄이는 과정은
방향/원주 범위·공통 좌표·필터/보간·원 그룹의 별도 검토 기록이 필요하다.

기준 용량과 팽창계수 공급자에는 같은 geometry/material condition,
내압−외압의 pressure basis·단위·허용 권리·원 버전/근거·지원 길이/손실 구간과
정규화의 포함 효과를 제공해 달라. 기준 벽·재료·축 길이 또는 이미 반영한
안전/형상 효과가 다른 표를 이름만 보고 교체하지 않는다. 원 표를 이쪽에
복제하지 않고 caller가 이용할 수 있는 공급자로 주입한다. 공급자 범위 이탈,
불완전 extent·기준보다 두꺼운 값·영 잔여 두께와 원 차압 비교를 보존한다.
불리한 창 삭제·표 외삽·음의 손실 clipping으로 계산을 완료하지 않는다.
연속 끝점 최적화·원주 상호작용·균열형 결함·열/외력 결합은 각각 후속 근거가
필요하며 정적 모델 용량을 MAWP·잔존수명·기간 파손확률로 재표시하지 않는다.

## PI-X02/03/04/06 추가 — 원시 위험 수치와 기간·사건의 범위

`reliability.independent_gate`와 `reliability.exclusive_paths`의 확인에는 기존
`MaterialJob`과 생산 `HRARiskClient`/`period_risk`를 재사용했다. 원 10응답·
기존 HRA API 5사례·독립 기준 16개·거부 4개의
[수치 대조](MATERIAL_RISK_REFERENCE_KO.md)를 새 FTA/ETA 구현의 근거로 사용하지 않는다.

수신자는 원 `unscoped_event_probability`와 1/s 경로 빈도에 사건 ID·endpoint·
조건·기회·자산·같은 기간·생존/나이·반복 노출·공동 상태/독립성 검토를
추가해야 한다. same-event 참조와 독립 복사본을 구분하고, 같은 관측·방호·
사람/물리 효과의 중복을 거부한다. 배타 경로의 미완전 합을 정규화해 채우거나
미상 확률을 0으로 만들지 않는다. 원 빈도와 연간 기대 횟수, 기간 내 한 번
이상 사건확률을 분리하고 year의 초수와 사용/생존 조건을 선언해야 한다.
실제 HRA 추정·관측 기간·노출/성공/실패와 조건부 가지의 근거는 원 위험
소유자가 유지한다. 숫자 일치만으로 scoped HEP나 현장 사건 빈도로 승인하지 않는다.

## 공통 source pin 보고서와 새 자료의 인수 조건

공통 `material_source_guard`의 실제 loaded root 검사와 전후 소스 확인을 유지한다.
저장된 report는 `verify_material_source_pin(report, expected_source_sha256,
executed=...)`로 다시 검사한다. SHA·파일 수·before/after·불변 선언의 불일치,
미실행/무핀 요청의 pin 주장, 재봉인한 잘못된 pin을 거부해야 한다. guard
자체의 실제 실행과 report의 일치 검사는 다른 증거이며 report는 전자서명이나
독립 실행 진위의 증명이 아니다. 요청 생성 때부터 직렬화 전에 예산을 확인하고
과대 입력을 잘라 처리하지 않는 것을 인수한다.

새 BAM/AHSS 자료군은 명시 권리·원 metadata/페이지·공급자 checksum·버전·저자를
보존한다. 원관측·변환 행·상세 receipt와 원 엔진 snapshot은 배포에 넣지 않는다.
[원조건 판독 가이드](MATERIAL_PROCESS_DATA_KO.md)의 source/시편/이력 구분을 따른다.
원조건 9요청 보류/수치 호출 0회와 독립 작성 모델 기준을 분리해 재현한다.
원 엔진별 배포가 준비되면 이 문서의 배포 인수 조건과 아래 CLI를 같은
버전·지원 자원·라이선스에서 다시 통과해야 한다.
Mat뿐 아니라 수신 HRA·PRISM 등 각 원 엔진도 정식 배포 URL·버전/commit·
artifact/source checksum·필수 자원·라이선스·호환 schema와 설치/재현 절차를
제공해야 한다. 다른 엔진의 소스나 모델 버전을 같은 값으로 추정하지 않는다.

```powershell
python scripts/check_material_quality.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_hydrogen_transport.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_profile_capacity.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_material_risk_reference.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT --hra PATH_TO_HRA
python scripts/acquire_data.py --only bam_s355_charpy ahss_hydrogen_permeation --workers 2 --maximum-seconds 45
python scripts/review_material_process_data.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
```

## PI-X05 추가 — 일곱 감시 family의 원 shape·spec·분할 계약

수신자는 process-monitoring-toolkit이며 원 검출기를 이 저장소로 복제하지 않는다.
현재 [모니터링 교환](MONITORING_EXCHANGE_KO.md)은 일곱 family의 선택 59 ID를
실제 fit→별도 calibrate→held-out score로 실행했다. 원 source tree는 28 Python
파일 SHA `a8856f3e30f7bddf7539dbe0b74ea7d47ada4127f52f765bf61e4a134300fe0a`,
PMT 0.1.0이다. `scripts/check_monitoring_exchange.py`와
`research/monitoring_exchange_results.json`의 실패도 함께 재현해야 한다.

우선 수정은 sklearn 1.9.1의 단일 채널 `lag_ridge`다. fit의 target `(n,1)`과
prediction `(n,)`이 broadcast되며 잘못된 error-scale shape가 생겨 calibration에서
실패한다. 1채널을 중복해 우회하지 말고 1·2·3출력에서 명시한 residual shape와
출력별 scale을 보장해 달라. 0분산·짧은 이력·결측/gap·창 초기 미계산을 포함하고,
선택 환경/지원 sklearn 버전에서 실제 fit/calibrate/heldout 왕복을 인수한다.

원 spec은 이름·family·extra·training·temporal 메타데이터지만 생성자 전체의
버전별 kwargs schema는 아니다. 필수/선택·기본값·허용 범위·실제 소비된 키·
지원 feature 수·dependency version을 제공하고 알 수 없는 kwargs를 거부해 달라.
CBLOF의 군 분할 불충족처럼 원 지원 조건 실패는 구조화된 진단으로 반환한다.
이번에 성공한 n_clusters/alpha/beta 작성값을 보편적 추천 설정으로 채택하지 않는다.

수신·반환 metadata에는 원 source/spec/runtime pin, asset/분석 clock과 source clock,
물리 run/source group/관측 ID, training/reference 근거, 분할 정책·단계·reset 정책,
원 단위·채널 순서·valid, 특징 창 endpoint와 첫 점수 가용 시각을 유지해 달라.
원 시각을 압축하거나 invalid 행을 지우지 않는다. scaler fit과 calibration quantile은
각기 선언한 단계에서만 수행하고 평가에서 재적합하지 않았음을 반환한다.
점수 척도·threshold·strict 비교·경보와 물리 latency는 서로 구분한다.

인수는 기존 작성 수치 기준과 future/holdout 불변 조건의 유지, 관측 재사용·
잘못된 clock/unit/spec/source pin 거부, 원 source 전후 불변, JSON/direct client
동등 왕복이다. 기존 library ECOD와 새 신경망 20종의 선택 조건에서 확인한
미래 관측 불변성을 모든 모수·모델의 인과성 증명으로 넓히지 않는다.
현장 성능 단계에서는 독립 source-group holdout, 실제 truth·
노출 기간·운전 조건·미상률과 사건 단위 지연/미검출/오경보를 별도로 검증한다.

원 `dynamic_pca`의 SPE/T²와 창, neural/temporal_neural 및 graph/diffusion의
실제 호출도 추가했다. 이를 다시 구현하지 않고 새로운 원 버전·설치 extra·
device/CPU thread·모수/창 조건에서 같은 독립 평가 protocol을 유지해 달라.
명시 hidden/epochs/batch_size/seed·training work·지원 dependency version과
fit 직후/보정·평가 후 tensor·전처리 상태의 SHA/shape/dtype/loss를 반환해야 한다.
GAN의 query 최적화와 detector 재학습을 구분하고, seed 재현·CPU thread 복구와
학습되지 않았다는 주장의 오염 거부를 인수한다. 원 연구 구현의 실행을 특정
논문/benchmark 성능 재현으로 표시하지 않는다. classification·incremental·
subsequence·engineering은 이번 선택 범위 밖이며 미실행 경로는 보류한다.

## PI-X01/03 추가 — 충전소·pool의 시계·원장·물성 전달

수신자는 hydrogen-station-sim, DynamicLH2PoolX와 결과를 받는 PRISM이다.
[충전소](STATION_EXCHANGE_KO.md)와 [pool](POOL_EXCHANGE_KO.md)의 실제 원 API를
유지한다. station의 닫힌 restriction network와 bounded full-reference episode,
pool의 spreading/fixed-area를 하나의 물리 모델이라고 합치지 않는다.

충전소 배포에는 필요한 property table·생성기·생성 조건·테이블 hash·물성 범위·
CoolProp/backend/version과 에너지 기준을 함께 제공해 달라. 현재 누락 테이블은
별도 snapshot에서 원 생성기로 만들었고 원본은 그대로 보존했다. 정상 설치에서
필수 자원 누락을 사전에 진단하고, Python/SciPy 호환 범위와 public import 경로를
고정해야 한다. 테이블 재생성 결과의 바이트 동일성과 물성 허용오차는 구분한다.

full-reference는 원 `build_reference_scenario`와 `SafeFullStationSimulator.simulate`를
호출하지만 현재 전체 유입/유출·축열의 적분 원장이 없다. 탱크/벽/열교환/압축기/
배관·환경·기계일의 개별 저장량과 구간 누적 질량/엔탈피/열/일을 반환해 달라.
전체 residual·부호·적분 스케일·오차·완료 prefix/종료 이유를 함께 제공한다.
현재 no-fault 짧은 episode의 빈 risk snapshot을 위험 0이나 HyRAM 검증으로 해석하지 않는다.
능동 fault·consequence의 support/호출 여부·오류를 별도로 반환해야 한다.

pool 입력은 검토한 `liquid_already_reached_surface` kg/s이며 ENU origin,
source asset/event/port, local-clock 원점/offset, ZOH와 final hold를 보존한다.
jet/deposition과 연결하려면 upstream 분기별 phase/property basis·질량/에너지·
도달 위치·시간·미도달/잔류량을 제공하고 각 수지를 닫아야 한다. 불일치한
AccidentHistory를 pool 성공으로 덮거나 ParaHydrogen을 Hydrogen으로 바꾸지 않는다.
SDK 현재 포화 물성은 `CP-HEOS:Hydrogen:native`, ambient Pa_abs다.

원 pool은 wet area와 reported footprint, instantaneous/last-substep/interval-mean,
누적 증발·escape·numerical adjustment를 구분해 반환한다. 이를 유지하면서
열 유입·잠열·액체 저장 에너지·수치 에너지 조정의 적분 ledger를 추가해 달라.
원 SDK는 실제 adaptive substep 상한과 예외 직전 ledger를 제공하지 않는다.
구조화된 최대 work/취소·부분 prefix·source status를 반환하면 현재 wall timeout의
완료 응답 부재와 구분할 수 있다. dry surface 재가열·fixed-area thermal age·water
closure 지원 시간도 별도 자격과 explicit 상태로 반환한다.

인수는 닫힌 계, 영/미소 유입, 유입 종료·dryout, 역류/명령 불연속, wet/report
분리, domain escape, 수치 조정, timestep/격자 세분, timeout/예산/속성 오류와
원 source 불변의 실제 실행이다. 각 grid의 모든 행에서 원·독립 질량 장부와
추가 에너지 장부를 비교한다. signal export의 구간 평균은 누적량 차분이며
초기 None/invalid와 clock offset을 유지한다. 실험 검증은 별도 독립 자료로 수행한다.

## PI-X03/06 추가 — 감시·지식 후보와 확률/동작의 구분

수신자는 기존 위험 및 HRA 소유자다. 위 감시 score/threshold/alarm, station/pool의
simulation SignalFrame, SAGA의 citation score/pending candidate에
`quantity_meaning`, source/model/version, asset/event/port/clock, 원 origin·근거와
지원 상태를 유지해 달라. 숫자의 범위가 0~1이라는 이유로 확률로 수용하지 않는다.
명시한 사건 endpoint·기회·기간·생존·운전 조건·인적/물리 효과·공동 의존과
중복 방호를 바인딩한 경우에만 기존 위험 계약으로 전달한다.

경보 발생·사람 인지·올바른 명령·목표 위치 도달·실제 공정 규칙 달성은 분리한다.
지식 후보는 권리/판/기간/인용·단위를 확인한 advisory이며 승인된 운전 규칙이 아니다.
이쪽은 후보→제어 자동 적용 함수를 만들지 않았다. 인수는 clock·origin·증거 누락,
부분 실패와 pending 상태의 보존, 확률 의미 없는 점수 거부와 실제 사건/기간
검토를 갖춘 기존 HRA/FTA 왕복이다. 새 FTA/ETA 구현은 요청하지 않는다.

## PI-X07 추가 — 실제 offline 경로에서 독립 library·추론 계약으로

수신자는 SAGA-PY다. [지식 교환](KNOWLEDGE_EXCHANGE_KO.md)은 실제 Database
초기화/문서 저장/hybrid_search, RagPipeline.citations, HAZOP monitoring_readiness와
reasoner 없는 propose_threshold를 호출했다. 원 server는 이 source 묶음에 없는
정적 UI 자산을 요구한다. UI 없는 정식 library 설치와 server import가 가능하도록
지원 경계를 분리하고 project/CITATION 2.0.1 대 namespace/API 2.0.0의 판을 정리해 달라.

현재 offline import 경로도 `rag → external_sources → indexer → pymupdf`로
PyMuPDF 1.28.2를 실제 import한다. 해당 배포의 AGPL/상업 조건은 SAGA의 MIT와
별개다. 검색·인용·준비도만 필요한 사용자가 문서 importer/native dependency를
설치/import하지 않아도 되는 정식 optional extra·lazy import/library 경로를 요청한다.
import stub나 라이선스 문자열 교체는 완료가 아니다. 가벼운 환경에서 실제 모듈
목록·네트워크 호출 0·검색/후보 검사를 재현하고 각 extra의 고지를 제공해야 한다.

요청·응답은 asset/location/domain/clock, 명시 as_of 시각·유효기간, source ID/판/
content hash/권리/허용 작업/allowlist, 원 locator/변환 계보, 실제 service/model/
prompt/schema/runtime pin과 reasoning_executed를 유지해야 한다. 추론 서비스가
없으면 needs_data와 검색 근거만 반환한다. 독립 작성 mock 답변을 생성 성공으로 세지 않는다.

생성 후보·기계 AnswerReview·사람 승인·운전 반영의 상태/권한을 분리하고,
claim→citation→허용 source/version/passage/exact quote의 연결을 검증해 달라.
정확한 quote가 있는 것과 그 문장이 수치·단위·설비/시기 적용을 뒷받침하는 것은
다르다. PDF importer를 정식 선택했을 때만 추출 방식·page/locator·원문/변환 hash를
반환하고 권리 미상 corpus는 거부한다. 외부 provider에는 정보 전달 범위·예산·
실패/재시도·취소·서비스 권한 계약을 별도로 적용해야 한다.

인수는 기존 offline 성공·미서비스 보류·영값/null·잘못된 방향·미검토·판/기간/
권리/locator 변조 거부를 유지하고, 실제 승인된 추론 경로의 인용 충실도·누락·
틀린 근거·정정/폐지 판·한국어/영어 질문을 독립 평가하는 것이다.
원 DB·운영 규칙을 바꾸지 않는 sandbox 왕복과 새 source fingerprint를 제출한다.

## PI-X01/04 추가 — 구현한 정상·원통 과도 열과 남은 연성 계약

이쪽 [정상 열 경계](BURIED_THERMAL_KO.md)는 ht의 기존 두 함수를 호출하며
`uniform_fluid_temperature`/`steady_plug_flow`를 제공한다. 이어
[원통 과도 경계](PIPE_SOIL_TRANSIENT_KO.md)는 실제 Mat
`thermal.transient_conduction_1d`로 관벽/단열/토양의 초기 셀 평균·시간 경계와
가변 k/Cv 축열을 연결했다. 같은 저항식이나 원 FV solver를 다른 저장소에
복제할 필요는 없다. 두 경로의 기하·온도 평균·에너지 의미를 유지한다.

Mat 수신자에게는 유한 접촉 저항의 정식 interface를 요청한다. 내부 계면 ID,
양쪽 표면 K, 방향·열률 W, 접촉 K·m/W와 구간 K/W의 관계, 온도/시간 의존
조건과 근거·불확실성을 명시하고 완전 접촉 극한·반대 방향·영 열·서로 다른
길이를 검증해야 한다. 현재 `perfect_contacts=False`는 out_of_scope이며
0저항으로 바꿔 실행하지 않는다. 수분·지하수/상변화·유체 재고/엔탈피 되먹임은
각 물리 소유 모델과 보존량을 따로 검토해야 한다.

원 결과에는 경계/계면별 **구간 누적 J**, 셀별 엔탈피·완료 prefix와 종료 이유를
추가해 달라. 현재 원통 adapter는 원 substep 누적 J와 최종 Cv 적분을 비교하지만,
전체 시간에 대한 계면/구간 ledger가 없는 것을 sample W의 사다리꼴 적분으로
대체하지 않는다. 모든 source/model/schema/default 입력·단위·좌표·clock/offset·
초기 셀 평균·물성 지원을 원 receipt에 결합해야 한다. 원 computed가 보고한
수지/step/반복 기준과 모순되거나 재해시 기록의 경계 W·K·면적이 입력과 다르면
거부한다. 영 순열량의 상대 수지 민감성도 미해결 원 상태로 보존한다.

정식 public owner source/package URL·버전·LICENSE/고지·필수 JSON/물성 자원·
호환 의존성·소스/artifact checksum과 설치 CLI를 제공해 달라. 확인되지 않은
Mat 배포 URL을 추측하지 않으며, private source pin만으로 다른 연구자가 원
계산을 재현할 수 있다고 표시하지 않는다. 이쪽 guide의 정상 log·±열유속 적분·
체적 평균 Bessel 모드·공간/시간 세분·가변 Cv 기준을 원 환경에서도 인수한다.

실측 입력은 [토양 자료](SOIL_DATA_KO.md)의 수직 깊이·처리 clock·일 극값을
radial radius·절대 달력 원점·순간 경계로 바꾸지 않아야 한다. 관벽 위치/형상,
센서 좌표·가열/표면 조건·source 처리 계보·열전도도/열용량과 독립 실험 대응이
필요하다. 같은 자료의 fitted 물성을 독립 validation 정답으로 사용하지 않는다.
현재 reader/신호 재생의 성공과 물리 calibration/validation 미실행을 구분한다.

## PI-X01/04 추가 — 지표·복수 cavity 단면의 기하와 비직교 열류

별도 FiPy 경계는 실제 ground surface와 다각형 pipe cavity를 갖춘 2D 토양
mesh를 받는다. 원통의 가상 바깥 반경을 지표 평면이나 실제 토양 축열로
바꾸는 모델이 아니다. 현재 검증된 서비스는 직교 mesh의 제한된 경계이며,
일반 비직교/곡면·모든 복수관을 포함한 기술 항목은 partial로 유지한다.

후속 소유자는 vertex/face/cell 인접과 방향·원 단위/좌표·축방향 extrusion,
외부 경계/내부 cavity의 단순 loop·containment·서로 다른 관 asset ID를 유지해야 한다.
겹침·틈·교차·뒤집힌 cell·연결되지 않은 토양·가짜 내부면 pipe 지정과
수평 지표를 만족하지 않는 입력을 거부한다. 다각형 cavity를 원형 관벽으로
표시하거나 원주/면적/법선을 근거 없이 치환하지 않는다.

비직교 열류에는 각도뿐 아니라 cell-center/face-center의 conjunction/skew,
비정상 aspect ratio·얇은 부분·far-field extent·곡면 근사·각 face 물성/법선의
오차를 검토한 metric·허용값·적용 근거가 필요하다. caller가 큰 허용 각도를
선택했다는 이유로 검증되지 않은 mesh를 승인하지 않는다. 기본 무보정 확산의
skew 경계 열류 문제와 correction 경로의 호환/물리 오차는 재현 조건·버전·실패를
보존하고 지원 밖으로 남긴다. 전역 수지만 맞는 내부/경계 flux나 완화한 기준을
비직교 물리 검증으로 대체하지 않는다.
구체적인 작성 전단0.75 선형장 기준은 실제 지표 법선 열24W지만 기본 gradient는
19.2W였고 별도 Correction 반복 시험도 약19.1874W였다. 다른 혼합 경계 조립에서
발생한 `MeshVariable.max(out=...)` TypeError도 별도 실패다. 이 결과를 모든
Correction 구성의 실패라고 일반화하거나 출력 열률만 바꿔 성공으로 표시하지 않는다.

완료 조건은 선형 해의 실제 물리 경계 flux·cell 적분 해, 비직교/곡면의 공간/시간
세분, 서로 다른 refinement path에서의 오차 감소, 단일/복수 cavity와 far-field
범위 민감도다. 행렬/gradient 재구성은 원 FiPy API를 사용하고 이쪽에 새 FV
solver를 쓰지 않는다. 필요하면 별도 호환 환경의 원 correction solver를 검토하되
기존 데이터·주 환경·기준 허용오차를 바꿔 실패를 숨기지 않는다.

열 경계 time interpolation·공통 clock/offset·BE 적용 구간과 누적 J, native
cell equation·국부/전역 에너지·지원 상태·부분 prefix를 함께 반환해야 한다.
previous-knot hold의 오른쪽 knot는 끝난 구간의 왼쪽 극한을 적용한 BE step과
새 knot 직후 상태가 다르다. 원 누적 J·BE 적용 W·순간 물리 열률을 별개로
보존하고, 완료/부분·미계산 초기 열률·실제 source/solver pin을 저장 검증과
감시 신호에서도 유지해야 한다.

## PI-X01/05 추가 — 실제 가스 유량과 계량 불확실도의 교환

새 수신 경계는 [가스 유량계](GAS_METER_KO.md)와 [측정 공분산](METROLOGY_KO.md)다.
소유 공정·계측 엔진은 압력 탭의 위치/기준, 실제 상류·하류 포트, 압력·온도·조성·
습분, 시계/사건/offset과 명목 양의 유동 방향을 보존해 달라. 측정 시각의 질량 유량,
실제 체적, 선언 기준 상태의 체적을 구분한다. 액체 LNG나 지역 밀도로 보정한 체적을
표준 건조 가스 체적으로 바꾸지 않는다. 미상 형상·정답·가스 조성·기준 상태를
예제 기본값으로 채워 실제 관측의 교정 기록처럼 내보내지 않는다.

계량 모델의 민감도를 제공할 때는 모델·구성·소스·입력 추정값/기반의 SHA-256,
정확한 입력/출력 순서와 SI 단위, Jacobian의 평가 지점, 수치 미분/해석 미분 방법·
섭동·지원 영역·검토 근거를 함께 달라. 원 모델의 실행 기록은 별도 검증한다.
구성한 covariance는 표준 분산/공분산이며 확대 U·분해능·반복 평균의 표준 오차와
서로 대체하지 않는다. 모든 성분의 근거와 상관·자유도의 미상 상태를 보존한다.
`valid=False`와 `None`을 구분하고 전체 공분산이 미상이면 보류하는 현재 계약을 따른다.

인수 조건은 동일 사건/시계의 실제 왕복 호출, 순/역류 부호와 명목 포트, 기준 상태
왕복의 질량 보존, 조성·습분 불일치 거부, 압력 기준/시간 누락 보류다. 독립 선형
공분산 기준·완전 상관/특이 행렬·비선형 모델의 기존 ensemble 비교를 사용한다.
교정이 필요하면 학습·검증이 분리된 실제 모델 적합과 기준기의 불확실도를 포함한
오류 모형을 제공해야 한다. 여러 미확정 교정값을 일반 역문제의 정확한 관측처럼
취급하는 방식은 수용하지 않는다. 시간 누적의 경우 순간 유량·구간 평균·계수기 누적을
구분하고 명시한 적분 방법·표본 구간/공백·보존 수지·공통 오차를 반환한다.
거래별 허용 오차·guard band·확대 계수는 검토한 호출자 규칙으로 받고 자동 인증하지 않는다.

## PI-X01/05 추가 — 양축 교정과 관측 기간의 수량 보존

[EIV 교정](EIV_CALIBRATION_KO.md)은 원 SciPy 해법으로 양축/시점 간 전체 공분산을
받는다. 소유 계측 엔진은 물리적 회차·모집단·적합/평가 역할, 실제 입력/단위/기반,
오차 모델의 표준 공분산·불확실도 평가 단계·분포/독립성 근거를 반환해 달라.
출처에 있는 전체 공분산을 점별 대각 가중치로 바꾸거나 파생 MC 자료를 독립 원센서
관측으로 표시하지 않는다. 일반 singular/exact-y·범위 밖·활성 경계에서 대칭 정규
공분산을 자동 내보내지 않는 현재 계약을 확장할 때는 별도 수학/독립 기준이 필요하다.

적용 예측의 새 x와 적합 모수 사이의 공분산이 미상이면 0을 가정하지 않는다.
필요하면 기존 metrology/ensemble에 결속한 전체 측정 모델과 Jacobian을 제공한다.
`predict_calibration`의 명목 y를 완전한 측정 불확실도로 바꾸지 않는다.

[구간 적산](TOTALIZATION_KO.md)은 점 값/구간 평균/계수기를 구분한다. 실제 계수기
기간·reset/wrap/역류의 근거·관측 clock과 기준 상태·포트를 보존하고 q가 pulse/τ에서
나오면 q/τ 상관도 보존한다. reference 평균 창과 MUT 계수기 창을 같다고 가정하지
않는다. 더 큰 창의 공분산은 공통 오차를 보존하는 저랭크/희소 표현과 동일 회차의
독립 검증이 필요하다. 64좌표를 넘는 현재 제한을 독립한 작은 창으로 몰래 분할하지 않는다.

인수 기준은 완전 상관 시계의 상쇄·혼합 reading/time 상관, 누락/invalid/긴 공백의
총량 보류, 계수기 증가/감소와 reset의 구분, 같은 조성·기준 상태의 질량/체적 경계,
커다란 offset에서의 시간 표현 공차와 끝점 보존이다. 모든 전송에 source/model/
schema/runtime/단위·문맥·수량·원 요청/응답 해시를 결속한다.

## PI-X02/03/06 추가 — 공간 위해장의 원본 검증과 공동 정수 법칙

[공간 위험](SPATIAL_RISK_KO.md)은 원 PRISM의 조건부 위해와 연간 집계, 원 HRA의
사건/정수 PMF F-N을 연결한다. 소유 엔진은 저장한 결과를 원 packet/defaults/
시설/수용체/풍향 좌표/평가 시계에서 재검증하는 공개 API를 제공해 달라.
원 request와 provider-boundary·시설/request digest, ok/실행/자격/문제/domain 표시는
서로 일치해야 한다. 재해시만으로 바뀐 위해/가중치/높이/유체/포트가 통과하면 안 된다.
원 열 flux→선량→위해·flash/overpressure·분기/합성 수학의 공개 검증 API가 필요하다.
이쪽은 원 소유 함수를 고립된 소스/런타임 고정 interpreter에서 재호출하며 원 solver를
복제하거나 patch하지 않는다. 모든 consumer가 같은 공개 사전 검사를 재사용하도록
원 검증 경계를 분리해 달라. 저장 primitive 재검산과 dispersion/fire geometry 전체
재실행·독립 물리 검증을 구분한다.

정수 F-N에는 셀별 정수 사망자 공동 상태/확률과 원 지지 인구·존재/위해 주변평균이
필요하다. 두 사람의 주변확률(.9,.1)에 총량{0:.5,2:.5}를 붙이는 사례는 평균1이
맞아도 불가능하므로 거부한다. 기대 사망자 문턱 곡선과 정수 사망자 초과빈도,
고정 위치/현재 존재 인구/시간 노출을 별도 출력으로 유지한다.
일반 GIS·연속 contour와 fractional modeled raster를 정수 인구로 바꾸려면 실제 CRS/
datum·수직 기준·면적/분포·시간/집단/rounding 및 불확실도 보존의 검토 계약과 독립
자료가 필요하다. 누락/지원 밖/원 보류를 0으로 채우거나 raw calibration 자료에서
발생 강도·HEP·인구를 추정하지 않는다.

## 최신 세션의 공통 인수 조건

비직교 열의 대안 탐색은 [유한요소 연구 기록](SOIL_FEM_RESEARCH_KO.md)에 있다.
초기 scikit-fem 탐색에서는 정상 선형장 법선 열/반력을 재현했지만 transient 상한
위반이 남았다. 이후 [P1 경계](SOIL_FEM_KO.md)는 명시 노드·원 분할/적분과 실제
행렬/온도/국소/전역 진단을 구현했으며 이전 실패도 보존한다. P1 nodal trace와 P0 평균의 투영, 공유 corner
반력 분배, 조립 operator/Robin/질량 이산화의 단조성과 세분 기준을 인수 조건에 포함한다.

요청 ID와 저장소·커밋·환경을 기록하고, 이 저장소의 예제 또는 같은 구조의
새 기준 사례로 실제 왕복 호출을 실행한다. 자기 저장소의 단위 검사뿐 아니라
이쪽 원본 계약으로도 검사한다. 코드·문서·실행 결과·남은 한계를 PR에 포함한다.

## 인양의 후속 소유 경계

PI-X04/기존 구조 연계에서 `lifting.LiftingRequest`의 부착점·앵커·질량중심·
프레임·외력을 입력으로 이어받을 수 있는 비선형 케이블 경계를 요청한다.
원 길이·EA/강성·초기 장력·중력/온도·활성 접촉과 외부 하중의 근거를 명시하고,
변위 적합·비압축 장력·느슨한 줄·힘/모멘트 수지·고정점 반력·불안정/비유일
진단을 원 결과로 내보낸다. 현 API의 유일한 정적 장력과 별도 원 길이/강성
기준을 각각 재현하고, 하중 경로·상태/시계·변형/에너지/허용하중 의미를 보존해야 한다.
제조사 방향 envelope와 결속·온도·사용 이력의 유효 한계는 별도 공급자로 받으며,
서로 다른 좌표나 승인되지 않은 표를 추정해 통과시키지 않는다. 동적 인양/다물체/
러그·관·앵커의 국소 응력과 실제 시험을 단일 정적 해의 자격으로 바꾸지 않는다.

## PI-X01/04 — 맵 압축·적분 열 장부·고화/frost의 구체 확장

`CompressorTrainRequest`의 총상태/유체·참조 좌표·속도·맵 digest·서지/초크 정의,
직렬 포트·축·냉각 경계·외부 기계/구동 손실을 원 엔진이 직접 소비할 수 있어야 한다.
반환값은 실제 맵 위치/외삽 여부·축/구동/기체 전력·열률·정압과 총압의 구분,
회전 관성·공유 축·재순환과 제어/방호 우선순위·원 시간/질량/에너지 누적을 포함한다.
정적 affine 맵/등엔트로피 점 기준과 동적 서지/회복 시험을 각각 독립 확인한다.
현재 DCS 자료의 단위/조성/유량 공백을 원 맵/서지 시험의 정답으로 사용하지 않는다.

`DynamicThermalCase`의 원 RK4 state와 별도로 실제 내부 quadrature의 열·유입/유출
엔탈피·질량 누적을 export하는 owner API를 요청한다. endpoint derivative×step과
구분하고 열린 CV의 유체/벽/외부 전체 보존을 누적량으로 검사해야 한다. 공급 열과
계면 잠열의 동시 해석은 경계/closure의 지지영역과 잔차를 명시한다.

벌크 고화/용융은 원 고체 EOS/열물성·latent enthalpy/상분율·핵생성/전선·열/질량
경계·접촉과 에너지/격자/시간 세분화를 받는 별도 native signature가 필요하다.
frost는 공기/포화 범위·침착/융해 질량과 latent/sensible energy·표면/열저항·
원 clipping의 진단을 반환해야 한다. LN2/NASA 실험의 원 시계·단위·열입력/기하와
source-cell 대응을 확보한 뒤 해당 원 물리에 연결하고 유체/장치 간 무근거 전이를 거부한다.



## 병렬 구현에서 구체화한 후속 요청

현재 아래 구현은 수치 검증 대기다. 다른 세션은 먼저 각 문서의 입력 계약과
현재 제한을 읽고 기존 소유 엔진을 확장한다.

- 인입력: [PULLING_KO.md](PULLING_KO.md)의 확장 요청을 PI-X04/01에 연결한다.
  EA/EI·접촉·토질·부력·압력·관성을 원 구조/재료 엔진이 담당하고, 명시적 좌표·
  양단 포트·수지·수렴 상태를 반환한다. 현재 유연선 결과는 유한 강성 관 검증이 아니다.
- 탱크: [TANK_BREATHING_KO.md](TANK_BREATHING_KO.md)의 계약으로 PI-X01을 확장한다.
  PSV 래치는 수락된 사건/적분 스텝에서만 갱신하고 trial 평가에는 부작용을 금지한다.
  다성분·액적 동반·포트별 적분 엔탈피·양/음 상변화 적분을 같은 보존 장부로 반환한다.
- 센서: [SENSOR_PLACEMENT_KO.md](SENSOR_PLACEMENT_KO.md)의 검토된 Jacobian과
  전체 공분산을 모니터링/원 물리 엔진이 생성하도록 한다. 선형화 상태·파라미터 단위와
  스케일·관측 순서·모델오차·상관 근거를 전달하고, 배치 점수를 누설 탐지 확률로 바꾸지 않는다.


경로 후속 요청 PI-X04/02는 [경로 매뉴얼](ROUTE_PLANNING_KO.md)을 따른다.
GIS 후보 생성·토질/횡단 계산·공정 일정·현금흐름은 각 소유 엔진이 맡고,
후보 ID·수량·통화/가격연도·근거·상태를 이 비교 계약으로 반환한다.

위치 역추정의 물리 예측 공급자는 [위치 매뉴얼](INVERSE_LOCALIZATION_KO.md)의
관측 순서·시계·단위·고정 nuisance·공동 잔차 공분산과 native source/config 해시를
보존한다. 단일 가장 좋은 후보를 검출 성공이나 누설 확률로 전달하지 않는다.


피그 연성 요청은 [피그 매뉴얼](PIG_MOTION_KO.md)을 따른다. 원 유동 엔진은
피그 위치/속도에 따른 양측 압력·seal 우회 유량·유체 수지와 사건 시각을 반환하고,
기계 엔진과 수락된 시간 스텝을 공유해야 한다. 초기 강체·마찰 모델의 공급 값을
검증된 seal 상관식으로 간주하지 않는다. 변형/충격은 원 구조 엔진이 담당한다.


## 추가 연성·관측·운영 소유 엔진 요청

- [가열 수송](HEATED_TRANSPORT_KO.md): 원 물성/열/수리 엔진이 조성·유변학·상·벽열관성·
  온도의존 열경계·분포 압력/엔탈피를 보존식과 함께 풀고 수렴/모델오차를 반환해야 한다.
  입구 물성 고정 셀 모델을 원유 전체 모델로 재명명하지 않는다.
- [외부 센싱](EXTERNAL_SENSING_KO.md): 모니터링 소유 엔진은 공간 지지영역·노출시간·
  clock 오차·검출한계·포화·비검출을 지원하고 정확값 window와 구분해야 한다.
  비검출을 0으로 채우지 않고 DAS/가스/영상의 원 알고리즘·교정 근거를 별도 보존한다.
- [운영 최적화](OPERATING_OPTIMIZATION_KO.md): 원 수송/압축 성능과 scheduling/lifecycle
  엔진에서 가변 모드·부하의 비용·효율·연속 연성·상관된 예측의 제약/목적식을 받아야 한다.
  현재 고정 배치·유한 전체계획의 최저 현재비용 결과는 그 공급 범위에만 적용한다.


## 지반·슬러리·파괴 진행의 후속 요청

[지반–관 요청 PI-X05/01](SOIL_PIPE_KO.md)은 원 구조 엔진의 인장분리·gap·
비선형 soil 접촉·대변위 및 공개 수지 감사 API 확장이다. 현재 bilateral 선형 지지를
비선형 매설관 모델로 재명명하지 않는다.

[슬러리 요청](SLURRY_TRANSPORT_KO.md)은 원 수송 엔진의 침적·농도분리·입도·
이질유동·난류 비뉴턴·열/마모 연성 및 적용영역 검증이다. 점도 구성식의 유효 전단률과
온도/농도 범위, 상관 불확실성을 반환해야 한다. 정지상태 항복 구간은 유일 압력차가 아니다.

[파괴 진행·정지 요청](FRACTURE_ARREST_KO.md)은 원 재료/동역학 엔진의 EOS·
가스 감압·동적 에너지 방출·속도 의존 저항·균열 경로 연성이다. 동시 공간/시계·
단위·source/config·재료방향·온도·수지·수렴을 보존한다. 현재 표본 비교는 동적 정지나
표본 사이 연속 유지의 증거가 아니며 정적인 KI 결과로 대체하지 않는다.


절차 기록의 ID·시간·검토자·철회 이력과 HRA 분리 계약은
[공통 절차 확장 요청](PROCEDURES_KO.md)을 따른다. 기록관리와 인적신뢰도 소유 엔진을
재사용하며 기록 만족을 자동 작업허가나 오류확률 0으로 바꾸지 않는다.


## 가스·좌굴·고형화 소유 엔진 요청

[비등온 가스](GAS_THERMAL_TRANSIENT_KO.md)는 원 동역학 엔진의 acoustic momentum,
혼합물/사용자 EOS rho-u flash·기준상태·전달물성 및 추가 분포 열경계 계약을 요청한다.
현재 유한 inventory와 준정상 접합 모델의 source-bound 장부를 유지해야 한다.

[좌굴](BUCKLING_KO.md)은 원 구조 엔진의 공개 K/Kg/DOF/기준하중 영수증과
비선형·셸 타원화/붕괴·결함·소성·추종하중의 별도 확장이다. eigen 모드가 없음을
무한 강도로 반환하지 않는다. 기존 선형정적 변위를 좌굴 검증으로 대체하지 않는다.

[고형화 경계](DEPOSITION_RISK_KO.md)는 원 상평형/공정 엔진에서 조성·물 활동도·
억제제·왁스 특성·불확실성과 시간/공간 근거를 보존한 검토 envelope를 받는다.
실제 석출 kinetics·열/물질 전달·deposit 성장/막힘은 별도 원 엔진 책임이며
CoolProp 단독 상 분류나 경계 노출시간으로 대체하지 않는다.


## 다른 세션용 개별 요청 문서

[소유 모듈 요청 목록](owner_requests/README.md)에 13건의 검토 가능한 요청을 분리했다.
`research/owner_extension_requests.json`이 내용 원본이고
`python scripts/export_extension_requests.py`가 실제 계약 이름·가이드·기술 ID를
AST/카탈로그와 대조해 개별 문서와 `research/owner_extension_contracts.json`의
소비자 소스 SHA를 생성한다. 수치 엔진을 import하거나 원 저장소를 수정하지 않는다.
원 소비자 코드가 바뀌면 다시 내보내야 한다. 생성기는 docs/owner_requests 문서를 관리한다.

각 요청은 세션에 전달할 수 있지만 구현 완료 또는 수락 시험 통과를 뜻하지 않는다.
이 저장소 내부 구조 API 작업도 섞여 있으므로 대상 컴포넌트를 먼저 확인한다.
외부 저장소 URL을 확인하지 못한 논리 컴포넌트에는 임의의 저장소 주소를 만들지 않았다.


PIPE-EXT-008의 공통 구조 factory/수지 감사/Ke-Kg 조립 공개 API는 이 저장소에서
작성했고 soil_pipe/buckling도 소비하도록 바꿨다. 이는 수치 검증 대기이며 재작성
요청이 아니다. 나머지 비선형 soil/접촉·셸/소성/결함 범위는 여전히 확장이 필요하다.


관 마찰 FRICTION-X01의 운전점 ID/group 관측축은 이 저장소의 inverse owner에서
작성했고 friction_study 소비자를 연결했다. 새 축은 과거 시간 API를 유지하며 같은
수치 커널을 사용한다. 선택적인 명시 공동 covariance/scaling 행렬도 이 저장소에서
작성해 같은 소비자에 연결했다. 훈련·heldout의 valid principal block만 각자 적용하고,
미상 상관·단위·근거를 자동으로 채우지 않는다. 이 경로도 수치 검증 대기다.
errors-in-variables 및 독립 실험 근거는 여전히 미완료다. 상세는
OPERATING_POINT_INVERSE_KO/FRICTION_STUDY_KO를 따른다.

PIPE-EXT-011은 `human-reliability-toolkit`의 공통원인 확장 요청이다. 이 저장소는
완전 Boolean grouped count의 정확 정규화·재생과 기존 joint-state FTA 결합까지만
소유한다. 검열/노출창·누락상태 의미, 공통원인 모수와 posterior/interval 불확실성,
시간순서·수리/회복·변화 노출은 원 위험 엔진에서 별도 typed 결과로 구현해야 한다.
요구당 확률을 연간 빈도로 바꾸거나 static joint states를 동적 수리모델로 재표시하지 않는다.

PIPE-EXT-012는 다품종 액체 배치 수송 원 해석기 확장 요청이다. 이 저장소는 요청·문맥,
시간–위치 조성/물성장, 총량·성분별 질량 원장과 재생 경계까지만 소유한다. 원 해석기는
advection/axial dispersion/interface tracking과 필요 열연성을 풀고 mesh/time refinement,
성분·총량 질량수지 및 독립 interface 도달/혼합폭 자료로 적용 범위를 입증해야 한다.
물 수질 엔진을 제품 배치 수송으로 재표시하거나 CoolProp 단독으로 혼합 규칙을 만들지 않는다.

PIPE-EXT-013은 누수 실험 가속도계·하이드로폰 취득/교정 확장 요청이다. 가속도계의
Sample/Value SI 변환·축/장착/교정과 A1/A2 clock 근거, 하이드로폰 RAW의 endian·word
width·framing·sample rate·code-to-pressure 변환과 H1/H2 동기화를 원 취득 소유자가
제공해야 한다. 현재 공개 어댑터는 이를 추측하지 않으며 대표 파일 전체를 독립 공급 export와
대조하고 held-out 사건에서 도달시간·위치·오검출/미검출을 평가하기 전까지 미자격으로 둔다.


## PIPE-EXT-016 WDSEventDB source-to-owner adapter contract

배관 통합 패키지는 이제 caller 검토 채널/clock mapping으로 source-bound `SignalFrame` batch를 만들고 exact replay한다. 아래 수신 모듈 요청은 이 batch를 받아 실제 feature/monitoring/FTA owner 경로에 연결하는 단계에 한정한다. 기존 `SignalFrame` 계산이나 분류기를 복제하지 않는다.

소유 monitoring/feature/FTA 모듈은 `WdsEventEvidencePacket`을 출처 메타데이터로 받을 수 있다. packet 자체에는 원 관측값이 없으므로 raw table 또는 별도 source reader를 통해 값을 가져와야 하며, 반드시 다음 입력 근거를 별도로 요구한다.

- 각 압력/유량/VFD/밸브 채널의 물리량, SI 변환식·원 단위·유효 범위, 센서 포트와 설치 식별자 및 교정 근거
- 실제 timestamp 열/기준시각/clock domain/time zone/sample interval과 동기화·누락·중복·순서 검토 결과; Excel 행 번호는 시간축으로 사용 금지
- 워크북 event ID가 나타내는 반복 run/실험 단위의 독립성 및 중복 신호 검토; 분할은 row-random 대신 run/group 기준
- `Labels` formula의 원 식, cached value 유무와 산출 정책, label-to-target 정의, onset/offset·평가 horizon 및 사후정보 여부
- owner 요청·계산 입력·출력에 source archive/table/selection digest, evidence artifact SHA, 변환 설정, 분할 ID와 검토 상태를 그대로 연결하고 replay API 제공

Owner는 원 측정값을 원본 reader에서 가져오며 packet의 `selected_excel_rows`, member, workbook SHA와 archive receipt를 대조해야 한다. 변환 표본 수, 제외/결측/invalid 수와 원 단위-표준 단위 변환을 함께 보고하고 값/행/시계를 조용히 추정하지 않는다. 검증은 workbook/run 단위 holdout을 사용하고, 임계값·모델·평가 horizon은 train partition만으로 고정한다. 독립 run이 부족하거나 timestamp/label provenance가 없으면 owner API가 결과를 거부/미자격으로 표시해야 한다. 이 계약은 owner 구현이나 field/FTA qualification을 뜻하지 않는다.

