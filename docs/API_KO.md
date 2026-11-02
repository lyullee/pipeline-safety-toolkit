# Python API와 교환 계약

공개 자료 취득의 출처·권리 메타데이터를 취득 시점에 고정하려면
`datasets.acquisition_provenance(spec, asset)`를 사용하고, receipt 재사용 전에는
`datasets.verify_acquisition_provenance(record)`로 검증한다. 취득 가용성은 사전
`datasets.require_acquisition_allowed(spec)`로 검사하며 자동 다운로드와 기존 사용자 파일이
같은 권리/용량 gate를 따른다. 반환되는 dataset catalog와 선택 자산 snapshot에는 각각
정규화 JSON SHA-256이 붙는다.
`asset_catalogued`는 선택 자산이 그 snapshot의 catalog asset 목록에 포함된 항목인지
구분한다.
`acquire_asset`와 `scripts/acquire_data.py`는 이 provenance를 파일별 receipt에
자동 첨부하고 research index가 이를 보존한다. 이 digest는 기록 변조/변경을
비교하는 식별값이며 라이선스 승인 또는 재배포 허가를 뜻하지 않는다. 이전 취득
receipt에는 없던 snapshot을 현재 catalog에서 소급 생성하지 않는다. 정책과 형식은
[자료 확보 및 권리 기록](DATA_KO.md)을 따른다.

실험 배관의 source-pinned 타격 CSV 한 파일을 선택해 bounded stream으로 읽을 때는
`percussion_data.read_percussion_recording`을 사용한다. 익명 수량 요약은
`summarize_percussion_recording`, 명시한 asset/sensor/clock과 caller-reviewed time-jitter
한계로 기존 acoustic `SampledTrace`를 만들 때는 `percussion_recording_to_sampled_trace`를
사용한다. 많은 CSV를 순차 처리할 때는 `PercussionArchiveReader`로 원본 해시와 listing을
한 차례 확인하고 여러 선택 member를 읽는다. 전체 자료와 편차, 누락 stroke 및 RAR listing 요구는
[배관 타격 신호 자료 안내](PERCUSSION_DATA_KO.md)를 따른다.

공정 제어실 공개 실험자료에서 caller가 검토한 simulator outcome을 시나리오별로
비식별 집계할 때는 `human_control_room_study.HumanControlRoomOutcomeMap`과
`summarize_human_control_room_study`를 사용한다. 매핑 근거는 context의 EvidenceRef ID와
SHA-256으로 고정한다. v2 요약은 행 단위 결과와 participant-scenario 묶음 상태를 함께 보존해
중복·상충·미매핑 행을 구분한다. 참가자 ID/행과 원 outcome은 포함하지 않으며 HEP/기간 위험을
계산하지 않는다. 원자료 reader와 모집단·자료 한계는
[공정 제어실 자료 안내](HUMAN_CONTROL_ROOM_DATA_KO.md)를 따른다.

HRA 0.5.0 `CollectionBundle`의 owner posterior를 기존 `RiskProbability`로 넘길 때는
`human_collection_exchange.bind_human_collection_estimate`를 사용한다. owner source pin,
collection audit 재확인, protocol/event 일치와 설비 condition/opportunity mapping 근거를
요구한다. opportunity 독립성 검토와 선택적 missingness 검토는 source-hash-pinned EvidenceRef ID가
필수이며 risk probability evidence에도 보존된다. 반환값은 owner estimate와 추가 근거를 분리해 보존한다. 재생 함수는
`verify_human_collection_risk_binding`이다. 이 adapter는 field HEP 자격이나 데이터의 현장 이전을
주장하지 않는다. 설정·증거 형식·한계는 [HRA collection 교환](HUMAN_COLLECTION_EXCHANGE_KO.md)을 따른다.

계측기별 clock offset/rate error를 원 관측에 적용할 caller-reviewed 후보가 필요하면
`clock_correction.ClockCorrectionModel`, `apply_clock_correction_candidate`,
`verify_clock_correction_candidate`를 사용한다. 원 프레임과 시각, 행 순서를 보존하며
근거·유효구간·잔차 상한을 봉인한다. 시간축 후보는 시계 동기화나 사건 인과 정렬의 증거가
아니다. 사용법과 한계는 [계측 시계 보정 후보](CLOCK_CORRECTION_KO.md)를 따른다.

이미 산출된 공정 점수 CSV를 출처·시간·결측을 보존해 기술 비교하려면
`derived_score_study.DerivedScoreMetadata`, `read_derived_score_csv`,
`evaluate_score_thresholds`, `verify_score_threshold_result`를 사용한다.
임계값 선택 근거는 호출자가 제공하며 이 경계는 원시 계측 복원, 점수 모델 검증,
독립 평가를 주장하지 않는다. 실행 절차와 자료의 한계는
[수질망 점수 기술 분석](WATER_SCORE_STUDY_KO.md)을 따른다.
시간 순서별 고정 임계값 비교에는 같은 모듈의
`ChronologicalThresholdRequest`, `evaluate_chronological_score_thresholds`,
`verify_chronological_score_thresholds`를 사용한다. 사건 근접 라벨을 고려해 cutoff
양쪽 최소 7일을 purge하며, 동일 사건 분리나 통계적 독립성은 입증하지 않는다.

조건부 물리 결과는 `consequence_exchange.PRISMConsequenceClient`의
`schemas/evaluate/annual_risk`를 사용한다. `ConsequenceCase`는 원 출구,
설비·수용체, 조건 ID와 수치·시간·공간 근거를 연결한다. 기본 의존성만으로
입력 계약을 작성할 수 있고 실제 실행은 별도 PRISM backend를 요구한다.
`AnnualRiskRequest`는 미계산 조건을 유지한 시간창 환산과 범위를 반환한다.
전체 사용법·원 엔진의 실패·범위의 의미는 [사고 결과](CONSEQUENCES_KO.md)를 따른다.

위험 엔진 경계는 `risk_exchange.HRARiskClient`가 제공한다. 메서드는
`bind_human_estimate`, `fault_tree`, `event_tree`, `lopa`, `annualize`,
`prism_demand`이며 마지막 메서드는 기존 PRISM 객체와 `record()`를 반환한다.
`run_risk_job`과 CLI `risk`는 같은 JSON 작업을 처리한다. 전체 입력 계약,
요구당 확률의 범위, 상태·계보·해시와 예제는 [위험 연결](RISK_EXCHANGE_KO.md)을
따른다. 외부 엔진은 해당 메서드를 실제 사용할 때 불러오며 계약만 작성하는
환경에는 HRA·PRISM을 요구하지 않는다.
관측 공동 상태는 `JointStateCount`, `JointStateCountTable`,
`joint_states_from_counts`, `verify_joint_state_count_receipt`,
`fault_tree_request_from_joint_counts`,
`fault_tree_request_from_verified_joint_counts`로 원 count와
모집단/grouping 근거를 보존해 기존 `fault_tree`에 전달한다. 이 API는 공통원인
모수를 적합하거나 표본 불확실성을 추정하지 않는다. `fault_tree_request_from_verified_joint_counts`
는 제공된 원 count 영수증을 재생 검증하고 동일 digest가 기존 FTA 요청에 실리는지
확인하는 convenience boundary다.

정보 오류·반응 오류 쌍의 결측 가능 기록에는
`response_exchange.information_response_missing_outcome_bounds`와
`verify_information_response_missing_outcome_bounds`를 사용한다. 이는 관측된 값과 양립하는
모든 완성에서 네 상태 비율의 유한 기록 경계를 계산하며 대치·표본 신뢰구간·모집단 전이를
하지 않는다. 상세 계약과 사용 예시는 [인적 반응 분석](HUMAN_RESPONSE_KO.md)을 따른다.
이를 기존 owner FTA top event에 전달할 때는 `response_exchange.information_response_fault_tree_missing_outcome_bounds`와 `verify_information_response_fault_tree_missing_outcome_bounds`를 사용한다. 네 결정적 사건 조합을 owner로 재생하고 결측 시행의 양립 상태에서 top-event 하한·상한을 계산한다. 한 조합이라도 owner가 미해결이면 전체를 보류한다.

다품종 수송은 `batch_transport.BatchTransportRequest`, `BatchComposition`,
`BatchInletStep`, `evaluate_batch_transport(..., provider=...)`와
`verify_batch_transport_record`를 사용한다. 외부 소유 solver의 조성·물성장과 질량
원장을 검증하며 기존 물 수질 모델을 다품종 모델로 재표시하지 않는다. 자세한 계약은
[배치 수송](BATCH_TRANSPORT_KO.md)을 따른다.

## 계산과 등록

```python
from pipeline_integrity import create_registry, evaluate

engine = create_registry()
models = engine.models()  # ModelInfo 튜플
schema = engine.describe("geometry.pipe")  # model, input_schema
# result = engine.evaluate(model_id, parameters, context)
# result = evaluate(model_id, parameters, context)
```

`Registry.register(ModelSpec(info, inputs, calculate), replace=False)`의 `inputs`는
`Contract`를 상속한 Pydantic 모델이다. 함수는 해당 입력 객체를 받아
`Calculation`을 반환한다. 모든 입력·출력에 단위 또는 의미 형식을 선언한다.
완료 결과는 선언한 출력을 모두 포함해야 한다. 중복 ID, 미선언 출력, 문자열
숫자, NaN·무한대 등은 거부한다. `json_input=True`일 때 parameters는 JSON
문자열이다. 배열을 Python 튜플로 옮길 때도 검증한다.

`ModelInfo`에는 모델 버전, 소유 구현 형태, 출처, 적용 가정과 qualification을
기록한다. 호출 함수의 소스 해시는 전체 저장소나 사용자 공급자의 구현을
고정하는 해시가 아니다. 배포 커밋·환경·공급자 버전을 별도 보존한다.

## 공통 계약

| 계약 | 필수 의미 |
|---|---|
| `RunContext` | asset_id, scenario_id, clock_id, origin, evidence, JSON metadata |
| `EvidenceRef` | ID, 원출처, 생성 방식, 위치, 선택 SHA-256·라이선스·제약 |
| `AssessmentResult` | 입력·값·단위·상태·가정·근거·실행 버전·해시 |
| `Pressure` | 값·단위와 absolute/gauge/differential 기준 |
| `FlowBoundary` | 구성요소·포트·시계·시간·방향·유체·물성 기준·절대압·온도·질량유량·엔탈피·상 |
| `SignalFrame` | 스트림·시계·경과 시간·선택 절대시각·값·단위·유효성·자료 생성 방식·모드 |

```python
from pipeline_integrity.contracts import Pressure

p_abs = Pressure(value=2.0, unit="bar", reference="gauge", ambient_pa_abs=101325.0).absolute_pa()
from pipeline_integrity.units import convert_quantity

q_si = convert_quantity(36.0, "m3/h", "m3/s")
temperature_k = convert_quantity(20.0, "degC", "K")
```

게이지압은 대기압을 명시해야 한다. 차압을 절대압으로 변환하지 않는다.
일반 단위 변환으로 기준압·기준체적·유효성의 의미를 추론하지 않는다.
`FlowBoundary.mass_flow_kg_s`는 비음수 크기이며 방향 필드와 함께 해석한다.
2상 경계는 증기 질량분율을 요구하지만 이 계약 자체가 2상 해석기는 아니다.

`SignalFrame`은 없는 값을 `None`으로 보존하고 유효한 결측 표본을 거부한다.
시계가 없는 관측에 임의 UTC를 부여하지 않는다. 절대시각을 공급하면 시간대가
필요하다. 열 이름·단위·valid 키가 같아야 한다.

## 물성 공급자

```python
from pipeline_integrity.thermo import CoolPropProvider, StateRequest

state = CoolPropProvider().resolve(StateRequest(fluid="Water", inputs={"P": 101325.0, "T": 300.0}))
```

독립 상태쌍은 P/T, P/Hmass, P/Smass, T/Dmass, P/Q, T/Q다. 지원 백엔드는
HEOS·IF97·PR·SRK다. 상태쌍과 백엔드에 따른 유체 지원 여부는 CoolProp이
검사한다. `single_phase_required=True`가 기본이며 상이 불명확하거나 2상이면
단상 계산에 사용하지 않는다. 지원하지 않는 수송 물성은 None으로 보존한다.
캐시는 한 프로세스에서 최대 4,096개 요청을 유지한다.

`PropertyProvider.resolve()` 구현을 공급해 기본 물성을 바꿀 수 있다. 모델
레지스트리 생성 시 ID·버전도 요구하며 반환 객체의 유체·백엔드·상 형식을
검사한다. 공급자의 정확성·상태 방정식·검증 책임은 공급자에게 남는다.

## 계산 작업 흐름과 기준

`Workflow`는 `WorkflowStep`들의 비순환 의존 그래프다. 각 단계는 입력값과
`ResultBinding(step_id, output_name)`을 받는다. 같은 입력을 두 방식으로 주거나,
없는 단계를 참조하거나, 순환하면 거부한다. 바인딩 단위와 압력 기준은
문자열까지 같아야 한다. 자동 단위 변환은 하지 않는다.

`run_workflow(engine, workflow)`는 부모 결과의 해시를 남기고, 부모가 미완료면
자식 계산을 보류한다. 단위 일치는 물리량·설비·운전모드의 동일성을 증명하지
않는다. 서로 다른 설비·시나리오를 조합할 때는 사용자가 연결 의미를 검토한다.

`DeclaredRequirement`는 model_id, output_name, 비교 연산, limit, unit,
criterion_source와 applicability를 요구한다. `evaluate_requirement`는 유효한
단일 수치에 대해서만 만족 여부를 판단하고 결과·기준의 해시를 남긴다.

## 현장 절차와 외부 호출

`Procedure`에는 적용 설비군·버전·항목별 작업·근거 기준·필수 증거·선행 작업을
선언한다. `ProcedureRecord`는 검토자·시각·판정·증거를 갖는다.
`reviewed_at`은 UTC offset이 있는 ISO 8601 timestamp여야 한다.
`assess_procedure(procedure, records, context, asset_class=...)`는 근거 누락과
선행 작업 미완료를 표시한다. 기록이 모두 만족해도 물리 안전성을 별도로
승인하지 않는다. 결과 replay는 검토시각의 원 UTC offset을 포함한 JSON 표현을 비교한다.

`MaterialClient.evaluate(model_id, parameters, context, location_id=...)`는 기존
`matintegrity.assessment.v1`를 호출한다. 결과·보류 상태·자산·위치·자료 생성
방식을 보존한다. `monitoring_batch(frames)`와 `hra_signal_series(frames, channel)`은
기존 프로젝트의 실제 클래스를 만든다. 팩토리 주입은 사용자 통합과 검증에
사용한다. 임의 리샘플링·레이블 생성·HEP 추정은 수행하지 않는다.

물 관망은 `simulate_water_network(path, WaterRun(...))`로 호출한다. 정수 초
시간 설정, 명시한 솔버 허용오차, 입력 파일 해시, 연속 방정식 잔차를 반환한다.
`pressure_head_m`는 물 압력 수두이며 절대압력이 아니다.

## 압력파와 공급 곡선·공간 재고

`LiquidTransient`는 `LiquidNode`, `LiquidPipe`, `History`를 조합한 관망을 받는다.
`simulate_liquid_transient(specification, context)` 또는
`pipeline-integrity liquid-transient examples/liquid_transient.json`으로 실행한다.
History는 0부터 시작하는 경과 초와 값의 튜플이며 선형 보간·마지막 값 유지를
사용한다. head 경계 값은 m, withdrawal 경계 값은 m³/s다. 대기 emitter 계수는
m³/s/√m다. `LiquidPipe.elevation_profile`에는 관 시작 노드에서 측정한
`chainage_m`와 `elevation_m`의 점들을 선택 입력할 수 있다. 점은 관 전체 길이를
덮고 끝점 고도는 연결 노드 고도와 일치해야 한다. MOC 공간격자에 선형 보간해
고점/저점을 압력 검사에 반영한다. chainage는 유량 부호와 관계없이 항상
`start_node`에서 `end_node`로 증가하므로 역방향 유량에서도 프로파일 순서를
뒤집지 않는다. 프로파일을 주지 않으면 노드 두 끝점 사이의 선형 고도를 사용한다.
실제 유량 경계를 밸브 명령이나 개도로 해석하지 않는다.

해석은 각 관의 공간 간격/공통 시간 간격에서 CFL=1이 되도록 파속을 제한적으로
조정하고 조정량을 보고한다. 초기 관 마찰 평형·접점 수지를 검사한다. 보존량
잔차와 증기압/공기 유입 경계가 허용 범위를 벗어나면 computed로 전달하지
않는다. 반환 시간·수두·유량과 시계·공간 격자·파속·체적수지·절대압력 최솟값을
보존한다. `vapour_pressure_margin_history_pa`는 각 MOC 시간점 최소 절대압에서
공급 증기압을 뺀 값이다. 최솟값의 `pipe_id`·격자 index·pipe 길이 비율과
`first_vapour_pressure_boundary`를 추가 반환한다. 경계 시각은 시간격자에서 처음
검출된 값이며 event 위치는 그 시점의 최저압 격자점이다. 시간/공간점 사이의 정확한
상변화 전선 위치나 cavitation/column separation 해는 아니다. 적용범위 밖이면 기존처럼
`out_of_scope`를 반환한다. 단계 수·총 격자·반환 값 수의 예산을 입력으로 조정할 수 있다.

`equipment.pump_system`는 공급자가 제공한 `ReferencePumpCurve`와 정수두+
유량 제곱 형태의 계통 곡선을 받는다. 동일 펌프의 직렬·병렬 수, 상사 속도·
직경 비, 선언 효율을 적용한다. 단조 감소 가지에서 PCHIP와 bracket 해법을
사용하고 원곡선 밖을 외삽하지 않는다. 비단조 맵은 별도 모델이 필요하다.

`inventory.segmented`는 ID·P_abs·T·체적이 있는 세그먼트들을 받아 실제 질량,
기준 체적과 공통 물성 기준의 내부에너지를 합산한다. 상태·체적 분할은 사용자가
공급한다. `u=h-P/rho`를 사용하며 운동·위치 에너지는 포함하지 않는다.
압력·온도 수송 또는 이동하는 상 경계를 계산하는 모델은 아니다.

`lh2dt_state`는 수소 장치 소유자의 실제 P/h 상태를 반환하고 유체·물성 기준·
온도·상·품질을 확인한다. `prism_port_state`는 실제 `ProviderPortState`를
`PortHandoff.native`로 반환한다. 대상 형식에 없는 시계·물성 기준은
PortHandoff의 원본 경계와 문맥에 보존한다. PRISM에 native 객체를 전달하더라도
해당 봉투를 근거로 함께 보관한다. 이 변환 자체는 방출·피해·빈도 계산이 아니다.

## 기존 장치 팩토리와 상태 보존

`LH2EquipmentClient`는 소유 프로젝트의 `build_asset_model`을 호출한다.
`models()`에서 22종의 필수 입력을 조회할 수 있지만, 팩토리의 지원 목록은
22개 모델의 현장 검증 또는 자체 구현을 뜻하지 않는다.

```python
from pipeline_integrity.equipment_exchange import LH2EquipmentClient, EquipmentRequest

client = LH2EquipmentClient(
    fluid="ParaHydrogen", property_basis="CoolProp8_HEOS_ParaHydrogen_default_reference"
)
print(client.models())
# built = client.build(EquipmentRequest(model_id="pipe", parameters=parameters,
#     parameter_source="reviewed_parameter_set"), context)
# packet = client.evaluate_two_port(built, left_request, right_request)
```

생성 결과는 `BuiltEquipment.native`와 `report`다. `ready`는 소유 객체를 만들 수
있다는 뜻이며 계산 완료가 아니다. 미지의 필수 입력은 `needs_data`, 잘못된
입력은 `rejected`로 남기고 객체를 반환하지 않는다. 파라미터·문맥·물성 기준·
소유 코드 버전/소스 해시를 보존한다. 해시는 Python 패키지 소스 범위이며 외부
데이터·전이 의존성 전체를 고정하는 잠금은 아니다.

기본 `reviewed_fields_only` 정책은 확인하지 않은 파라미터 이름을 거부한다.
기존 소유 팩토리가 모르는 필드를 무시할 수 있기 때문이다. 사용자가 직접
소유 API를 검토한 경우 `parameter_policy="owner_native"`를 선택할 수 있고,
미검토 이름도 보고서에 남는다. 숫자·형상·모델별 상세 검증은 소유 계약을
따르며 `parameters` 내부 전체가 이 패키지의 개별 수치 모델 스키마와 같다는
뜻은 아니다. 입력 이름이 맞아도 실제 소비 필드의 전체 감사는 소유 API 확장이 필요하다.

P/T·P/Hmass·P/Smass 상태쌍을 같은 수소 종류·HEOS·물성 기준으로 전달한다.
정확한 포화/2상 상태가 필요하면 `single_phase_required=False`를 명시한다.
`initialize_dynamic_pipe`는 자산·파라미터·시계 해시를 가진 `EquipmentState`를
반환한다. 다른 설비·파라미터·시계의 상태를 붙여 계산하지 못하게 한다.

소유 팩토리는 고유 단위 관례를 유지한다. 특히 회전수는 rpm이고 소유자의
`mapped_pump.head_curve_points`는 (kg/s, Pa)다. 이 패키지의 `equipment.pump_system`
곡선인 (m³/s, m)와 서로 다르다. 밀도·중력·회전 단위를 명시해 변환해야 하며
두 곡선을 이름만 보고 같은 데이터로 전달하면 안 된다.

`step_dynamic_pipe`는 소유자의 한 스텝을 호출하고 `advance_dynamic_pipe`는 같은
구간을 시간 세분화해 비교한다. `DynamicStepTolerance`의 질량 kg·에너지 J·벽온도
K 절대 허용오차와 상대 기준을 사용한다. 한도 안에서 일치하지 않으면
`DynamicAdvanceError.diagnostics`에 보류 근거를 남긴다. 이 비교는 독립 오차
상계나 실제 실험 검증이 아니다. 소유 응답의 마지막 미분 수지와 전체 구간의
누적 수지도 구분한다. 실행 예제는 `scripts/check_equipment_factory.py`다.

이 advance에서는 양단의 선언 상태가 구간 안에서 일정하다. 상태나 명령이
변하는 사건 시각에서 구간을 나누고, 네트워크와 연결할 때는 소유 동역학
조립기의 수지·시계·종료 조건을 별도로 사용한다.

## 배관 사고 이력과 위험 모듈 경계

`release_exchange.PipeAccidentRequest`는 사고 ID·출구·시계·시작 시각, 초기 절대압과
온도·선택 품질·벽온도, 주변 상태, 실패 개구·유출 계수, 관찰 기간·시간 간격,
ENU 위치와 단위 방향을 명시한다. 품질을 주면 같은 압력의 포화 온도와 일치해야 한다.
명목 간격 수와 반환 간격 수는 최대 100개다. 양방향 파단은 이 프로토콜의 대상이 아니다.

```python
from pipeline_integrity.release_exchange import PipeAccidentRequest, prism_release

# client와 built는 같은 명시적 수소 종류·물성 기준의 dynamic_hem_pipe다.
# request = PipeAccidentRequest.model_validate_json(request_json)
# history = client.export_accident_history(built, request)
# if history.status == "exchange_checked":
#     handoff = prism_release(history)
#     native_packet = handoff.native
#     record = handoff.record()
```

`LH2EquipmentClient.export_accident_history`는 소유 엔진을 호출한다. 원 응답을
보존하고 자산·사건·유체·기준압·경과 시계, 좌측 유지 질량/엔탈피 적분, 유한
재고의 질량 장부와 같은 상태방정식의 기체 분기 물성 일치를 검사한다.
불일치는 `AccidentHistory.status="withheld"`와 `issues`에 남으며 PRISM으로 전달하지 않는다.
`exchange_checked`는 이 교환 검사를 통과했다는 뜻이다. 독립 실험 검증·전체
내부에너지/열 원장·위험 계산 완료가 아니다.

소유 응답의 시각은 사고 시작부터의 경과 초다. context의 `clock_id`와 요청한
`start_time_s`를 봉투에 보관한다. native 패킷을 전체 공정 시각으로 임의 이동하지 않는다.
위치의 ENU 좌표는 m이며 방향 벡터와 다르다. 액체 도달 위치·비행 시간·지름을
주려면 세 값을 모두 공급한다. 소유 함수가 좌표를 바꾸거나 혼합 밀도를 기체
밀도로 출력하면 보류한다. 누락된 액체 도달 근거를 기본값으로 승인하지 않는다.

`isolation_time_s`는 이번 소유 export에서 순간 개구 폐쇄를 지정한다. 양의 관찰
꼬리를 명시해야 하며 반환 시계가 그 사건을 유지해야 한다. 이 입력은 명령 수신,
유한 구동 또는 차단 뒤 배관 재고의 방출에 대한 근거가 아니다. 소유자가 반환한
`reviewed_isolation_success` 문자열을 현장 방호 승인으로 승격하지 않는다.

`prism_release`는 PRISM의 기존 기체/액체 방출 빌더를 호출하고 결과와 원 이력의
해시·문맥·물성 기준을 함께 반환한다. 원 관측 출처가 measured여도 계산 이력은
model_prediction이며 원 출처는 context에 남는다. 합성 사례는 synthetic이다.
빈도·회복·방호·확산·피해를 자동 생성하지 않는다. 이력 또는 패킷을 변경하면
해시 검사에서 거부한다. 설치된 두 소유 패키지가 필요하며 팩토리 주입도 가능하다.

재현 명령은 `scripts/check_release_exchange.py`와 연동 문서에 있다. 현재 일반
수소 기체 기준 사례는 통과했고, 파라수소 표시 및 2상 분기·좌표 사례는 보류했다.

## 반복 검사와 재료 스키마 검색

`MaterialClient.models()`와 `describe(model_id)`는 기존 재료 레지스트리의 모델 ID와
실제 입력 스키마·출력 단위·가정·확률 의미를 반환한다. 사용자 evaluator를
주입했다면 검색에 사용할 소유 `registry_factory`도 명시한다. 검색 성공은
그 목록 전체의 수치·현장 검증을 뜻하지 않는다.

`ili.ILILoadRequest`와 `load_ili_run(archive, member, request)`는 선택 용접부의
정규화된 `InspectionRun`과 읽기 보고서를 반환한다. 원본을 끝까지 읽는 것이
기본이며 축약된 읽기는 complete로 표시하지 않는다. report에는 선택 source row 번호·digest와 budget 조기 종료 사유를 기록한다. `verify_ili_load_record(run, report, request, archive_path=None)`는 row manifest·invalid row·run feature·partial scan 상태와 digest를 교차검증하고 archive 경로를 주면 실제 재판독 결과까지 비교한다. `maximum_archive_bytes`로 압축 archive 입력 예산을 조절할 수 있고, 같은 file handle의 hash/parse와 전후 filesystem identity를 검사한다. 계산용 feature ID는 해당
회차 관측의 ID이며 동일 물리 결함의 ID가 아니다.

`match_inspections(baseline, followup, registration, policy)`는 명시한 용접부·좌표
변환과 위치 gate로 비용 최소 일대일 후보를 만든다. 결과는 선택·모호·미대응·
미등록 관측과 입력/정책 해시를 보존한다. 비용은 확률이 아니며 깊이를 목적함수에
넣지 않는다. SciPy를 포함한 engineering 추가 설치가 필요하다.

`InspectionTrack`과 `CorrespondenceReview`로 검토한 경로를 공급하고
`evaluate_inspection_track(track, runs, matches, material_client, context)`를 호출한다.
시각·측정 오차·좌표 등록·대응 검토가 없으면 needs_data이며 재료 성장 함수를
호출하지 않는다. 검사를 통과하면 기존 `corrosion.thickness_trend`의 응답과 별도의
최악 계측 오차 경계를 보존한다. 추정 명목 잔여 두께와 실제 UT 측정은 구분한다.
owner trend 호출 전 context EvidenceRef에서 각 회차 source ID·SHA·origin, 시계 기준,
좌표 등록 승인, 승인된 대응 검토 근거를 모두 확인한다. 중복 evidence ID나 누락·불일치
근거는 needs_data로 반환하며 owner를 호출하지 않는다.
전체 계약·단위·예제·한계는 [반복 검사 문서](INSPECTION_KO.md)를 따른다.

## 물 성분 수송

`water_quality.WaterQualityRun`은 `WaterRun`, mode, 초기 상태, 주입·반응, 상위
시계 오프셋, 파라미터 출처와 계산 예산을 받는다. `simulate_water_quality(inp,
specification, context)`는 농도 kg/m³·나이 s·출처 비율 1 중 하나의 노드/관
이력과 수력 결과·원 엔진 수지·경고·해시를 반환한다.

`water_quality_frames(result, node_ids=..., stream_id=...)`와
`water_quality_batch(result, node_ids=..., stream_id=..., factory=...)`는 계산 상태와
해시가 유효할 때만 전달한다. metadata에는 결과 해시·자산·시나리오·수량 의미·
초기 문맥의 생성 방식·파라미터 출처가 남는다. `adapters.monitoring_batch`도
선택적인 `metadata`를 받으며 기존 시계·스트림·origin과 모순되는 키는 거부한다.

`quality_threshold_arrival(result, node_id, threshold)`는 첫 임계값 도달 보고
구간 또는 보고 기간 내 미도달을 반환한다. 연속 도달시각이나 확률을 계산하지
않는다. 주입 4종·정수 초 패턴·반응·단위·예산은 [성분 수송](WATER_QUALITY_KO.md)을 따른다.

`WaterRun.maximum_hydraulic_periods`는 실제 수력 단계 수를 제한한다. 수력
전용과 수송 경로는 같은 엔진 실행기를 사용하며 수지 통과만으로 경고를
무시하지 않는다. 반환 `native_diagnostics`에서 단계별 경고와 최대 수렴 통계를 확인한다.

## 범위가 있는 파라미터 추정

`inverse.fit_parameters(observations, request, context, predict)`는 `FitParameter`,
`FitRequest`, `Observations`를 사용하고 predict는 `Prediction`을 반환한다.
관측·예측의 자산·시계·단위와 표본을 검사하고 학습 시각만 목적함수에 사용한다.
경계 좌표·척도·차분·손실 함수·최적화 및 실제 모델 호출 예산을 설정할 수 있다.
`fitted`는 파라미터 추정 상태이며 검증 개선·기본 모델 교체를 의미하지 않는다.

`water_inverse.WaterForward`는 `WaterSensor`와 `WaterParameter`로 관망의 센서와
수정할 속성을 결합한다. 원본을 새로 읽어 메모리에만 변경하고 공통 EPANET
경로로 실행한다. 파라미터 단위는 `parameter_units`에서 조회한다. 보정 JSON은
`pipeline-integrity water-fit examples/water_calibration.json`으로 실행한다.
상세 스키마·6종 결합·시간 분리·국소 rank와 한계는 [보정 문서](CALIBRATION_KO.md)를 따른다.

## 파형 시간차와 조건부 위치

`acoustics.SampledTrace`는 표본 시계·수량·단위·valid·출처를 가진다.
`estimate_delay(left, right, DelayRequest, context)`는 정규화 상관 또는 GCC-PHAT의
부호 있는 시간차·경쟁점·코히어런스·품질을 반환한다. 시계 근거가 없으면
corrected_delay_s는 None이며 apparent_delay_s와 구분한다.

`locate_on_span(delay_result, PipeSpan, context)`는 상대 시계·단일 경로·구간 내
소스·파속/유속/위치 경계를 요구한다. 조건이 빠지면 needs_data다. 범위 밖
명목 위치는 보류하고 구간에 잘라 맞추지 않는다. 경계는 사용자 입력 구간의
보수적 전파이며 신뢰구간·사건 확률은 아니다.

`delay_feature_frames`는 증가하는 시계와 품질을 유지해 모니터링으로 전달한다.
`localize_acoustic_leak(left, right, request, span, context)`는 지연·위치 계산을
합성해 원 파형 해시와 두 하위 결과의 재생성 가능한 해시를 한 객체에 담는다.
자산·시계 불일치는 거부하고, 자격 조건 미충족은 기존 `needs_data`로 전파한다.
위치식은 기존 `locate_on_span`만 사용한다.
`acoustic_data.SheffieldArchive`는 동시 측정 CSV만 읽고 결합 거리 행렬을 거부한다.
예제·CLI·단위·수식·실제 자료 적용은 [음향 문서](ACOUSTICS_KO.md)에 있다.

## 유한 구동과 액체 밸브

`valve_motion.ValveMotionRequest`는 명령·표본 시계·스트로크 시간·개도별 CdA
곡선을 받는다. `simulate_valve_motion`은 기존 구동기를 호출하며 결과의 원
명령·공급자·해시를 보존한다. `liquid_valve_from_motion`으로 생성한 LiquidValve를
`LiquidTransient.valves`에 연결한다. CdA의 단위는 m²이고 개도·표본·곡선은
사용자 입력이다. `motion_frames`는 개도·목표·CdA의 simulation 신호를 만든다.

밸브 수력은 SciPy를 쓰지만 계약 형식과 밸브 없는 MOC는 핵심 의존성으로
사용한다. 명령 시각·목표 도달·유량 차단의 의미와 수치 예산·관별 수지·
기본 JSON 실행은 [밸브 문서](VALVE_TRANSIENT_KO.md)를 따른다.

`pump_transient.PumpCoastdownRequest`, `evaluate_pump_coastdown`,
`verify_pump_coastdown_record`는 원 펌프 동특성 공급자의 속도·유량·양정·동력과 회전자
에너지 원장을 검증한다. 동력 적분, `rho*g*Q*H`, `0.5*I*omega^2`, 수치보정 상한을
각각 검사하며 펌프 동역학이나 MOC 동시해법을 복제하지 않는다.
[펌프 정지 경계](PUMP_TRANSIENT_KO.md)를 따른다.

## 압력 이력과 재료 피로

fatigue_exchange.PressureFatigueRequest는 압력 이력·기준·벽 온도·형상·검토된
곡선과 초기 손상·한계의 근거를 받는다. assess_pressure_fatigue는 기존
MaterialClient를 통해 원 재료 워크플로를 호출한다. 미확정 압력 기준·결측은
needs_data이고 원 적용 범위 보류도 보존한다. pressure_history_from_transient는
원 MOC 사양·결과에서 선택 관 끝점의 절대압을 변환하며 온도는 선언 입력이다.

결과 receipt의 check_integrity와 native_result로 원 입력·응답을 추적한다.
코드·JSON·CLI 예제, 기원 매핑·원주 막응력 범위·반주기·손상 의미는
[피로 문서](PRESSURE_FATIGUE_KO.md)를 따른다. 기존 피로 계산을 재구현하지 않는다.

## 정상 기체 관망

GasNetwork는 GasBoundary·GasJunction·GasPipe와 유체·근거·수치 예산을 받는다.
solve_gas_network는 기존 원 엔진을 호출하고 질량·정적 엔탈피·운동량·영역을
따로 검사한다. 반환 GasNetworkResult의 native는 원 결과와 노드/도착 물성을
보존하고 check_integrity로 변경을 확인한다. 지정 질량 인출은 경계 조건이며
소비 장치 성능 계산이 아니다. 고도차·정체·상·물성·잔차 보류를 확인한다.
`checks.pipe_flow_summary`는 각 원 pipe ID의 start→end signed kg/s, flow direction, segment IDs를 정규화해 제공한다. native link의 endpoint 방향이 뒤집혀도 각 부호를 원 관 start→end 기준으로 정규화한다. endpoint chain이 맞지 않으면 거부하고, 분할 간 흐름 불일치가 허용값 밖이면 요약은 unresolved이며 결과를 withheld한다.

read_gaslib는 ZIP의 .net/.scn에서 SI 형상과 원 단위·지정 운전·장치를 읽는다.
미지 조성·압력 기준·열 경계·능동 장치를 추정하지 않는다. 전체 재현과
backend 교체·기준·한계는 [기체 관망 문서](GAS_NETWORK_KO.md)를 따른다.

## 운전·정비·검사 일정

scheduling.ScheduleRequest는 필수 Operation·대체 OperationMode·PlanningResource·
CapacityWindow·TimingRelation·ResourceHold·소모품과 선언 비용을 받는다.
solve_schedule은 선택 CP-SAT backend를 호출하고 audit_schedule로 반환 배정을
원 입력에서 다시 검사한다. optimal/feasible/infeasible/unknown/withheld와
proven_optimal을 구분하며 결과 check_integrity로 중첩 변경을 확인한다.

schedule_exchange.bind_hra_task는 원 Task·단계/역할 매핑·해석 근거를 받는다.
planned_hra_steps는 같은 문맥의 통과한 계획을 simulation 시각으로 전달한다.
원 기한·전제·실제 완료나 확률을 추정하지 않는다. 자세한 단위·격자·목적·
하한·자원 구간·실제 재현은 [일정 문서](SCHEDULING_KO.md)를 따른다.

## 인지·시간·복구 경계

`response_exchange.HumanResponseClient.cognitive_estimate`는 `CognitiveResponse`의
순서 있는 조건부 단계로 원 HRA 초기 Estimate를 얻는다. `evaluate(initial, event,
HumanResponseRequest, RunContext)`는 고정 기한의 조건부 시간 모델 또는 시간 제한
없는 복구 중 하나를 적용한다. `HumanResponseResult.risk_probability()`는 검증된
RiskProbability를 반환하며 `HRARiskClient`의 FTA·ETA·연간 환산 입력으로 사용한다.
`DeadlineScenarioSet`과 `evaluate_deadline_scenarios`는 근거가 연결된 이산 가용시간
분포의 각 지점에서 기존 HRA 고정 기한 모델을 호출하고, 요구 모집단 정의와 합이 1인
시나리오 `probability_mass`로 가중 평균하여 결과를 `HumanResponseScenarioResult`에
저장한다. 행마다 `time_model`을 교체하면 해당 조건의
`response_population_definition`도 요구한다. `.risk_probability()`는 검증된
RiskProbability를 반환한다.
`verify_deadline_scenario_result`는 각 소유자 호출과
집계를 재생한다. 점 사이를 보간하지 않으며 누락된 시나리오가 있으면 전체 결과를
보류한다. `DeadlineScenarioSet.maximum_scenarios`와
`maximum_empirical_observations`가 계산량을 제한한다. 이는 caller가 제공한 이산
분포의 조건부 적분 근사이지 연속 분포·사람과
공정의 의존성 모델이 아니다.
원 엔진은 별도로 설치한다. 전체 입력·상태·단위·제약은 HUMAN_RESPONSE_KO를 따른다.

쌍 오류의 표본불확실성을 FTA에 연결할 때는
`response_exchange.information_response_fault_tree_cluster_bootstrap`에 기존
`FaultNode`, 쌍 사건 ID와 범위가 맞는 두 `RiskProbability`, `HRARiskClient`,
`InformationResponseFaultTreeBootstrapRequest`를 전달한다. cluster 전체를 재표집하고,
각 표본에서 사건 주변확률을 joint counts로 다시 계산하여 기존 owner FTA에 함께 전달한다.
결과는 단일 top event의 pointwise 구간이며, 재생은
`verify_information_response_fault_tree_cluster_bootstrap`로 수행한다. sampling·tree
budget 설정과 사건 의미의 책임 경계는 [인적 반응 설명](HUMAN_RESPONSE_KO.md)에 있다.

## 유한 검사 캠페인

`inspection_planning.InspectionCampaign`은 가설·검사 방법·예약 시각·자원·예산과
미발견 가중량 기준을 정의한다. `plan_inspections(campaign, context, materials=...,
backend=...)`는 원 재료 호출과 공통 일정 결과를 `InspectionPlan`에 보존한다.
새 솔버를 직접 구현하지 않고 ScheduleRequest로 컴파일하며, 모드별 실행 창은
공통 일정 API에서도 사용할 수 있다. 결과의 올림 최적성·반올림 경계·원 점수와
물리 파손 확률을 구분한다. 전체 입력과 제약은 INSPECTION_PLANNING_KO를 따른다.

## 원 NDE 파형과 초음파 교환

`nde.load_tubers_waveform`은 NDEWaveform에 signed seconds/Volts, 원 행·결측·해시를
보존한다. `tubers_visual_pairs`는 PNG 헤더/파일명 대응과 누락을 반환한다.
`evaluate_echo_thickness(trace, EchoThicknessRequest, RunContext, materials=...)`는
명시한 구간·에코/교정 근거를 원 재료 엔진으로 전달한다. 이 해석은 연구용이며
실제 에코/두께/검출 기회를 자동 판정하지 않는다. 전체 계약과 재현은 NDE_DATA를 따른다.

## 탱크 형상·액위·정적 재고

`tanks.evaluate_tank_inventory(TankInventoryRequest, RunContext, property_provider=None)`는
형상 또는 CalibratedTankCurve, TankObservation, 선택적 DeclaredTankDensity/
PropertyTankDensity를 받는다. 결과의 status는 형상 변환, mass_status는 전체
유체 질량의 상태다. 미지의 양의 기상 공간 질량과 전체 질량은 null로 유지한다.
상태별 물성에는 기존 CheckedProvider를 사용하며 유체·상·절대압·상태 근거를 확인한다.
`lh2dt_tank_geometry(geometry, factory=None)`는 기존 HorizontalVesselGeometry와
입력·소스 해시·17점 체적 검토 기록을 반환한다. 수평 동일 평판/타원 끝단에 한정된다.

`tank_data.review_coupled_tank_source(root)`는 단위/메타데이터 공백을 보고한다.
`coupled_tank_frames(root, CoupledTankReadRequest)`는 실험 번호·단위·영점·근거·
origin을 명시해 공통 SignalFrame을 만든다. 원 시각·원 행·단위 매핑·결측을
보존하며 마지막 펌프 명령을 보간하지 않는다. 전체 계약은 TANK_INVENTORY를 따른다.

## 시계 있는 제어와 계측 시나리오

`control_exchange.PIDSession(PIDLoop, RunContext, initial_time_s=...)`는 원 PID를
호출한다. `step(decision_time_s, setpoint, ControlObservation)`은 상태·원 결과·
기준/단위·근거·판단 이력 해시를 반환한다. `checkpoint()`/`restore()`는 동일
설정/context/공급자에 한해 직렬화 상태를 이어간다. ControlAxis는 SI 단위와
pressure_reference/reference_id를 보유하며 PIDGains는 차원 있는 세 단위를 받는다.
결측 정책은 withhold/hold_last/fixed_output이고 부적합 관측에서는 상태를 동결한다.

`instrument_exchange.replay_transmitter(TransmitterScenario, frames, context)`는
원 필터·표시와 선언한 drift·delay·dropout·reported clock을 연결한다.
시계 offset/rate를 주입하면 `reported_clock_id`를 별도로 요구하고 SignalFrame에 그
ID를 기록한다. `transmitter_monitoring_input`은 지연이 없고 identity clock인 완전한
연속 packet 범위만 `MonitoringSplit`으로 봉인하며 invalid/dropout 행을 보존한다.
현재 monitoring 입력이 행별 arrival time/시계 mapping을 받을 수 없어 지연 또는
별도 reported clock은 거부한다. 확장 요청은 [PIPE-EXT-015](owner_requests/PIPE-EXT-015.md)다.
`packet_observation(packet, axis=..., evidence_id=...)`는 seal과 axis를 검사해
PID 입력을 만든다. 음수 보고 시각·미지 입력·원본 origin을 유지하며 교정/고장률을
추정하지 않는다. 전체 필드·시계 정책·실제 실행과 한계는 CONTROL_EXCHANGE를 따른다.

## 재료 스키마 고정 작업과 고체 온도

`material_exchange.prepare_material_job(model_id, parameters, context, location_id=...,
evidence_ids=..., client=...)`는 원 모델 버전·스키마·출력 단위·확률 의미를 기록한
MaterialJob을 만든다. `evaluate_material_job(job, context, client=...)`는 실제 원
입력/시계/물리 위치·상태·해시를 확인하고 MaterialReceipt에 원 결과를 보존한다.
`check_integrity()`로 저장 후 변경을 검사한다. 원 해석 가정과 별도 context 필요
조건은 자동 해결하지 않는다.

`evaluate_pressure_section(PressureSection, context)`는 내외압을 절대압으로 명시
변환해 원 원통 탄성 API를 각 반경에서 호출한다. `solid_thermal_frames(receipt)`는
원 고체 열 computed 결과만 simulation SignalFrame으로 반환한다.
`thermal_rod_frames(archive, run_id)`는 검토한 원 ZIP과 member 해시를 확인한 뒤
기존 reader의 excess K·counts·실험 clock을 보존한다. 전체 계약은 MATERIAL_SECTIONS를 따른다.

## 균열·성장·사용자 파괴 경계

`fracture_exchange.assess_mode_i(ModeIRequest, RunContext, client=...,
opening_stress_receipt=...)`는 검토한 개시 저항·형상/하중·온도/재료를 원 K API로
전달한다. native critical depth와 지원 범위 안의 qualified 값을 구분한다.
`assess_crack_growth(CrackGrowthRequest, context)`는 순서 있는 tensile blocks·깊이별
Y·SI 계수·ΔK/R/온도 범위·threshold/저항을 원 variable_paris로 전달한다.

`opening_stress_from_section(section, SectionOpeningMapping, context)`는 검토한
스칼라 변환 receipt를 만들며 Mode-I와 digest/정의/값을 바인딩한다.
`assess_failure_points(parameters, context, location_id=..., evidence_ids=...)`는
사용자 허가 경계의 원 중첩 계약을 strict JSON으로 실행한다. MaterialJob도 같은
JSON 경로이고 기존 MaterialClient 기본은 strict Python이다.
`crack_arrest_frames(archive, specimen_id)`는 원 reader의 signed SI·sample clock과
filename metadata·header flag를 보존한다. 전체 계약은 FRACTURE_EXCHANGE를 따른다.


## 선형 프레임과 signed 하중 신호

pipeline_integrity.structural의 FrameRequest는 노드·직선 부재·단면·등방성 재료·
고정/스프링·지점 이동·집중/선형 분포하중·사용자 조합을 엄격하게 선언한다.
solve_frame(request, context)는 선택 설치한 PyNite 3.2.0을 호출해 조합별 전역
노드 결과·로컬 부재 결과·독립 외력/모멘트 수지와 적용 범위를 기록한다.
미검토/미상 집중하중은 needs_data, 불안정·수지/소변위 이탈은 out_of_scope다.
frame_signal_frame(receipt, combination_id, context)는 조합별 별도 simulation
스트림을 반환한다. FrameSignalLoad와 solve_frame_signal_snapshot은 원 신호의
힘/모멘트 차원·부호·시계를 유지해 한 하중 슬롯을 대입하며 invalid는 None으로
보류한다. 상세 입력·부호·이용 한계·실행 예제는 STRUCTURAL_KO를 따른다.


## 열/압력 프레임 하중의 준비와 조합

FrameRequest.initial_strains는 uniform FrameInitialStrain을 받는다. 원 axial_n과
실제 elastic_axial_force_tension_positive_n·보정 end actions를 구분한다.
node별 복원 힘/모멘트 수지와 평균 축변형률 한계를 추가 검사한다.

frame_loading의 UniformFrameTemperature·PressureFrameMapping으로
prepare_uniform_thermal_frame/prepare_pressure_frame을 호출하면 해시가 있는
FrameLoadContribution을 얻는다. ready는 하중 준비 상태다. solve_loaded_frame은
같은 원본 frame/context에서 준비한 고유 효과를 조합하고 모든 원 소유 결과를
보존한다. 시각·입력 변경, 중복 효과, 미검토/범위 이탈·미상 정의를 검사한다.
상세 계약·실행 예제와 total 압력/Poisson·열 부재력 복원은 FRAME_LOADING_KO다.


## 생애주기 비용/CO2e와 원 결과의 평가 연결

lifecycle의 MoneyBasis·Money·CostEvent·RecurringCost·EmissionEvent·
LifecycleAlternative·LifecycleStudy로 입력하고 evaluate_lifecycle(study, context)를
호출한다. 기존 economics.cashflow version2의 explicit times_s를 사용해 대안별
현재비용을 구하고, CO2e는 할인 없이 별도로 비교한다. 미상/미검토·서로 다른
서비스/가격/기간은 판정 또는 비교에서 보류한다. 상세 예제는 LIFECYCLE_KO다.

lifecycle_exchange의 integrate_power_frames는 covered point-sample 구간의
signed import/export 에너지를 구한다. value_power_energy·value_schedule·
value_expected_loss는 원 해시·의미·명시한 재무 매핑을 CostEvent/EmissionEvent와
ValuationSource로 보존한다. source/event/status·valuation context 변경을
검사하고 같은 위험 빈도/손실 성분의 겹치는 기간을 거부한다. 실제 시장/작업/
수명이나 손실 확률은 자동 추정하지 않는다.

## 공동 불확실성 API

`uncertainty.EnsembleRequest`에 단위·measurement/input/model 층·근거·주변분포,
명시한 scenario/epistemic/aleatory 의미·의존성·표본 수·seed를 전달한다.
`generate_ensemble(request, context)`의 봉인된 설계를
`evaluate_ensemble(design, nodes)`에 전달한다. 각 `EnsembleNode`는 구현 해시와
설정·출력 계약 및 Python callable을 갖고 동일한 draw context를 받는다.
`registry_node`는 기존 등록 계산의 최상위 스칼라를 정확한 단위로 연결한다.
미계산 비중과 유효 비중 조건부 통계를 분리하고 선언 지지 범위·임계값의
경계를 제공한다. 표본 ID는 시간이 아니며 HEP 분포 평균을 관측 오류율로
바꾸지 않는다. 전체 예제·API·해석·오류 정책은 UNCERTAINTY_KO를 따른다.

## 관측 기반 공동 분포 적합 API

uncertainty_fit의 JointObservations/DistributionFitRequest로 원 그룹·조건·단위와
명시한 학습/평가 ID를 선언한다. fit_joint_distribution은 학습 전용 모수/상관을
반환한다. DistributionUse와 distribution_design이 기존 ensemble design을 만들고
assess_joint_distribution이 고정한 holdout의 점수·포함률·보류 비율·선언 gate를
반환한다. 척도는 기존 inverse.ResidualScale을 재사용한다. 이름/키/검토 기본값·
유한 거리 예산·분할 및 Python 예제는 UNCERTAINTY_FIT_KO를 따른다.

## 전기화학·전기방식 API

ElectrochemicalRequest의 method·원 parameters·자산/위치/시계/조건·재료/환경·
전류 의미·방법별 검토를 evaluate_electrochemical에 전달한다. 기존 MaterialJob의
스키마/버전/단위/근거를 고정해 원 엔진을 호출한다. SurfaceCurrentReduction과
reduce_surface_current는 원 신호의 signed 밀도/면적/전하·gap을 보존한다.
load_galvanized_tafel_summary는 원 공급자 checksum과 조건/단위/불특정±를 유지한다.
Python 예제·검토 키·금지되는 전류 변환·범위는 ELECTROCHEMICAL_KO를 따른다.

## 공통 기간의 위험 API

period_risk의 RiskPeriod는 정확한 시간창·시계와 공통 조건 설명을 정의한다.
PeriodEventBinding은 원 RiskProbability와 원 시간창·물리 endpoint·목표 context/
period digest·검토 근거를 받는다. PeriodLogicRequest의 기존 FaultNode/JointState를
evaluate_period_logic에 전달하면 원 HRA의 Boolean 계산을 재사용한다.

bind_creep_period_event는 원 조건부 수명 기록과 MaterialReceipt를 유지한다.
시간창·공통 생존 조건·의존성·계보가 미해결이면 원 엔진 import 전에 보류한다.
출력은 research mission_probability다. period AND가 동시 불능이나 누설을 뜻하지
않으며 demand/annualize 계약을 바꾸지 않는다. 상세 필드·예제·자원 예산과
공개 시편 기반 가정 시나리오는 PERIOD_RISK_KO를 따른다.

## 크리프·검열 수명 API

creep_exchange의 CreepRequest/evaluate_creep는 원7모델의 parameters와 방법별
검토, CreepDomain의 재료·온도·응력·시간 지원을 받는다. CreepSequence와
evaluate_creep_sequence는 빠진 노출 구간·기준 수명을 보존하며 기존 시간비
누적 모델을 호출한다. CreepBlock의 모델 기준 수명에는 명시적 분위수가 필요하다.

수명 적합 결과는 모델 해시와 선택한 전체 fit_record로 조건부 예측에 연결한다.
CreepRiskMapping/creep_risk_probability는 원 MaterialReceipt·새 재료 나이와
정확한 기간을 확인해 연구 mission_probability로 바인딩한다. 요구 시 확률·연간
빈도·봉쇄 상실은 추정하지 않는다. load_cmsx6_specimens는 공급자 checksum을
확인한 뒤 원 판독기를 재사용한다. 필드/예제/실행 명령은 CREEP_KO를 따른다.

## 압력 시험·기체 이력 API

pressure_tests의 PressureTestRequest/evaluate_pressure_test는 원4모델의 parameters와
방법별 검토를 strict MaterialJob으로 연결한다. GasDecaySpec와
 evaluate_gas_decay_series는 SignalFrame 전체를 검사하고 압력 기준·대기압·체적·
대표 기체 온도·고립 강체/조성·기준 P/T/Z를 명시한다. 모든 구간과 전체 끝점을
원 모델로 계산해 몰수 수지를 비교하고 음의 순 유출을 보존한다.

CoolPropMolarProvider는 기존 thermo 상태 검사와 Dmolar를 사용해 원 R 기준의 Z를
구한다. MolarGasState 공급자는 provider=로 교체한다. load_pe100_pressure_decay는
원 checksum/헤더/순서/게이지압·미상 온도 역할과 조성/체적을 보존한다. 상세
필드·조건·예제·원 10이력의 보류와 가정 시나리오는 PRESSURE_TESTS_KO를 따른다.

## 용접 지표·전기/열 기록 API

welding_exchange의 WeldingRequest/evaluate_welding은 원8모델의 엄격한
MaterialJob·버전·단위·receipt를 재사용한다. WeldWaveformSpec/evaluate_weld_waveform은
동일 표본의 U×I를 기존 signed power integrator로 연결하고, 거리/효율 미상과
음의 전기 교환은 원 비음수 입열 모델로 바꾸지 않는다. WeldThermalSpec/
evaluate_weld_thermal은 센서·시계·공백·명시한 band/냉각 가지를 보존한다.

welding_data의 read_tandem_recording은 검토된 ZIP/HDF를 readonly 배열·근거로
반환한다. recording.frames의 명시한 선택창/common_origin_utc/sample 예산과
selection 기록을 함께 보존한다. ebw_data.load_ebw_thermal과
select_ebw_cooling_branch는 공급자 ZIP 또는 단일 pinned asset을 담은 bounded wrapper를
받아 원행·센서 위치·유일한 무가공 postpeak bracket을 유지한다. `verify_ebw_thermal_source`
는 봉인된 EBW source record를 원 archive에서 재판독해 전체 equality replay를 확인한다.
wrapper checksum만으로
publisher asset을 인정하지 않고 내부 pinned SHA-256을 검증한다.
서로 다른 실험을 같은 용접부로 묶지 않는다. 상세 필드·파라미터·재현·경계는
WELDING_KO를 따른다. 원본/변환 sample 행을 코드 배포에 넣지 않는다.

## 경보·반응 사건과 물리 신뢰도

`alarm_exchange.AlarmReplaySpec`/`replay_alarm`은 원 AlarmPolicy를 호출한다.
원 스코어 단위·결측·지속/회복·래치·reset과 관측 activation 의미를 유지한다.
공개 JSON에 원 행을 넣지 않으므로 local source binding 검증에는 원 frame의
재실행 또는 snapshot이 필요하다. ALARM_SOURCE_KO와 RESPONSE_TRACE_KO를 따른다.

`response_trace.ResponseTraceSpec`/`trace_response`는 source-bound alarm,
실제 ValveMotionResult, 선택 HumanResponseResult와 process_frames를 받는다.
선택 `HumanResponseScenarioResult`에는 source-bound `HumanResponseClient` 재생이 필요하다. `verify_response_trace_record`는 원 process frame을 다시 받아 trace 변환을 재계산한다. SampledEndpointRule은 명시한 단위/비교/hold/간격의 표본 조건이다. 기록 경보,
단서, 명령, 목표 bracket, 공정 관측을 따로 반환한다. 반환 status는 경계
검사 상태이며 공정 성공 여부는 process_endpoint.state에 있다. command 기한과
process 한계를 혼용하지 않고 dynamic_risk_probability는 현재 null이다.

`reliability_exchange.PhysicalReliabilityRequest`/`evaluate_physical_reliability`는
원 gamma_poisson/weibull_interval 두 모델을 MaterialJob/receipt로 재사용한다.
`physical_frequency`의 PhysicalFrequencyMapping은 원 digest pin·초/년 기준과
물리 사건/조건/범위 검토 뒤 평균 강도만 연결한다. `physical_period_probability`의
WeibullPeriodMapping은 생존 나이·원점·같은 기간을 보존해 equipment의
mission_probability로 연결한다. 요구당 HEP·발생확률·평균빈도를 구분한다.
모든 입력/검토 키와 실행 예제는 PHYSICAL_RELIABILITY_KO를 따른다.

## 제작·압흔·인장 및 변형률 API

`fabrication_exchange.FabricationRequest`/`evaluate_fabrication`은 굽힘 변형률,
토크 기반 볼트 preload와 제작 후 벽 예산3모델을 원 MaterialJob으로 호출한다.
SI_INPUT_UNITS·REVIEWS의 단위/검토와 각 파라미터 근거를 받는다. 요구 기준은
FabricationRequirement로 명시하며 계산 status와 requirement_outcome을 나눈다.
실측 제작 후 벽에 제조/성형 손실을 다시 빼지 않는다. FABRICATION_KO를 따른다.

`hardness_exchange.HardnessRequest`/`evaluate_hardness`는 원 Brinell/Vickers의
N·m 기하 입력과 HardnessConditions를 받는다. Pa와 conventional HV/HBW 표현을
원 그대로 보존한다. `verify_hardness_record`는 원 receipt·맥락·요청을 확인한다.
표면·재료 상태·계측 조건이 없으면 미상으로 남긴다. HARDNESS_KO를 따른다.

`characterization.CharacterizationRequest`/`evaluate_characterization`은 원
tensile_point·ductility_retention·offset_yield·ramberg_osgood4모델을 호출한다.
시편/재료 상태·위치/시계·정확한 SI/분율/bool 단위·검토 근거를 받는다. None과
비유한 잘못된 값, uniform 선언 False와 True를 구분한다. offset branch를
정렬하거나 반복행을 삭제하지 않고 cyclic 모수를 단조 인장 자료에서 만들지 않는다.
CHARACTERIZATION_KO의 전체 입력과 원 schema pin 설명을 따른다.

`source_archive.pinned_source_zip`는 소형 ZIP을 bounded memory snapshot으로 읽는다.
대형 파일은 `pinned_source_zip_stream`이 공급자 hash를 같은 file-backed handle에서
검증하고 명시한 archive/central-directory/member/합계/member-count 예산으로 central directory를 검사한다.
`deferred_capacity` 자료는 호출자가 `allow_deferred_capacity=True`를 명시해야 한다.
ZIP을 풀거나 모든 member CRC를 검사하지 않으며 archive-wide CRC 검증 완료를 주장하지 않고, CRC는 실제로 읽은 member에 대해서만
ZipFile이 확인한다. source identity/bytes는 사용 전후 재검증한다. 로컬 이전 취득 digest를
공급자 검증으로 표시하지 않는다. `copper_tensile.load_copper_tensile`은 원20LIS·5열·시편·메타데이터·
행 번호·부호·결측과 SI 열을 제공한다. 원 machine travel과 reported strain은
별도다. 구체적 자료 적용은 COPPER_TENSILE_KO/COPPER_HARDNESS_KO/FASTENER_DATA_KO를 따른다.

## 검사 통계·TOFD·두께 격자 API

`inspection_statistics.InspectionStatisticsRequest`와
`evaluate_inspection_statistics(request, context, *, client=None, pinned_job=None,
source_root=None, expected_source_sha256=None)`는 원
`inspection.binomial_evidence`, `inspection.hit_miss_pod`,
`inspection.tofd_depth`, `geometry.thickness_grid` 네 모델을 호출한다.
`InspectionTrial`은 원 관측 ID·시편·시계·결함 정답·검출 여부·크기와 선택
cluster/획득 시각을 보존한다. `TOFDReference`는 같은 불변 frame의 lateral/
diffraction pick, 구분된 송수신 probe/wedge와 간격·음속·벽두께 근거를 담는다.
`ThicknessGridLayout`은 원 행/열 ID·좌표계·축 방향과 첫 셀 중심을 담는다.

기본 검토는 False이며 true review마다 선택한 context 근거가 있어야 한다.
미상 정답을 miss로, blank를 알려진 결함으로 바꾸지 않는다. 반복 시편/공유
cluster는 별도 의존 모델이 필요하다. 격자 미상 셀과 음의 순손실, 원 POD의
분리·비유계 상태를 유지한다. `check_inspection_statistics_record(record)`는
전체 digest·중첩 MaterialReceipt와 값/단위/확률 의미의 projection을 확인한다.
POD는 결함 크기와 절차가 주어진 검출확률이며 기간 고장확률이나 HEP가 아니다.
전체 필드·단위·방법별 review 키·예산·원 solver와 경계는
[검사 통계 가이드](INSPECTION_STATISTICS_KO.md)를 따른다.

## 반복 진폭·경로 일·변형률 수명 API

`cyclic_fatigue.CyclicFatigueRequest`와
`evaluate_cyclic_fatigue(request, context, *, client=None, pinned_job=None,
owner_source_root=None)`는 원 `fatigue.goodman`, `fatigue.hysteresis_path`,
`fatigue.strain_life` 세 모델을 호출한다. 요청은 시편·재료 조건·모집단·
교정 지원·하중 이력, engineering/true 응력과 engineering/logarithmic 변형률,
cyclic/monotonic 구분, 인장 양수·half-range 진폭·한 cycle의 두 reversal
정의와 원 파라미터·단위·검토 근거를 보존한다.

경로 요청의 `path_sample_ids`는 원 배열과 정렬하고 선택 `sample_time_s`와
`observation_period_s`는 같은 시계의 경과 초다. 반복 시각과 원 순서를
유지하며 시간축이 없으면 새 주파수를 만들지 않는다. 명시하지 않은 cycle
경계·선형 폐합으로 닫힌 일을 만들지 않고, 원 signed work와 미계산 `None`을
유지한다. `cycles_to_failure`와 `reversals_to_failure`는 서로 다른 원 출력이며
기간 잔류 수명이나 누적 손상으로 자동 변환하지 않는다.
[반복 하중 가이드](CYCLIC_FATIGUE_KO.md)에 모든 입력·단위·review 키,
원 NumPy 경로 처리와 SciPy 근찾기, 사용자 파라미터 교체 예제가 있다.

두 API는 값·검토가 미확정이면 원 엔진 import 전에 `needs_data`를 반환한다.
Python 원값에 `json_value`를 적용한 뒤 재검증하므로 NaN/Inf를 미상 null로
변경하지 않는다. 계산에는 기존 MaterialJob/Receipt의 live schema·버전·입력·
단위·context·확률 의미 검사를 사용하며 소유 통계·재료식은 복제하지 않는다.

## 선택적 원 소스 고정과 부식·마모 경계

`material_source.material_source_fingerprint(source_root)`는 `src` 아래 정렬된
Python 상대 파일명과 원 바이트의 tree SHA-256 및 파일 수를 반환한다.
`material_source_guard(source_root=None, expected_source_sha256=None, client=None)`는
두 pin 인수를 함께 받는 context manager다. 실제 로드된 원 package/evaluator와
공급된 registry의 소속을 확인하고 예외 종료에서도 전후 파일 불변을 검사한다.
두 인수가 없으면 원 엔진을 import하지 않으며 `None`을 yield한다. pin이 있으면
yield한 dict의 `after`와 `source_unchanged`가 `with` 종료 때 완성되므로 이후에
직렬화한다. 공개 기록에는 개인 소스 경로를 넣지 않는다.

`verify_material_source_pin(report, expected_source_sha256, *, executed=True)`는
원 모듈을 import하거나 다시 실행하지 않고 저장한 pin 보고서의 일치를 확인한다.
실행한 pinned 요청에는 같은 `before`/`after` tree SHA·양의 정수 파일 수와
`source_unchanged=True`가 필요하다. 미실행 요청은 `executed=False`로 검사하며
pin 보고서를 주장할 수 없다. pin 없는 요청도 실행 소스 고정을 주장하지 않는다.
이 검사는 기록의 내부 일치 검사이며 전자서명·작성자 인증·독립 실행 증명이 아니다.

검사 API의 pin 인수는 직접 전달한다. 피로 API는 요청의
`owner_source_tree_sha256`와 평가 인수 `owner_source_root`로 같은 guard를 쓴다.
현재 고정 실행에 사용한 private snapshot과 MIT 보존·배포 제외 조건은
[연동 문서](INTEGRATION_KO.md)의 원 소스 고정 절차를 따른다.

`corrosion_wear`에는 `CorrosionWearRequest`, `LossDomain`,
`evaluate_corrosion_wear`, `verify_corrosion_wear_record`를 제공한다.
원 power-law·Archard·면적 최대 Gumbel 세 모델의 버전 `1.0.0`에서 원 응답
12개·작성 조건의 독립 스칼라 기준 11개·거부 경계 5개를 확인했다.
평균 침식·최대 깊이·마모 부피,
물리 노출 원점·동일 모집단·모수 지원·면적 전이의 의미를 유지하는 경계다.
[부식·마모 API 가이드](CORROSION_WEAR_KO.md)와
[실행 결과](../research/corrosion_wear_results.json)를 따른다. 스칼라 기준은
Gumbel 적합·재표집이나 물리 꼬리의 독립 검증을 뜻하지 않는다.
[공개 부식·마모 자료 검토](CORROSION_SOURCE_KO.md)의 열네 원조건 요청은
필수 조건 부족으로 모두 보류했고 원 수치 모델을 호출하지 않았다.

## 재료 조성·충격 천이 API

`material_quality.MaterialQualityRequest`와
`evaluate_material_quality(request, context, *, client=None, pinned_job=None,
owner_source_root=None)`는 `materials.chemistry_reconciliation`,
`materials.pitting_composition_index`, `materials.impact_transition`을 호출한다.
요청에는 material condition·population·physical scope·series/assay ID, 원 입력,
composition basis·정확한 단위·검토와 선택 근거를 둔다.
`verify_material_quality_record(record)`는 원 receipt의 입력·문맥·단위·값·상태와
소스 pin 보고서의 일치를 다시 검사한다. 전필드·기본값·교체 예제는
[재료 품질 가이드](MATERIAL_QUALITY_KO.md)를 따른다.

조성 정합은 0~1 질량 분율 구간과 명시 비교 정책을 사용하며 미측정·검열 구간,
미상 heat/명칭과 `False` 정책을 보존한다. `computed`와 내부의
`satisfied`/`not_satisfied`/`unknown`은 별개다. pitting은 같은 assay의 Cr·Mo·N
질량 백분율과 caller 계수를 요구하며, 지수를 실측 부식률로 해석하지 않는다.
impact는 같은 재료·형상·방향·조건의 완전·비검열 관측 ID·K·J를 받는다.
원 shelf/식별 조건의 보류와 관측 온도 밖 목표 crossing의 `None`을 유지한다.
부분 입력이나 미검토는 원 import 전 보류하고 크기 예산은 직렬화 전에 검사한다.

## 수소 원자 몰 수송 API

`hydrogen_transport.HydrogenTransportRequest`와
`evaluate_hydrogen_transport(request, context, *, client=None, pinned_job=None,
owner_source_root=None)`는 `transport.semi_infinite_diffusion`,
`transport.hydrogen_trap_equilibrium`, `transport.hydrogen_slab_kinetic`을 호출한다.
농도는 수소 원자의 mol/m³이며 H₂ 몰수·ppm·전류를 바로 넣지 않는다.
재료·격자·모집단·모수 지원, 물리 경과 원점과 원 깊이/시간/셀 ID를 연결한다.
방법별 concentration measure·binding reference·flux sign과 온도 지원은
[수소 수송 가이드](HYDROGEN_TRANSPORT_KO.md)의 명시 계약을 따른다.

반무한의 점 농도, 국부 평형의 total/lattice/trap 분배와 미분 확산계수,
유한 슬랩의 셀 평균을 구분한다. kinetic 양쪽 표면의 양의 유속은 모두 고체
안쪽이며 방출은 음수다. 독립 capture/release 이력을 binding 값으로 덮어쓰지
않는다. 입력 미상·예산·원 solver의 부분 실패를 완료 궤적으로 바꾸지 않는다.
`verify_hydrogen_transport_record(record)`는 원 결과와 pin 일치를 확인한다.
수송을 취성·수명·고장확률로 자동 변환하지 않는다.

## 축 방향 감육 용량과 기존 위험 원시 기능

`profile_capacity.ProfileCapacityRequest`와
`evaluate_profile_capacity(request, context, *, client=None, pinned_job=None,
owner_source_root=None)`는 원 `structural.profile_capacity`를 호출한다.
원 axial point ID·좌표계·기준 벽·잔여 두께와 같은 형상/재료의 기준 용량·
팽창계수 공급자 근거를 보존한다. 선택 demand는
`Pressure(reference="differential")`의 비음수 내압−외압이며 절대압이 아니다.
`verify_profile_capacity_record(record)`는 원 receipt·pin과 별도 요구 압력차 비교를
확인한다. 원 nodal-window scan을 연속 끝점 최적화나 설계 승인으로 표시하지
않는다. 전체 입력·지원 밖/영 잔여 두께의 보류는
[감육 용량 가이드](PROFILE_CAPACITY_KO.md)를 따른다.

`reliability.independent_gate`와 `reliability.exclusive_paths`는 기존
`prepare_material_job`/`evaluate_material_job`으로 확인했다. 생산 위험 계산은
기존 `HRARiskClient`·`period_risk`를 유지하며 새 게이트 API를 추가하지 않는다.
전자는 `unscoped_event_probability`, 후자는 1/s의 배타 경로 빈도다. 같은 사건의
반복 참조를 독립 복사본으로 늘리지 않으며, 공통 기간·조건·생존·기회를 검토하지
않은 원 확률을 scoped HRA/FTA 입력으로 바꾸지 않는다.
[원 위험 수치 대조](MATERIAL_RISK_REFERENCE_KO.md)에 기존 API 재현이 있다.

## 충격시험·전기화학 투과의 원조건 판독

`material_process_data.read_bam_charpy(folder)`와
`read_ahss_permeation(path)`는 새 두 자료군의 원 셀·시편/이력·출처·단위를
보존한다. `verify_material_process_data(record)`는 원조건과 변환 행의 digest를
검사한다. 실제 파일의 공급자 크기/체크섬·권리도 읽기 전에 확인한다.
BAM은 한 온도의 미파단 6시편이며 제조사 조성 선언의 시험 lot 대응이 미상이다.
AHSS는 총 검출 전류 이력이며 유효 면적·농도 경계·D/포획/초기 상태가 미상이다.
원조건의 재료 3요청·수소 6요청은 모두 `needs_data`, 원 수치 호출은 0회다.
원관측·변환 행·상세 receipt는 공개 집계에서 제외하며
[공정 자료 가이드](MATERIAL_PROCESS_DATA_KO.md)를 따른다.

## 분리 학습·보정·평가를 갖는 모니터링 API

`monitoring_exchange.evaluate_monitoring(request, splits, context, *, client=None,
owner_source_root=None)`는 `MonitoringRequest`, 순서 있는 세 `MonitoringSplit`,
`RunContext`를 받는다. `statistical`, `classical`, `forecasting`, `modern`,
`dynamic_subspace`, `neural`, `temporal_neural`의 원 registry ID를 선택하며,
실제 확인한 59개 ID와 전체 필드는
[모니터링 교환 가이드](MONITORING_EXCHANGE_KO.md)에 있다. 원 검출기·특징·적합을
복제하지 않고 PMT의 `Monitor/create_detector/SignalBatch/WindowFeatures`를 호출한다.

| 교체할 입력 | 계약 |
|---|---|
| 방법·모수 | `family`, `method`, `parameters`, `features`, `normalize`, `false_alarm_fraction`; `{}`는 선택 버전의 기본 모수, `None`은 미상 |
| 모집단·분할 | `population_id`, `split_policy`; training→calibration→evaluation의 세 역할. 같은 run의 시간순 분할 또는 서로 다른 세 물리 run/source group |
| 관측 | 각 split의 `physical_run_id`, `source_group_id`, `source_evidence_id`, `observation_ids`, `frames`, `reference_kind`; 원 ID·시계·채널 순서·단위·origin·valid를 보존 |
| 출처·검토 | `expected_owner_version`, `expected_native_spec_digest`, `owner_source_tree_sha256`, 네 `applicability_reviews`와 `review_evidence_ids`, 선택 `evidence_ids` |
| 자원 한도 | 행·채널·특징 cell·JSON 모수 node 예산; 과대 입력을 잘라 통과시키지 않음 |
| 신경망 한도 | `maximum_training_epochs`, `maximum_hidden_units`, `maximum_training_work`, `cpu_threads`; 원 학습 모수와 실제 sequence 창을 함께 검사 |

`NativeMonitoringClient(source_root)`는 현재 Python의 검토 소스를,
`PythonMonitoringClient(python_executable, source_root, ...)`는 별도 JSON worker를
사용한다. `monitoring_source_fingerprint`와 `client.describe`로 실제 source/version/spec을
확인한 다음 요청에 고정한다. 유효한 검토·핀·모수가 없으면 원 import 전
`needs_data`다. 조건·시계·단위·관측 재사용·핀 불일치는 오류로 보존한다.
`verify_monitoring_record(record)`는 중첩 receipt와 입력/출력 정렬도 검사한다.

정규화와 detector fit은 training만, quantile 보정은 calibration만 사용한다.
evaluation은 다시 fit하지 않는다. `history_policy="reset_each_split"`이며 창 초기의
미계산 행을 삭제하거나 0점으로 채우지 않는다. raw score는 방법별 척도이고 경보는
`score > threshold`다. 보정 분율이 실제 미래 오경보율·탐지 성능·HEP를 보장하지 않는다.
선택 소스/sklearn 1.9.1의 단일 채널 `lag_ridge` shape 실패와 CBLOF 군 분할
지원 실패는 원 오류로 유지한다. 성공한 작성 모수는 추천 모수가 아니다.

동적 부분공간은 `dynamic_pca`, neural은 autoencoder 계열·VAE·GAN·diffusion,
temporal_neural은 RNN/LSTM/GRU/CNN/TCN/Transformer와 그래프 계열 등 원 등록 ID를
호출한다. 신경망에는 `parameters.hidden/epochs/batch_size/seed`를 명시하고,
temporal_neural에는 `window`도 제공한다. 누락을 원 기본 학습으로 대체하지 않는다.
원 temporal 모델의 창을 이쪽 features로 다시 계산하지 않으며 identity features를 쓴다.
원 학습 직후와 보정/평가 후 tensor·전처리 상태의 SHA/shape/dtype을 비교해 추가 fit을
거부한다. `neural_training`의 seed·epoch/loss·CPU/thread·버전도 저장 검증에 포함한다.
같은 seed의 작은 CPU 재현은 다른 환경의 bit 일치나 공정 성능을 보장하지 않는다.

현재 7 family의 선택 ID는 실제 실행했고 동적 부분공간·신경망·그래프·확산을
미실행으로 남겨 둔 앞선 단계에서 범위를 확장했다. classification·incremental·
subsequence·engineering family는 이 API의 선택 범위 밖이다. 원 137사례의
156개 기준은 수치/미상29·인과/holdout88·창17·seed20·CPU 복구2로 구분하며,
이를 모두 독립 물리 정답으로 세지 않는다. 신경망 receipt 재해시 오염 10개와
기존 거부 8개, pytest64개는 [가이드](MONITORING_EXCHANGE_KO.md)의 별도 증거다.

## 검토한 지면 액체 유입의 pool API

`pool_exchange.evaluate_pool_request(PoolRequest)`는 검토한 DynamicLH2PoolX
SDK를 JSON worker에서 실행한다. `reference_config("spreading"|"fixed_area")`는
교체 가능한 작성 조건의 구조 예이며 기본 요청은 미검토다. spreading은
solid/water 표면과 radial numerics·closure를, fixed_area는 solid substrate와
면적·열 경과·초기 질량을 받는다. 전체 필드는 [pool 가이드](POOL_EXCHANGE_KO.md)를 따른다.

`PoolRequest`는 `source_root`, `config`, `context: RunContext`, `source_pin`,
`boundary_reviewed/source_reviewed/model_reviewed`, 선택 `python_executable`,
`timeout_s`, 직렬화 `input_budget_bytes/output_budget_bytes`를 갖는다.
`source_pin=digest(source_manifest(source_root))`는 선택 SDK 파일을 고정한다.
config의 boundary는 `context.metadata["pool_boundary"]`와 정확히 같아야 하며
source asset/event/port, ENU origin[m], clock offset[s], 유입 의미와 ZOH final hold를
포함한다. 유입은 **이미 표면에 도달한 액체 kg/s**다. 원점 0부터의 시각을 받으며
offset은 SignalFrame 변환에 적용한다. 유체/물성 기준은 명시
`Hydrogen`/`CP-HEOS:Hydrogen:native`, 압력은 ambient Pa_abs다.

`validate_stored_result(result, source_root, context)`는 저장 receipt의 바이트·계약
일치를 검사한다. `to_signal_frames(result, context)`는 computed 결과와 같은
context 및 의미·수지 검사를 통과한 receipt만 받으며, 누적 증발량 차분으로 구한 구간 평균 kg/s와 재고·누적 증발 kg를
내보낸다. 첫 구간 평균은 `None/valid=False`다. 원 순간/마지막 substep rate를
구간 평균으로 바꾸어 표시하지 않는다. wet/report 면적과 escaped mass·numerical
adjustment·경고를 유지한다. 질량 수지와 SDK 에너지 원장 부재도 별도로 남긴다.
이 API는 jet/deposition 또는 분산·위험 확률을 자동 계산하지 않는다.

## 공통 context를 쓰는 충전소 동특성 API

`station_exchange.evaluate(StationRequest)`는 원 hydrogen-station-sim을 별도
Python에서 호출한다. `StationRequest`는 `source_root`, `python_executable`,
`config`, `context: RunContext`, 선택 근거 `evidence`, `source_pin`,
`source_reviewed/model_reviewed/conditions_reviewed`, wall timeout과 직렬화
`input_budget_bytes/output_budget_bytes`를 받는다. 기본은 미검토 needs_data다.
`reference_config()`와 `reference_boundary(asset_id, clock_id)`는 작성 구조 예이며
실제 조건과 근거로 교체해야 한다. `examples/station_exchange.py`의 `make_case()`는
unreviewed 요청을 만든다. 원 manifest digest가 source pin이다.

`context.metadata["station_boundary"]`는 다음 여섯 필드를 정확히 갖는다.
asset/clock은 같은 RunContext에 결합하고 선택 evidence는 context의 중복 없는
EvidenceRef ID 중에서 고른다. 원점 0의 native 시계에 명시 offset만 더한다.

| boundary 필드 | 의미 |
|---|---|
| `asset_id`, `clock_id` | `context.asset_id`, `context.clock_id`와 같음 |
| `clock_offset_s` | 분석 시계에 대응하는 비음수 경과 초 |
| `event_id` | 비공백 episode 식별자 |
| `pressure_reference` | `absolute` |
| `property_basis` | `Hydrogen TABLE; pinned src/h2station/data/hydrogen_properties_v1.npz` |

입력 schema `station-network-exchange/1`은 사용자 tank/restriction network와
불연속 ZOH opening 명령을, `station-reference-episode/1`은 원 full-reference
no-fault 시나리오의 제한된 유한 구간을 받는다. 외부 envelope는
`station-exchange/2`다. 같은 schema로 단위·미상 조건을 재해석하지 않는다.
closed network의 질량/가스·벽 에너지 수지는 반환되지만 full-reference의
전체 질량/에너지 원장은 `needs_data`다. native risk snapshot을 위험확률 0으로 바꾸지 않는다.

`validate_stored_result(result, source_root=None, context=None)`는 computed/partial
기록의 출처·context·원장·공개 관측 진단을 다시 확인한다. 미실행 needs_data
기록은 원 source를 읽지 않는다. `to_signal_frames(result, context, *, source_root,
allow_partial=False)`는 검증한 receipt만 받는다. tank의 원 값과 연결의 명목
source→target 기준 signed kg/s를 내보내고, 역방향일 때 음수로 표시한다.
실제 역방향 포트는 원 receipt에 그대로 있다. native가 보고하지 않은 연결은
원 API의 0-flow 의미를 따른다. 없는 유동 엔탈피는 None/invalid다.
full-reference는 원 노출 scalar만 내보내며 전체 재고·에너지를 만들어내지 않는다.
partial prefix는 명시 `allow_partial=True`와 별도 operating-mode로 구분한다.
전체 필드·예산·의존성·property table과 제한은 [충전소 가이드](STATION_EXCHANGE_KO.md)를 따른다.

## 출처·권리·검토 상태를 갖는 지식 API

`knowledge_exchange.evaluate_knowledge(request, context, *, client=None)`와
`verify_knowledge_record(record)`는 `KnowledgeRequest`와 기존 `RunContext`를 쓴다.
`KnowledgeSource`는 판·허용 방법·권리·명시 유효기간·분야/설비·문단과 content pin,
`KnowledgePassage`는 page/hierarchy/locator/text,
`KnowledgeCandidate`는 인용 위치·수치·단위·방향과 rationale를 보존한다.
판·권리·기간·근거·source pin이 미상이면 원 import 전 보류한다.
요청 시각은 명시 timezone-aware `as_of_utc`이며 시스템 현재 시각을 추측하지 않는다.

`SagaOfflineClient(source_root=..., python_executable=..., work_root=..., timeout_s=...)`는
별도 Python의 원 SAGA 검색·인용·HAZOP 준비도 API를 호출한다. `retrieve`와
`candidate_review`를 실행하고, `threshold_proposal`/`grounded_answer`는 추론 서비스
부재를 `needs_data`로 남긴다. 검색 점수는 관련성 확률이 아니고 후보는 pending이다.
모든 결과의 authority는 advisory이며 사람 검토를 요구한다. 경보·작업·제어 적용
함수는 없다. custom `KnowledgeClient.run(payload)`도 동일 출처·시계·권한·인용·
실행 여부 계약을 반환해야 한다. 상세 조건과 실제 import 라이선스는
[지식 교환 가이드](KNOWLEDGE_EXCHANGE_KO.md)와 [외부 고지](../THIRD_PARTY_NOTICES.md)를 따른다.
외부 추론·서버·실제 안전 지식의 정확성까지 검증한 상태는 아니므로 전체 범위는 partial이다.

## 단일 매설관의 정상 열 경계 API

`buried_thermal.evaluate_buried_thermal(request, context)`와
`verify_buried_thermal_record(record)`는 `BuriedThermalRequest`,
`ThermalPipeLayer`, `RunContext`를 사용한다. ht의 원통 저항·등온 평면 형상 인자와
기존 cooling을 호출한다. `uniform_fluid_temperature`는 고정 유체 온도의 W,
`steady_plug_flow`는 검토한 일정 유량·cp의 정상 출구 온도와 열률을 계산한다.
전체 토양 시간/수분 연성은 partial이다.

관 중심 깊이·내반경·엄격히 증가하는 layer 외반경·길이, layer/soil의 k와 온도
지원, 내부 film, layer 앞/토양 접촉, 경계 K, 자산/위치/시계·근거를 명시한다.
접촉 저항 입력은 **K·m/W**이며 길이로 한 번 나눠 구간 K/W로 만든다.
유동에는 kg/s, J/(kg·K), 검토한 최대 NTU와 유체 물성 근거를 추가한다.
깊이는 관 중심 기준이고 관은 전 구간 평면 아래에 있어야 한다.
검토한 최소 길이/직경비와 물성 온도 지원을 외삽하지 않는다.

`heat_from_fluid_to_soil_W`는 유체→토양이 양수이고 열 유입은 음수다.
유동의 평균 layer/soil 온도와 입·출구 국소 경계 온도를 함께 보존하고 지원을
양끝에서 검사한다. 유량 0의 정체는 유동 모드 밖이며 미상과 구분한다.
computed 기록 재검증에는 같은 adapter/설치 ht 버전·실제 함수 source pin이 필요하다.
미계산 기록은 ht import 없이 보류 계약을 확인한다. 입력 전체·교체·정상 수지와
이 정상 함수의 미지원 범위는 [매설 열 가이드](BURIED_THERMAL_KO.md)를 따른다.
시간 축열은 아래 원통 과도 경계, 실제 지표와 cavity는 별도 단면 경계를 사용한다.

## 관벽·단열·토양 원통층의 과도 열 API

상변화의 부재 또는 제공한 열용량에 잠열이 반영됐다는 검토는
`phase_change_absent_or_represented_in_supplied_capacity`와 선택 근거로 별도 선언한다.
일반적인 물성 지원 검토만으로 이 조건을 승인하지 않는다.

`pipe_soil_transient.evaluate_pipe_soil_transient(request, context, *, client=None,
owner_source_root=None)`는 `PipeSoilTransientRequest`와 기존 `RunContext`를 받는다.
실제 계산은 원 MatIntegrity `thermal.transient_conduction_1d`가 소유한다.
기존 `MaterialClient/MaterialJob/MaterialReceipt`와 source guard를 사용하며
이쪽에 유한체적·비선형 solver를 복제하지 않는다. 검토한 원 source/model/schema
pin이 없으면 native import 전에 보류한다.

`PipeSoilMaterial`은 id·증가하는 온도 K와 k[W/(m·K)]/부피 열용량[J/(m³·K)]
곡선·근거를, `PipeSoilShell`은 id/role/material_id·반경 edges[m]·초기 **셀 평균**
K·근거를 받는다. 관벽에서 시작해 토양으로 끝나는 층과 일치하는 계면을 명시한다.
`cylinder_length_m`, 0에서 증가하는 `time_s`, `clock_offset_s`도 별도로 선언한다.
`PipeSoilBoundary`의 adiabatic/prescribed_temperature/prescribed_heat_flux/
convection_radiation과 각 활성 history를 제공한다. 시간 knot 사이 보간은 linear다.
안·밖 두 경계 모두 고체 안으로 들어오는 열이 양수다.

`perfect_contacts=True`만 실행하며 None은 미상, False는 유한 접촉 저항 미지원이다.
원 물성의 온도 지원·반복/step·work/보관 예산과 별도 에너지 감사 허용오차를
선택하고, 네 review와 근거를 새 조건에 결합한다.
`context.metadata["pipe_soil_boundary"]`는 `pipe_soil_boundary(request)`의
자산/위치/사건/시계/offset/보간/형상/부호와 정확히 같아야 한다.

`verify_pipe_soil_transient_record(record)`는 원 입력 전체·단위·초기 상태·원
receipt와 source 보고·최종 에너지 감사를 다시 확인한다. 원통 셀 체적과 부피
열용량 곡선의 독립 적분을 원 substep 누적 경계 J와 비교한다. 출력 시점의
경계 W도 반 셀 열저항·셀/표면 K·실제 면적과 선언한 경계에 부합해야 한다.
computed와 원 상대 오차·step/반복 진단의 모순, 추가 native 입력도 거부한다.
해시는 서명이나 실제 실행의 독립 증명이 아니다.

`pipe_soil_signal_frames(record, context)`는 computed·에너지 감사 통과·같은
context만 받는다. 셀 평균 K와 **출력 시점**의 안·밖 열률 W를 원 시각+offset에
내보내며, 이를 구간 평균이나 누적 J로 표시하지 않는다. 원 실패/미상은 원 상태로
보존한다. 바깥 면은 동심 원통으로, 실제 지표 평면·매설 깊이를 자동 반경으로
변환하지 않는다. 수분·접촉·유체 에너지 되먹임을 포함한 전체 연성은 partial이다.
전체 입력과 13사례/원10호출의 작성 수치·계약 기준은
[원통 과도 가이드](PIPE_SOIL_TRANSIENT_KO.md)를 따른다.

## 실제 토양 관측의 원조건 API

`soil_data.read_soil_cover_workbook(path, *, source_spec=None)`는 피복 실험의
선택 workbook 하나를, `read_borehole_temperature(folder, *, source_spec=None)`는
선택 MAT와 두 method 파일을 읽는다. 기본은 catalog의 검토한 spec이다.
공급자 크기/체크섬·권리·구조를 검사하고 원 셀/배열·시각·부호·결측을 보존한다.
openpyxl/h5py는 실제 읽기에서만 import한다. 원 method 코드는 실행하지 않는다.

`verify_soil_source_record(record)`는 raw 셀/배열에서 진단과 단위·좌표·미상/처리
상태를 재검사한다. `borehole_temperature_frames(record, context, *, depth_index,
evidence_id, clock_offset_s=0.0)`는 같은 raw SHA/권리의 EvidenceRef와
`metadata.soil_observation_binding`에 결합한 asset/clock/record/processing clock/
수직 depth index·m/offset/quantity meaning을 요구한다. °C→K 변환만 수행하고
measured origin·원 간격·긴 공백·처음 5초를 보존한다. UTC와 물리 달력 원점은 미상이다.

시추공 **수직 깊이**를 배관 radial radius로 옮기지 않는다. 피복 실험의 일
maximum/minimum/range는 순간 온도 이력이 아니며, L1~L5 깊이·온도 단위와
수분/suction 단위의 미상도 유지한다. 실제 온도 판독을 관벽 경계나 토양 물성
보정으로 자동 승격하지 않는다. [토양 자료 가이드](SOIL_DATA_KO.md)의 source
권리·처리 계보·row 진단·변환/보류 경계를 따른다.

## 실제 지표와 다각형 관 공동의 2D 토양 API

`soil_cross_section.evaluate_soil_cross_section(request, context)`는
`SoilCrossSectionRequest`, `RunContext`를 받는다. optional FiPy 4.0.3의 Mesh2D·
CellVariable·TransientTerm·DiffusionTermNoCorrection·SciPy linear solver를 호출한다.
현재는 **직교 mesh에 한정한 연구용 부분 서비스**다. 일반 비직교·곡면과 모든
복수관 모델을 구현 완료로 표시하지 않는다. caller의 허용 각도만 높여 내부의
1e-6도 비직교 상한을 해제할 수 없다.

| 계약 | 입력과 의미 |
|---|---|
| `SoilCrossSectionMesh` | 원 vertex[m], face의 두 vertex ID, cell의 face ID, `boundary_face_groups`, extrusion 길이[m], 수평 `ground_surface_y_m`, caller 검토 비직교 한계 |
| `SoilCrossSectionBoundary` | id, `role=ground|pipe|far_field`, pipe의 `component_asset_id`, `kind=temperature|heat_flux|convection|adiabatic`, 해당 값의 시간×group face history, evidence ID |
| `SoilCrossSectionRequest` | asset/location/clock/event·offset, mesh, 0에서 증가하는 time_s·maximum_step_s, `history_interpolation=linear|previous_knot_hold`, 일정 k/Cv·검토 온도 지원·초기 셀 평균 K, boundaries, geometry/property/initial 및 선택 근거·6 review, J/상대 감사 기준, input_units·expected_fipy_version |

mesh에는 실제 수평 지표와 내부의 닫힌 다각형 pipe cavity loop가 있어야 한다.
겹침·틈·교차·불완전 adjacency와 가짜 내부면의 pipe 지정은 거부한다.
원 face 순서와 각 시간 배열은 그대로 정렬한다. 다각형 공동은 원형 관벽·
단열재·유체 셀을 포함한 모델이 아니며, pipe 경계 값은 토양 표면에 주는 조건이다.
k는 W/(m·K), Cv는 J/(m³·K), 열유속은 토양 유입 양의 W/m²다.
2D cell 면적과 face 길이는 extrusion 길이로 각각 체적·열전달 면적으로 변환한다.

`boundary_metadata(request)`를 `context.metadata["soil_cross_section_boundary"]`에
정확히 결합한다. location/event/offset/extrusion, 좌표 평면 `x_y_metres`,
`declared_polygon_cavity_soil_surface` 의미와 원 근거를 유지한다.
미검토·미상은 FiPy import 전 needs_data다. native/backend 버전·소스와 기하,
각 step의 전역/국부 cell energy·온도 지원을 저장 기록에 포함한다.

`verify_soil_cross_section_record(record, request=None, context=None)`는 `valid`를
포함한 dict를 반환한다. 같은 FiPy에서 저장 cell state의 gradient/face K/열류와
implicit cell equation을 재구성하되 새 온도 이력을 전진 계산하지 않는다.
전역 수지나 바깥 해시만 맞춰 native 열류를 바꾼 기록도 거부한다.
computed 기록 재검증에는 같은 backend가 필요하고, 미실행 보류는 FiPy 없이 확인한다.

`to_signal_frames(record, context, *, allow_partial=False)`는 감사 통과한 computed
결과를 내보낸다. partial prefix는 명시 opt-in과 별도 mode가 필요하다.
채널은 `cell.<id>.temperature_K`, `boundary.<id>.backward_euler_power_into_soil_W`,
`boundary.<id>.cumulative_energy_into_soil_J`다. 초기 power는 None/invalid다.
이 W는 끝난 implicit step의 BE 적용 값이며, previous-knot hold의 오른쪽 knot에서
새 경계 직후 순간 열률과 같지 않다. 누적 J·단순 출력 시점 열률·구간 평균을
서로 바꾸지 않는다. 전체 적용 범위와 실패한 비직교 경로는
[단면 가이드](SOIL_CROSS_SECTION_KO.md)를 따른다.

## 측정 공분산과 선언 규칙

`metrology.propagate_measurement_uncertainty(request, context)`는
`MeasurementBudgetRequest`의 자산·시계·사건·시각, 입력 추정값과 SI 기반,
입력에 해시 결속한 `MeasurementLinearization`, 전체 `StandardCovariance`를 받는다.
모든 상관 성분을 보존해 `J U Jᵀ`와 출력별 표준 불확실도·부호 있는 기여를 반환한다.
미상 성분은 독립이나 0으로 치환하지 않고 `needs_data`, 부적합 행렬은
`out_of_scope`다. 모델·공분산·관측·상태 기반과 검토의 근거가 `RunContext`에 있어야 한다.

`CoverageDeclaration`의 검토한 k는 확대 불확실도에만 사용한다. 확률·자유도를
자동 추정하지 않는다. `MeasurementDecisionRule`의 한계·guard band와 비교 방법으로
선언 판정/중첩 보류를 구분한다. `verify_measurement_uncertainty(record)`는 원 요청에서
예산과 판정을 재구성하지만 외부 측정 모델의 물리적 타당성이나 교정 인증을 대신하지 않는다.
전체 계약·예제·현재 한계는 [계량 가이드](METROLOGY_KO.md)를 따른다.

## 압축성 차압 유량계와 실가스 체적

`gas_meter.evaluate_gas_meter(request, context)`는 `GasMeterRequest`와 문맥을 받아
Fluids의 원 차압계 모델 및 기존 CoolProp 공급자를 호출한다. 관/측정 단면·실제 압력 탭,
절대 압력·온도·명시한 몰 조성·원 상관식 또는 검토한 Cd/팽창 계수·적용 범위를 받는다.
명목 양의 포트와 실제 상류/하류 포트, 시계·사건·offset을 보존한다. 질량 유량과
실제 상태 체적, 같은 조성의 선언 기준 상태 체적을 별개로 반환한다. 기준 상태를
생략해도 된다는 명시 설정이 없으면 기준 압력·온도·습분 기반이 미상인 요청을 보류한다.

`verify_gas_meter_record(record, request=None, context=None)`로 native 기록의 의미를
검사하고 `to_signal_frame` 또는 `to_signal_frames`로 검증한 측정량을 전달한다.
기본 예제의 미검토 요청은 선택 물성/유량계 패키지 import 전에 `needs_data`다.
`available_gas_meter_types()`는 별도 선택 의존성을 사용하는 명시적 탐색 함수다.
원 selector의 실행 확인은 실측 정확도나 특정 규격 전체의 적합성 선언이 아니다.
세부 도메인·실제 검증·출력 이름은 [가스 계량 가이드](GAS_METER_KO.md)를 따른다.

## 명시적 P1 토양 유한요소 경계

`soil_fem.evaluate_soil_fem(request, context)`는 `SoilFEMRequest`의 실제 노드 초기값과
`SoilFEMBoundary`의 Dirichlet 노드 trace 또는 원 face 순서의 자연 경계를 받는다.
기존 `SoilCrossSectionMesh`의 cavity/face/cell 검증을 재사용하고 scikit-fem의 P1
조립·적분·선형 해법을 호출한다. P0 셀 평균을 P1 노드로 자동 투영하지 않는다.
기본 삼각 분할·질량/Robin 적분과 조립 operator·온도·국소/전역 수지 조건을 함께 검사한다.
FiPy의 직교 제한을 해제하거나 원 FiPy/Mat solver를 수정하는 경로가 아니다.

`verify_soil_fem_record(record, request=None, context=None)`는 실제 기하·operator·원
상태에서 물리를 재구성해 `valid`를 포함한 dict를 반환한다. `to_signal_frames(record,
context, allow_partial=False)`는 검증된 결과만 신호로 만든다. 노드/셀 평균, 물리적
facet 법선 열률/약형 반력, BE 구간 적용 W/누적 J를 구분한다. 공유 Dirichlet corner의
경계별 반력이 유일하지 않으면 미상으로 유지한다. 조립 진단을 통과하지 않은 결과는
일반 computed 신호로 내보내지 않는다. 명시한 부분 prefix의 사용과 전체 한계는
[유한요소 가이드](SOIL_FEM_KO.md)를 따른다. 선택 설치 extra는 `soil-fem`이다.

## 양축 오차와 전체 공분산의 교정

`calibration.fit_calibration(request, context, provider)`는 `CalibrationRequest`와
`CalibrationProvider`를 받는다. x/y 추정값·표준 공분산의 순서/단위·관측/회차/모집단,
적합·평가 역할, 모델/함수/구성 식별자와 지지 영역을 명시한다. 잠재 실제 x와 모델
모수를 함께 추정하며 시점 간·양축 간 공분산을 그대로 보존한다. 기존
`inverse.solve_bounded_least_squares`가 원 SciPy 해법을 공통 호출한다.

독립 평가를 요구하면 물리적 분할과 오차 독립성도 검사한다. 미상 공분산을 0으로
넣지 않고 exact-x는 명시한 경로만 사용한다. 일반 singular/exact-y·활성 경계·랭크
결함에서 지지하지 않는 공분산을 내보내지 않는다. `verify_calibration_record(record,
provider, request=..., context=...)`는 현재 source/native 수학을 재실행하며
`predict_calibration`은 관측된 적합 지지 밖을 거부하고 새 관측의 불확실도를 자동
0으로 가정하지 않는다. 기존 물 관망 역문제는 [기존 교정](CALIBRATION_KO.md), 새
EIV 계약은 [양축 교정](EIV_CALIBRATION_KO.md)을 따른다. 두 실행/집계 파일을 구분한다.

`calibration_data.read_emue_calibration`과 `emue_calibration_inputs`는 30점과 전체
60×60 공분산의 파생 단계·권리·원본 checksum을 보존한다. 실제 코드는 출처의
프로그램을 실행하지 않는다. [자료 가이드](CALIBRATION_DATA_KO.md)를 따른다.

## 구간 적산과 시계의 공분산

`totalization.totalize_measurements(request, context)`는 `TotalizationRequest`의
점/선형·구간 평균·누적 계수기 의미와 명목 방향을 명시해 수량을 적산한다.
`readings` 다음 `times` 순서의 전체 공분산으로 순 수량과 구간 길이의 공동
불확실도를 기존 계량 서비스에 연결한다. `boundary_metadata`로 시계/포트/기반을
문맥에 결속한다. 미상·invalid·공백, 상쇄되는 공통 시간 오차와 개별 시간 오차를
구분하며 tail이나 다른 회차 사이를 채우지 않는다.

`verify_totalization_record(record, request=..., context=...)`는 전체 구간/수량/공분산을
재구성한다. `totalization_data.vsl_counter_period_cases`는 원 meter reader를 재사용해
실제 MUT 계수기 기간을 매핑한다. [적산 가이드](TOTALIZATION_KO.md)에 시계 표현
공차·공분산 크기·구간 부호·자료 한계를 명시했다. [공통 JSON 경계](JSON_BOUNDARY_KO.md)는
NaN/무한대를 null로 바꿔 정상/미상으로 오인하는 경로를 차단한다.

## 국소 격자와 정수 공동 사망자 법칙

`spatial_risk.evaluate_spatial_risk(request, context)`는 `SpatialRiskRequest`의 국소 ENU
격자·수용체 높이·셀별 정수 인구·존재 확률·조건부 위해장·사건 강도를 원 PRISM/HRA
API에 연결한다. 같은 사람의 존재 확률을 두 번 곱하지 않는다. 고정 위치 위해율,
존재를 반영한 개인 위해율, 기대 사망자와 정수 F-N은 다른 출력이다.

`JointCasualtyDistribution`은 셀별 정수 사망자 상태와 확률·근거를 받는다. 셀별
평균이 해당 위해/존재 주변확률과 맞아야 하며 검증한 총 정수 사망자 분포를 원
HRA F-N 함수에 보낸다. 평균 사망자만으로 정수 초과빈도나 독립성을 추정하지 않는다.
원 PRISM의 기대 사망자 문턱 곡선은 별도 이름으로 남긴다. 자료가 없는 영역이나
원 계산 보류는 0으로 만들지 않는다.

`verify_spatial_risk_record`는 원 요청/시설/소스/시계/열·위해/분기 의미까지 재검사하며
`to_signal_frames(record, context, allow_partial=False)`는 부분/미상과 신호 유효성을
보존한다. 연속 CRS 변환/곡면/공개 인구 raster 투영을 자동 수행하지 않는다. 정확한
지원 범위·원 엔진 설치/핀·배포 경계는 [공간 위험 가이드](SPATIAL_RISK_KO.md)를 따른다.

## 인양 하중의 추가 서비스

`lifting.LiftingRequest`에 질량중심·중력·줄/부착점/상부 앵커의 명시 좌표와
검토된 유효 힘 한계를 넣는다. `evaluate_lifting`은 선언한 강체 자세의 유일한
장력과 공유 부품의 벡터 합력을 계산한다. 한계 미상은 명목값을 유지한
needs_data, 계산 가능한 한계 초과는 별도 `acceptance`로 남긴다. 초정정
분담·느슨한 줄·자세 변화·동역학·구조 안정성을 최소 노름 해로 대신하지 않는다.
`verify_lifting_record`는 원 요청/문맥·현재 계산을 재생하며 `to_signal_frame`은
정적 N 채널을 내보낸다. 정확한 입력과 예제는 [인양 가이드](LIFTING_KO.md)를 따른다.

## 다단 압축·상변화·자료의 추가 경계

`compressor_train`의 요청·맵·냉각 경계는 검토된 같은 유체/총상태 정의와 원
압축기의 실제 점 계산을 연결한다. `evaluate_compressor_train`과 대응 verify,
signal export를 사용한다. 정적 여유·실제 동적 서지를 구분하며 전체 API와
미상을 [압축기 가이드](COMPRESSOR_TRAIN_KO.md)에 기록했다.

`phase_change_exchange`는 구분된 case signature로 계면·정상/동적 장치·단열을
호출한다. `evaluate_phase_change`, 대응 verify, `to_signal_frames`와 장치 전용
`to_flow_boundaries`를 제공한다. 원 종료 미분·순간 열률·누적 장부를 구분한다.
[상변화 가이드](PHASE_CHANGE_KO.md)의 벌크 동결/frost·실험/에너지 공백을 유지한다.

`thermal_process_data.read_thermal_process_source`는 선택한 원본과 권리·pin을
확인하고 자료별 의미를 보존한다. `iter_refinery_observations`는 원 센서 행을
스트리밍하며 보정한 맵/HEP나 단위를 만들지 않는다. 재생 검사와 실제 적용
한계는 [자료 가이드](THERMAL_PROCESS_DATA_KO.md)를 따른다.

공통 `thermo.coolprop_reference_anchors()`는 uncached H/S 런타임 식별값이다.
`CoolPropProvider(use_cache=False)`는 같은 원 property API를 매번 실행한다.
기존 기본 cache를 그대로 두며 참조 기준을 바꾸는 통합은 명시적 uncached
경로와 기준 핀을 사용해야 한다. 참조점은 교정 데이터/운전 조건의 대체물이 아니다.



## 병렬 구현 API: 수치 검증 대기

아래 API는 새 구현이며 마지막 전체 회귀 결과에 포함되지 않는다. 수치 시험은
누리온에서 실행한다. 계산 가능 상태와 현장 자격을 구분한다.

| 모듈 | 입력과 호출 | 사용 안내 |
|---|---|---|
| 인입력·곡률 | `pulling.PullingRequest`, `evaluate_pulling(request, context)` | [인입력](PULLING_KO.md) |
| 센서 배치 | `sensor_placement.SensorPlacementRequest`, `plan_sensor_placement(request, context, backend=...)` | [센서 배치](SENSOR_PLACEMENT_KO.md) |
| 탱크 호흡·벤트 | `tank_breathing.TankBreathingRequest`, `evaluate_tank_breathing(request, context)` | [탱크 호흡](TANK_BREATHING_KO.md) |
| 압력파 자료 | `pressure_wave_data.verify_pressure_archive`, `list_pressure_members`, `read_pressure_member`, `parse_pressure_csv`, `qualify_pressure_pair`, `verify_pressure_pair_qualification`, `build_pressure_pair_trace_record`, `verify_pressure_pair_trace_record` | [압력파 자료](PRESSURE_WAVE_DATA_KO.md) |
| 누수 다중센서 자료 | `leak_multisensor_data.inventory_leak_multisensor_archive`, `accelerometer_member_labels`, `read_accelerometer_member`, `parse_accelerometer_csv`, `verify_accelerometer_csv_record`, `qualify_accelerometer_pair`, `verify_accelerometer_pair_qualification`, `build_accelerometer_pair_trace_record`, `verify_accelerometer_pair_trace_record`, `bind_accelerometer_delay_request`, `hydrophone_member_labels`, `read_hydrophone_member`, `verify_hydrophone_source_record` | [다중센서 자료](LEAK_MULTISENSOR_DATA_KO.md) |
| 유사 누수 자료 등록본 대조 | `leak_deposit_crosswalk.compare_member_ledgers`, `compare_leak_testbed_deposits` | [등록본 crosswalk](LEAK_DEPOSIT_CROSSWALK_KO.md) |
| 음압파 분석 경계 | `pressure_wave.bind_pressure_wave_pair_request`, `evaluate_pressure_wave`, `verify_pressure_wave_record` | [음압파](PRESSURE_WAVE_KO.md) |

세 계산 모듈은 각 `verify_*_record`로 명시적 재실행 검토를 제공한다.
이는 무서명 기록의 재현 비교이며 실행자의 신원을 보증하지 않는다.
압력파 CSV 파서는 열·시간 배율·압력 배율/오프셋·압력 기준·시계·근거를
호출자가 선언해야 한다. 파일명에서 단위나 동기화 품질을 추정하지 않는다.


경로 대안은 `route_planning.RoutePlanningRequest`와 `evaluate_routes(request, context)`로
호출한다. `verify_route_record`가 원 요청/문맥을 재실행한다. [경로 매뉴얼](ROUTE_PLANNING_KO.md).
공급한 유한 후보의 Pareto 비교이며 지형 전체의 최적 경로를 생성하지 않는다.

누설 후보 위치는 `inverse_localization.LocalizationRequest`, `LocalizationProvider`,
`localize_leak(request, context, provider)`를 사용한다. 기존 `inverse.Prediction`으로
원 물리 엔진의 예측을 전달한다. `verify_inverse_localization_record`는 같은 공급자를
요구한다. [위치 역추정 매뉴얼](INVERSE_LOCALIZATION_KO.md). 상대 적합도는 누설 존재
확률이나 위치 사후확률이 아니다. 두 구현 모두 수치 검증 대기다.


피그 이동은 `pig_motion.PigBoundaryWindow`, `PigMotionRequest`,
`evaluate_pig_motion(request, context)`를 사용한다. `verify_pig_motion_record`는
재실행하고 `to_signal_frames`는 검토된 문맥으로 시계열을 내보낸다.
[피그 매뉴얼](PIG_MOTION_KO.md). 공급 압력에 따른 강체 1D 운동이며
유동과의 양방향 연성·누설·변형·충격 해석은 후속 범위다. 수치 검증은 대기다.


관 마찰 원자료는 `friction_data.read_friction_observations(folder)`와
`verify_friction_observation_record(record, source_root=...)`로 읽고 재검토한다.
[원자료 안내](FRICTION_ACOUSTIC_DATA_KO.md). 원본 수두와 온도 유래 점도를 보존하며
모델 적합·압력기준·시계를 추정하지 않는다. 리더 시험도 누리온 대기다.


## 추가 연성·관측·운영 API: 검증 대기

| 경계 | 호출 | 안내 |
|---|---|---|
| 가열 수송 | `heated_transport.HeatedTransportRequest`, `evaluate_heated_transport(request, context, property_provider=...)` | [가열 수송](HEATED_TRANSPORT_KO.md) |
| 외부 센싱 | `external_sensing.ExternalSensingRequest`, `adapt_external_sensing(request, context)` | [외부 센싱](EXTERNAL_SENSING_KO.md) |
| 운영 대안 | `operating_optimization.OperatingOptimizationRequest`, `optimize_operating_plans(request, context)` | [운영 최적화](OPERATING_OPTIMIZATION_KO.md) |

가열 수송은 기존 열·수리·물성 모델을 셀별로 연결한 부분 연성이다. 원유 조성을
자동 생성하지 않는다. 외부 센싱은 비검출/포화/누락을 별도 유지하고 검토된 정확값만
기존 monitoring 및 sampled trace에 연결한다. 운영 비교는 기존 lifecycle/CP-SAT로
공급된 전체 유한 계획을 비교하며 연속 운전 최적화나 수리 상태를 새로 추정하지 않는다.
각 모듈의 verify 함수는 현재 소스와 원 의존성을 다시 실행한다. 세 API 모두 누리온 수치 검증 대기다.


음향 PCM 자료는 `audio_data.parse_pcm_wave`, `read_training_audio`,
`verify_training_audio_record`를 사용한다. 정수 코드와 원본/도구 식별자만 보존하며
물리 단위나 시계를 추정하지 않는다. [음향 자료 API](FRICTION_ACOUSTIC_DATA_KO.md).


## 세션 없이 API 탐색하기

`python scripts/export_api_inventory.py`는 수치 엔진을 import하지 않고 Python AST에서
모듈·공개 함수 시그니처·계약 클래스 필드·기본 표현식·공개 메서드와 소스 SHA를
`research/api_inventory.json`으로 내보낸다. 클래스의 상속 필드나 재수출 alias는
정적 목록에 펼치지 않는다. 실제 입력 검증과 적용 범위는 각 계약/매뉴얼이 기준이다.
이 목록은 런타임 실행·시험 통과·기능 자격의 증거가 아니며 소스를 바꾼 뒤 다시 생성한다.


지반–관 연성은 `soil_pipe.SoilPipeRequest`, `evaluate_soil_pipe(request, context)`와
`verify_soil_pipe_record`를 사용한다. 기존 frame 엔진에 검토된 양방향 nodal spring과
지반변위를 연결한다. 기존 일반 하중이나 spring을 새 하중으로 덮어쓰지 않도록
입력에서 거부한다. [지반–관 매뉴얼](SOIL_PIPE_KO.md). 비선형·인장분리·접촉은
미지원이며 수치 검증은 누리온 대기다.


슬러리 수송은 `slurry_transport.SlurryPropertySnapshot`, `SlurryTransportRequest`,
`evaluate_slurry_transport(request, context)` 및 `verify_slurry_transport_record`를
사용한다. Newtonian 경계와 균질 비뉴턴 층류의 범위를 분리한다. 항복유체 정지 상태는
유일 출구 압력을 반환하지 않는다. [슬러리 매뉴얼](SLURRY_TRANSPORT_KO.md). 불확실성 설계와 연결할 때는 `slurry_uncertainty.slurry_transport_node`를 기존 `uncertainty.generate_ensemble`/`evaluate_ensemble`과 함께 사용한다. 실행 예제는 `examples/slurry_uncertainty.py`다.

파괴 진행·정지 경계는 `fracture_arrest.FractureArrestRequest`, `ArrestTrajectory`,
`assess_fracture_arrest(request, context, trajectory=..., provider=...)`와
`verify_fracture_arrest_record`다. source_mode로 검토된 이력 또는 원 공급자 중 하나를
선택한다. 표본 비교는 동적 정지/연속 유지 증명이 아니다.
[파괴 진행·정지 매뉴얼](FRACTURE_ARREST_KO.md). 두 구현의 수치 시험도 누리온 대기다.


절차 기록의 원 입력·근거 ID 충돌·재현 검토는
`procedures.verify_procedure_result(result, procedure=..., records=..., context=...)`를
사용한다. [절차 API 설명](PROCEDURES_KO.md). 공통 도구로 29절차를 재사용하며
절차별 현장 적합성이나 승인 권한을 대신하지 않는다. 추가 시험은 누리온 대기다.


## 가스 과도·좌굴·고형화 경계: 누리온 검증 대기

| 경계 | 호출 | 안내 |
|---|---|---|
| 비등온 가스 셀 네트워크 | `gas_thermal_transient.GasThermalTransientRequest`, `evaluate_gas_thermal_transient(request, context)` | [가스 과도](GAS_THERMAL_TRANSIENT_KO.md) |
| 탄성 frame 좌굴 | `buckling.BucklingRequest`, `evaluate_buckling(request, context, provider=...)` | [좌굴](BUCKLING_KO.md) |
| 수화물·왁스 경계 비교 | `deposition_risk.DepositionRequest`, `assess_deposition(request, context, envelope=..., provider=...)` | [고형화 경계](DEPOSITION_RISK_KO.md) |

각 모듈의 verify 함수는 원 의존성/공급자를 재실행한다. 가스 셀 네트워크는 음향
운동량 PDE가 아니며, frame eigen 좌굴은 셸 붕괴·소성·결함 해석이 아니다.
수화물·왁스 경계는 kinetics·침적량·막힘 확률을 만들지 않는다.
검토된 ThermoPack hydrate source build가 있는 경우에만 `ThermopackHydrateProvider`를
사용한다. 현재 PyPI wheel에 없는 기능을 기본 의존성으로 가정하지 않는다.

`GasThermalCell.effective_fluid_volume_m3`는 원통 기하 체적을 대체하는 inventory
입력이다. 설정 시에는 별도 `fluid_volume_override_evidence_id`가 필요하며 실제
`RunContext.evidence`에 포함되어야 한다. computed 결과의 `cell_fluid_volumes`에는
기하 체적, 적용된 재고 체적, override 여부·근거와 기하 대비 비율이 기록된다.
전체 요청 작성·검토·source pin 예제와 준정상 저 Mach 한계는 위 가스 과도 매뉴얼을
따른다. 실제 수치 회귀는 누리온에서 실행한다.


## 공통 JSON 작업

18개 신규 어댑터는 `adapter_jobs.AdapterJob`, `adapter_specs`, `describe_adapter`,
`run_adapter_job`, `verify_adapter_job_record`로 연결한다.
[공통 작업 API](ADAPTER_JOBS_KO.md)의 허용 역할·payload·입력 예산·CLI를 따른다.
원 계산과 verifier를 재사용하며 callback은 Python에서 등록한 객체 별칭만 받는다.
이 서비스의 회귀도 누리온 대기이며 원 기능의 자격을 승격하지 않는다.


## 확보 자료를 적용하는 연구 API

관 마찰은 `friction_study.FrictionStudyRequest`, `evaluate_friction_study(..., source_root=...)`로
기존 수리/역추정을 호출한다. [마찰 study](FRICTION_STUDY_KO.md).
음향은 `audio_study`의 선택 clip 진단과 명시 monitoring 준비 경계를 따른다.
[음향 study](AUDIO_STUDY_KO.md). 두 수치 분석은 아직 실행하지 않았다.

현장 Excel은 `field_leak_data.read_field_leak_workbook`, `FieldLeakMapping`,
`bind_field_leak_source`, `to_field_monitoring_split`과 두 verify 함수를 사용한다.
[현장 자료](FIELD_LEAK_DATA_KO.md). 실제126관측을 읽었고, 값의 Pa변환이나
모니터링 학습은 실행하지 않았다. 각 API의 단위/시계/근거 부족은 보류 상태로 유지한다.

Zenodo 배수망 archive는 `water_network_data.inspect_water_network_archive(path)`로
공급 체크섬을 재확인하고 원래 10개 CSV의 행 폭·열별 빈칸을 순차 점검한다. 원 관측 행은
로컬 reader 반환에만 포함하며 공개 인벤토리에는 넣지 않는다.
`verify_water_network_archive_inventory(record, path=...)`는 같은 archive를 다시
읽어 인벤토리를 비교한다. `parse_water_network_event_table`은 검토한 두 이벤트 표의
원 셀과 순서를 보존하되 날짜·좌표·유량의 의미를 확정하지 않는다. pickle 9개는
실행/역직렬화하지 않으며, CSV 수두·이벤트 의미를 자동으로 정규화하지 않는다.
`read_water_network_pressure_window`는 caller가 지정한 센서·naive local-clock
구간을 원 문자열로 읽는다. `build_water_network_inverse_bridge`는 명시적으로 검토한
port와 `pressure_head` m basis, event-origin 시각을 받아 기존 위치 API 행과 source·clock·
센서 매핑 근거를 보존하는 sealed receipt를 만든다.
`verify_water_network_pressure_window`는 선택 구간을 원 archive에서 재생한다.
`read_epanet_coordinate_record`는 지정 run과 node ID의 INP 좌표 문자열을 고정하고,
`verify_epanet_coordinate_record`가 source를 replay한다. `to_water_network_location_candidates`는
caller affine transform 및 각 후보의 port/identity/parameter mapping을 받아 기존
`inverse_localization.LocationCandidate`들을 감싼 무결성 레코드를 만든다. 좌표 frame·단위·
변환은 공급 자료에서 자동 추론하지 않는다. 이후 `candidate_set.verify_digest()`로
변환 레코드를 확인하고 `candidate_set.candidates`를 기존 위치 요청에 연결한다. inverse
context에는 archive/member hash, transform 검토 근거, 후보와 센서 port 매핑, 각 measurement
basis 및 request review의 EvidenceRef를 제공한다. `bind_water_network_localization_context`
는 request rows/candidates/source hash와 두 adapter receipt의 일치, 동일 archive SHA,
필수 근거 ID를 확인한 뒤 정확한 `inverse_localization_boundary`와 receipt digest가 담긴
`RunContext`를 만든다. `dataset_evidence_id`로 지정한 EvidenceRef에는 archive SHA-256과
CC-BY-4.0을 기록하고, `origin`은 압력 자료가 실측인지 시뮬레이션인지 검토해 명시한다.
세부 절차는 [자료 안내](WATER_NETWORK_DATA_KO.md)를 따른다.
[자료 안내](DATA_KO.md),
[상세 데이터 연결](WATER_NETWORK_DATA_KO.md).


현장 monitoring은 `FieldMonitoringSelection`, `field_monitoring_boundary`,
`evaluate_field_monitoring`으로 세 실제 원행 선택을 기존 PMT gateway에 연결한다.
`verify_field_monitoring_record`는 source 재읽기와 원 semantic verifier를 사용하며
refit/독립현장 검증과 구분한다. [현장 source/monitoring](FIELD_LEAK_DATA_KO.md).


## 실제 시계 없는 운전점 역추정

`inverse.OperatingPoint`, `OperatingPointObservations`, `OperatingPointPrediction`,
`OperatingPointFitRequest`와 `fit_operating_points`는 원 point/state/source/group을
보존하는 축이다. 기존 `fit_parameters`/물리time API와 공통 수치커널을 쓰며
행 번호를 seconds로 만들지 않는다. [운전점 inverse](OPERATING_POINT_INVERSE_KO.md).
마찰 연구의 `operating_point_calibration` 및 `build_friction_operating_points`가 이
경계를 사용한다. 잔차척도·독립 반복/공분산을 자동 만들지 않고 추가 회귀는 미실행이다.


밀도 없는 수두 계산은 `models.hydraulics.LiquidHeadLoss`와 `liquid_head_loss`를
사용하며 기본 registry의 `hydraulics.liquid_head_loss`에서도 호출할 수 있다. 기존
pressure API와 공통 기구학/Fluids 경로를 공유하고 generic uncertainty registry node에
연결된다. Pa·질량·동력은 가짜 밀도로 만들지 않는다. [수두 API](LIQUID_HEAD_KO.md).
마찰 study도 실제ρ가 없으면 head 경로를 사용하고, 실제ρ가 있으면 기존 pressure/work와 대조한다.

기본 registry의 source fingerprint v3는 함수 텍스트·모듈 bytes와 `partial` 인수,
함수 기본값·closure·참조된 전역 바인딩, bound method의 소유 인스턴스 상태 및 callable 인스턴스 상태를 가능한 범위에서 함께 지문 처리한다.
runtime에는 callable/module/binding SHA와 추적 완전성(scope)이 기록된다. 비직렬화 상태는
숨기지 않고 binding scope를 partial로 표시한다. 실행 전후 지문이 바뀌면 결과를 거부한다.
이 지문은 loaded bytecode/모든 외부 dependency/모든 module-wide 가변값의 증명이나 실행 인증은
아니며, 고정 소스의 새 프로세스에서 실행하는 누리온 계획과 원 adapter의 구체적인
source/runtime 경계를 함께 사용한다.

운전점 역추정에는 `OperatingPointFitRequest.residual_metric`으로 선택적
`OperatingPointResidualMetric`을 공급할 수 있다. `ResidualRow`의 명시 ID·단위·순서와
원 행렬·출처·검토 근거를 보존하며, 기존 시간 API와 기본 scalar 경로를 유지한다.
공동 행렬은 linear loss에만 적용하고, scalar 척도를 중복 적용하지 않는다.
관 마찰 study도 같은 owner를 사용한다. 상세와 미검증 경계는
[운전점 역추정](OPERATING_POINT_INVERSE_KO.md)과 [관 마찰 연구](FRICTION_STUDY_KO.md)를 따른다.


관측 사건 타임라인은 `risk_timeline.RiskTimelineRequest`와
`build_observed_risk_timeline`을 사용한다. 기존 `response_trace.trace_response` 영수증을
해시, 자산·시나리오·공통 시계·원점, 근거, 경보→단서→명령→공정 endpoint 순으로
연결한다. 매핑 검토와 원 context에 존재하는 근거가 필요하며 unresolved trace, 중복
기회/명령, 창 밖/역순 사건은 거부한다. endpoint는 달성 표본 또는 완료 관측창에서의
미달성 상태를 함께 보존한다. 결과는 관측·시뮬레이션 타임라인 자료이며
동적 위험확률·사건 의존성·시간 의존 고장/복구를 추정하지 않는다.


계측 교정의 적용 이력은 `calibration_application.CalibrationApplicationRequest`,
`apply_verified_calibration`, `verify_calibration_application`으로 기록한다. 기존 교정 엔진의
영수증 검증과 forward prediction을 호출하고 source/application/provider/config digest를
묶는다. 입력 공분산은 digest로만 보존하고 출력 불확실도에 전파하지 않는다. 절차는
[교정 적용 기록](CALIBRATION_APPLICATION_KO.md)을 따른다.


교정한 계측값을 기존 `MonitoringSplit`에 전달하려면
`calibrated_monitoring.build_calibrated_monitoring_input`을 사용한다. calibration application의
유효 입력값 전체와 시간순 관측 행을 정확히 맞춰 적용하고 원 observation ID·invalid/null·clock을
보존한다. 시계 변환·결측 보간·불확실도 전파는 하지 않는다.
[교정 모니터링 연결](CALIBRATED_MONITORING_KO.md)을 따른다.


Mendeley potash elbow 실험 원본은 `potash_elbow_cp.read_potash_elbow_cp(path)`로 읽는다.
공급 파일 SHA-256을 확인하고 표 분할, 시험 반복값, 보고 평균/95% CI 및 원 행 위치를
보존한다. `mA/cm2` source 값과 명시적인 `A/m2` 환산을 함께 제공하며 보고 통계는
재계산하지 않는다. CC BY 4.0 attribution와 자료 범위는
[POTASH_ELBOW_CP_DATA_KO](POTASH_ELBOW_CP_DATA_KO.md)를 따른다.

별도 Mendeley V2 정체 배관 자료는 `potash_pipe_cp.read_potash_pipe_cp(path)`로 읽는다. 공급 파일 SHA-256, OOXML 표·20개 시점·5분 시간격자를 확인한다. 세 반복, 공급자 보고 평균과 95% 구간은 별도 유지하고 보고 통계를 다시 계산하지 않는다. 원/환산 단위를 함께 내며 현장 자격을 주장하지 않는다. 자료 안내는 [POTASH_PIPE_CP_DATA_KO](POTASH_PIPE_CP_DATA_KO.md)를 따른다.
`to_signal_frames(dataset, clock_id=..., stream_id=..., statistic="reported_mean")`는 검증된 결과에서 보고 평균 시계열을 `SignalFrame`으로 만들고 `(frames, EvidenceRef)`를 돌려준다. `statistic="replicate", replicate_index=0..2`로 한 반복을 선택할 수 있다. 반환 근거를 `RunContext.evidence`에 포함해야 하며, 프레임 origin은 `laboratory`다. 면적·bare fraction·보호 전위 기준을 만들지 않는다.

검사 특징점 프로파일 연결에는 `inspection_profile_bridge.bind_inspection_profile`과
`evaluate_inspection_profile_capacity`를 사용한다. 원 검사 SHA·시계·점 ID와 보수적 잔여두께를
확인한 다음 기존 `structural.profile_capacity` owner API로 위임한다. 전체 coverage는 추론하지 않으며
세부 게이트와 사용 한계는 `docs/INSPECTION_PROFILE_BRIDGE_KO.md`에 기록했다.


## 교정 계측과 액체 밸브 경계

`calibrated_valve_boundary.bind_calibrated_valve_boundary`는 기존 calibration application 결과에서
유효 CdA(`m2`) 관측 이력을 만들고 기존 `transient.LiquidValve`로 전달한다. 계측 보정과
액체 과도 해석기를 복제하지 않는다. 원 센서 channel/단위, source frame 순서·시계·origin,
교정 receipt, 밸브 crosswalk 및 network context가 모두 일치해야 한다. 결측·invalid 구간은
보간하지 않고 거부하며 불확실도 전파·clock correction·과도 solver 실행·현장 자격은 제공하지
않는다. 상세 계약과 예제는 [교정 밸브 경계](CALIBRATED_VALVE_BOUNDARY_KO.md)를 따른다.


검사 특징점 프로파일 연결에는 `inspection_profile_bridge.bind_inspection_profile`과
`evaluate_inspection_profile_capacity`를 사용한다. 원 검사 SHA·시계·점 ID와 보수적 잔여두께를
확인한 다음 기존 `structural.profile_capacity` API로 위임한다. 전체 coverage는 추론하지 않으며
세부 게이트는 `docs/INSPECTION_PROFILE_BRIDGE_KO.md`에 기록했다.

체결 원 곡선은 `fastener_data.read_fastener_trace(path, kind=None)`로 한 파일씩 읽는다.
카탈로그 승인 fastener 자산과 공급자 SHA-256을 확인하고 출처·trial·행·단위·각도 좌표를
보존한다. 반환값은 원 관측 셀을 포함하므로 비공개 분석 산출물로만 취급한다. 제작/preload
solver는 복제하지 않으며 힘·면적·nut factor 공백을 자동으로 채우지 않는다. 자세한 계약은
[체결 자료](FASTENER_DATA_KO.md)를 따른다.

공통원인 count를 기존 FTA로 넘길 때는 `common_cause.CommonCauseBinding`과
`fault_tree_request_from_common_cause_counts`를 사용한다. 원인 분류 근거, 영향 사건의
dependency group, 동일 모집단의 검증된 공동 상태표가 필요하다. 주변확률에서 의존성이나
공통원인 계수를 추정하지 않는다. FTA leaf가 원인 및 각 영향 사건을 실제 소비하는지 검사하고,
조건/기회/모집단/grouping 단위와 table digest를 영수증에 고정한다. 영수증 재생은
`verify_common_cause_binding_receipt`로 확인한다.
세부 계약은 [공통원인 연결](COMMON_CAUSE_KO.md)을 따른다.


## GasLib 정상 관망 연결

`gaslib_network.prepare_gaslib_network`는 원 `.net/.scn` 및 ZIP의 digest를 다시 확인하고 명시적 caller state/evidence로 기존 `GasNetwork` 입력을 만든다. `solve_gaslib_network`는 기존 `solve_gas_network`로 위임하며 `verify_gaslib_network_run`은 source/member/owner result가 든 receipt를 확인한다. 경계 절대압·온도, 조성/EOS, 열·손실·withdrawal과 각 근거를 호출자가 제공한다. nomination 값을 유량으로 변환하지 않으며 비영 고도차와 non-pipe active devices를 거부한다. context와 선택 시나리오가 일치해야 한다. 상세 계약 및 제한은 [GasLib adapter 문서](GASLIB_NETWORK_ADAPTER_KO.md)를 참조한다.


공정 제어실 human-factors workbook은 `pipeline_integrity.human_control_room_data.read_human_control_room_archive`로 source-pinned table API를 제공한다. 반환 객체에는 participant-level 행이 있으므로 공개 요약에는 `summarize_human_control_room_archive`를 사용한다. `verify_human_control_room_archive`가 pinned archive replay를 확인하며 단위·시간·task outcome·HEP를 추정하지 않는다. 표본 구성과 개인정보 경계는 [자료 안내](HUMAN_CONTROL_ROOM_DATA_KO.md)를 따른다.

실험실 water-distribution 누수·센서 고장·사이버 이벤트 workbook archive는 `pipeline_integrity.wds_event_data.read_wds_event_archive`로 source-pinned raw cell API를 제공한다. `build_wds_signal_mapping`/`build_wds_signal_batch`는 caller 검토 채널 단위와 명시 시각축을 기존 `SignalFrame`으로 변환하고 `verify_wds_signal_batch`가 원행·변환 exact replay를 확인한다. 단위·clock·formula 의미를 자동 추정하지 않으며 상세 계약은 [WDSEventDB 안내](WDS_EVENT_DATA_KO.md)를 따른다.





