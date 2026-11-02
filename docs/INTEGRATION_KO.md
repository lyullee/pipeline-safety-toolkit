# 기존 프로젝트 연동

다른 저장소의 원격·소스 코드를 수정하지 않고 실제 클래스·공개 함수를 호출했다.
로컬 체크아웃에 커밋이 없으면 커밋을 추정하지 않고 `null`과 소스 트리 SHA-256을
기록한다. 실행 당시 상태는 `research/integration_results.json`이 기준이다.

## 확인한 경계

| 소유 프로젝트 | 실제 호출 | 이번 확인 |
|---|---|---|
| matintegrity | `evaluate`, `RunContext`, `Evidence`, 소유 레지스트리 검색 | 부식 균일 예측·다층 원통 열전달·합성 검사 경로의 두께 추세 호출 |
| process-monitoring-toolkit | `SignalBatch`, `Monitor`, `create_detector('robust_z')` | BattLeDIM 864행, 시간순 fit/calibrate/score 288행씩 |
| human-reliability-toolkit | `SignalSeries` | 압력 수두 채널 864행의 시계·단위·생성 방식 전달 |
| lh2dt_models | `HydrogenProperties`, `StrokeLimitedCommandedValve` | P/h 온도 왕복 약 1.3×10⁻¹² K, 2초 스트로크에서 0.5초 후 개도 0.75 |
| PRISM | `ProviderPortState` | 실제 포트 스키마 생성과 원래 시계·물성 기준의 별도 보존 |

모니터링·HRA의 신호 연동은 감지 정확도 또는 HEP의 현장 검증이 아니다. 압력 신호로 HEP를
자동 생성하지 않았다. 초기 실행 당시 Mat 목록은 74개였고 후속 검색에서 75개를
반환했다. 당시 새 `transport.hydrogen_slab_kinetic` ID도 범위 목록에 외부 소유로
추가했다. 검색 성공을 모든 계산의 수치 재검증으로 표시하지 않는다.

수소 장치·PRISM 결과는 `research/physics_integration_results.json`, 공개 소스의
조사 커밋은 `research/external_source_versions.json`을 따른다. 현재 대상 PRISM
포트는 수소/파라수소와 액체·기체·2상을 받는다. 상위 포트가 초임계이거나 다른
유체이면 몰래 기체로 바꾸지 않고 거부한다. source state와 atmospheric source는
다른 경계다. 이번 실행은 확산·피해·빈도 계산을 실행하지 않았다.

```powershell
python scripts/check_physics_integrations.py --lh2dt PATH_TO_LH2DT --prism-source PATH_TO_PRISM/lh2-tno/src
```

강건 점수의 초기 교정 임계값이 약 2.04×10⁶으로 커서 훈련 채널의 0-MAD·준상수
조건과 scale_floor 정책을 추가 검토 대상으로 남겼다. 연동 성공이 바로
검출기 적합성을 의미하지 않는 구체적인 사례이며 PI-X05에서 확장을 요청한다.

## 기존 장치 팩토리 재사용

`equipment_exchange.LH2EquipmentClient`는 소유 팩토리의 22개 모델 식별자와 필수
입력을 조회하고 실제 장치를 생성한다. 필수 입력이 없으면 needs_data를 반환하며,
검토하지 않은 필드명은 기본 정책에서 거부한다. owner_native 정책을 명시해
직접 전달할 때도 미검토 이름을 기록한다. 장치 생성 ready는 물리 검증 통과가 아니다.

실제 관 양단 상태 평가, mapped pump 곡선 변환과 운전점, 액체/2상 동적 제어체적의
일부 평형·응답을 실행했다. 동적 상태는 장치 파라미터 해시·자산·시계에 묶이며,
시간 세분화가 선언한 질량·에너지·벽온도 일치 기준을 만족해야 진행한다.
원 엔진의 마지막 시각 미분값을 전체 시간 구간의 적분 유량으로 바꾸지 않는다.
원 엔진의 물리 적합성 실패도 보류로 보존한다. 통합 질량·에너지 유출 원장은 PI-X01의
후속 요구이며, 전체 22종 장치와 다상 파 전파를 모두 검증한 상태는 아니다.

```powershell
python scripts/check_equipment_factory.py --lh2dt PATH_TO_LH2DT
```

실행 증거는 `research/equipment_factory_results.json`, 상세 API와 단위는
`docs/API_KO.md`에 있다. 소유 패키지는 클라이언트를 생성할 때 지연 import하므로
이 연계가 필요하지 않은 핵심 전용 설치에 수소 엔진을 강제하지 않는다.

## 실제 사고 이력 전달

`PipeAccidentRequest`로 초기 상태·개구·주변 경계·관찰 시간을 명시하고
`client.export_accident_history(built, request)`를 호출한다. 원 엔진의 기체와
액체 이력은 PRISM의 기존 resolved-release 빌더를 재사용한다. 원 응답·문맥·
시작 시각·해시를 함께 보관하며, 기체 분기 상태와 질량 장부를 추가 확인한다.

```powershell
python scripts/check_release_exchange.py --lh2dt PATH_TO_LH2DT --prism-source PATH_TO_PRISM/lh2-tno/src
```

일반 수소 기체의 유한 재고 기준 사례는 실제 패킷으로 전달했다. 파라수소
표시가 Hydrogen으로 바뀌는 사례와 2상 기체 분기 밀도·액체 도달 위치 오류는
withheld로 남겼다. 수신 스키마가 파싱됐다는 이유만으로 전달하지 않는다.
PI-X01의 재현·수정 조건과 `research/release_exchange_results.json`을 따른다.
양방향 파단·차단 후 관 재고·전체 열 원장·독립 검증은 남는다. 이력의 종료
문자열이나 순간 개구 폐쇄를 현장 방호 승인·유한 구동 완료로 바꾸지 않는다.

## 실행 방법

관련 프로젝트를 설치한 환경에서 어댑터를 직접 사용한다. 소스 체크아웃을
선택해 검증할 때는 다음과 같이 경로를 인자로 제공한다.

```powershell
python scripts/check_integrations.py --materials PATH_TO_MATINTEGRITY --monitoring PATH_TO_MONITORING --hra PATH_TO_HRA
```

이 스크립트는 제공한 `src/`를 현재 프로세스에서만 import 경로에 추가한다.
다른 프로젝트에 파일을 쓰지 않는다. 공개 결과에는 개인 체크아웃 경로를 넣지
않고 버전·소스 해시·결과를 기록한다.

```python
from pipeline_integrity.adapters import MaterialClient
# context는 이 패키지의 RunContext
# result = MaterialClient().evaluate(
#     "corrosion.uniform_forecast", parameters, context, location_id="segment-01")
```

Mat의 원본 응답은 `ExternalResult.payload`에 보존한다. 자산·위치·스키마가
바뀌면 거부한다. 실험을 측정으로, 시뮬레이션을 모델 예측으로 변환할 때 원래
분류도 metadata에 남긴다. 문헌 출처를 `open_dataset`로 자동 표시하지 않는다.

## 신호 전달

반복 검사 연결은 `docs/INSPECTION_KO.md`와 `research/ili_application.json`을 따른다.
이쪽에서 단위·용접부·위치·시계·정합 검토·오차를 보존하고, 재료 추세는 기존
함수를 호출한다. 실제 자료의 후보 정합을 승인 경로로 자동 바꾸지 않는다.
`MaterialClient.models/describe`는 소유 레지스트리의 기능을 직접 사용한다.

모니터링 배치는 같은 스트림·시계·채널 순서·단위·생성 방식을 요구한다.
결측은 NaN과 valid=False로 넘기며 시간을 압축하지 않는다. 모드가 일부만
있으면 추정해 채우지 않고 오류를 반환한다. HRA 신호도 무효 표본과 None을
유지한다. 레이블·검출 정답은 측정 행렬에 섞지 않는다.

현재 `SignalFrame`에는 자산·포트가 독립 필드로 없다. 스트림과 별도의 설비
매핑을 명시해야 한다. 외부 모듈의 시계·모드·설비 식별을 강화하는 후속 요청은
확장 요청서에 기록했다.

## 추가 통합 대상

| 프로젝트 | 사용할 책임 | 현재 경계 |
|---|---|---|
| lh2dt_models, DynamicLH2PoolX | 재고·상변화·방출·풀 | 일반 배관·명령 이력과의 결합 필요 |
| slabx, degadisx | 방출 후 확산 | 유체·물성·기준 상태 검토 필요 |
| pifira, PRISM | 화재·폭발·위험 집계 | 사건 ID와 빈도 유도 계약 필요 |
| hydrogen-station-sim | 공급·압축·저장·충전 시계 | 포트·명령·완료·질량/에너지 수지 연결 |
| 위험 엔진 | FTA·ETA·LOPA·근거 이력 | 기회당 확률·시간·노출 단위의 명시 |
| SAGA | 근거 검색·분석 후보 | API 버전과 승인된 수치 모델 구분 |
| prism-dynasense | 감지·동역학·위험 통합 | 모듈 책임과 이벤트 중복 점검 |

기존 공개 PRISM 분석에서 `lh2_tno.derived_frequency` 소비 기능의 버전 차이를
확인했다. 새 패키지에 위험 집계 엔진을 중복 구현하지 않고 요청서의 공통
사건 계약으로 소유 프로젝트가 확장하도록 한다.

## 방출·압축성 평면에서 사고 결과로

기존 PRISM의 평면 생성·부착, 소스 고정 워커, SLABx·복사·위해도와 연간
계산을 실제 호출했다. case·request·기하학·시계·원 패킷·소스 해시를 유지한다.
정렬 바람은 통과하지만 횡풍과 20도 기울어진 바람은 각각 방향 범위·투영
단면 수지 때문에 보류된다. 미계산 조건을 제거하지 않는 연간 범위도 확인했다.
구체 입력·수치·원 기록은 CONSEQUENCES와 `research/consequence_results.json`을 따른다.
이는 유한 시간창의 진단 경로이며 전체 사고·현장 위험의 검증은 아니다.

## HRA에서 기존 위험 엔진과 PRISM으로

HRA의 원 관측 바인딩·FTA·ETA·LOPA 및 PRISM DemandFailure를 실제 호출했다.
`risk_exchange`는 요구당 확률의 조건·분모·포함 효과와 이미 소비한 방호를
보존한다. 입력·결과·재현 명령은 `RISK_EXCHANGE_KO.md`, 실행 증거는
`research/risk_exchange_results.json`에 있다. 기존 수학 구현을 복제하지 않았다.
이 연결이 물리적인 누설 빈도나 전체 사고 결과의 연성을 완성하지는 않는다.
고정 버전에 없는 derived_frequency는 PI-X02의 수용 확장으로 남는다.

## 성분 수송에서 모니터링으로

`water_quality_batch`는 기존 모니터링의 실제 SignalBatch를 생성한다. 합성
출처 추적 기준에서 201행·2채널, 단위 1, 생성 방식 simulation과 결과 해시를
확인했다. 추가 metadata는 기존 확장 필드를 사용하며 별도 검출기를 만들지
않는다. 공개 관망의 보류 결과는 전달을 거부한다. 실행 증거는
`research/water_quality_results.json`이며 이 검사는 검출 정확도·HEP를 계산하지 않는다.

## 기존 밸브 구동에서 액체 MOC와 HRA로

원 LH2DT 구동기의 set_target/advance와 개도만 재사용하고 기체 유량·물성은
호출하지 않는다. 곡선·명령·시계가 포함된 receipt를 액체의 압력 의존 유량
경계로 연결했다. 실제 HRA SignalSeries에는 161개 개도·단위 1·simulation을
전달했다. 의미 metadata의 원 API 확장은 PI-X06, 독립 구동 팩토리와 기체
차단 후 재고는 PI-X01에 구체화했다. 자세한 계약과 재현은 VALVE_TRANSIENT,
실행 근거는 `research/valve_transient_results.json`에 있다.

## 압력파에서 기존 재료 피로로

압력 이력을 원 OperatingHistory와 workflow.pressure_fatigue로 전달했다.
막응력·레인플로·Basquin·Miner는 원 엔진에 맡긴다. 같은 창의 원 입력·응답·
위치·시계·근거와 손상 장부를 보존한다. mixed-origin의 원 API 확장은 PI-X04에
구체화했다. 실제 원 호출 5개와 압력파·공개 관측의 재현은 PRESSURE_FATIGUE와
research/pressure_fatigue_results.json을 따른다. 계산 손상에서 위험 사건 확률을
자동 생성하지 않는다. 같은 하중 창을 재료·위험 단계에서 중복 적용하지 않는다.

## 기존 동역학 엔진으로 정상 기체 관망

원 SteadyNetwork와 Pipe, 물성 클래스를 같은 유체·에너지 기준으로 묶었다.
기체 물성·마찰·접점 해법을 새로 복제하지 않았다. 지정 인출은 별도 제어면의
질량·엔탈피 경계로 표현하고 계산용 말단은 물리 노드 목록에서 제외한다.
도착 엔탈피 상태·독립 수지·정체 보류와 원 응답을 보존한다. 16개 실제 사례와
CLI·원 소스 해시는 gas_network_results에, 계약과 재현은 GAS_NETWORK에 있다.
전체 에너지·고도·직접 노드 부하·잔차 스케일·능동 장치는 PI-X01의 확장 대상이다.

## HRA 작업에서 자원 일정으로

원 Task·ordered_steps·analyze_task를 재사용하고 모든 단계→작업·역할→자원을
해석 근거와 함께 연결했다. 선행 관계는 명시한 finish-start로 확인하며
native deadline은 계획 기한으로 자동 변환하지 않는다. 실제 4단계·5검토
후보·실패 사건 정의를 유지했다. 반환 planned_hra_steps는 계획의 경과 초이며
원 Task를 수정하거나 observed 완료·전제 충족·HEP로 승격하지 않는다.
원 API의 계획/관측·기한 기준·역할 수용은 PI-X06, 계약·기록·재현은
SCHEDULING과 research/scheduling_results.json을 따른다.

## 실제 HRA 인지·시간·복구 연결

`research/human_response_results.json`의 17사례는 원 cognitive_chain, 시간 분포,
중단 자료, 기한, 완료 구간과 복구 계산을 호출했다. 최종 확률을 실제 FTA와 연간
환산에 전달했으며 원 소스는 변경하지 않았다. 원 HRA 공개 terminal 자료의 시간·
복구 재적용은 거부했다. 이 경계에서 데이터 의존성이나 공정의 기한을 추정하지
않으며 전체 현장 보정과 공동 시간·복구 모델은 PI-X06의 후속 범위다.

## 원 재료 검출 계산과 공유 일정

검사 계획의 11개 실제 사례는 원 inspection.logistic_pod 및 no_indication_update를
호출하고 공유 CP-SAT/일정 감사로 연결했다. 원 입력·설비·위치·시계·context 해시·
상태와 원 결과 전체를 보존한다. PoD 범위 밖 후보는 비교에서 삭제하지 않고 보류한다.
POD나 베이지안 갱신·반복 검사 계산을 이 저장소에 복제하지 않았다. ILI 13개 전체의
보류와 별도 9개 가상 후보/96메뉴 비교는 서로 다른 적용 범위다.

## 원 초음파 두께 API의 데이터 연결

`research/nde_data_review.json`에는 TUBERS 231파형 판독과 가정한 에코의 5개
교환 사례가 있다. 원 inspection.ultrasonic_thickness의 3개 computed·1개
out_of_scope를 보존했고, 마지막 전압 결측 구간은 원 엔진 호출 전 보류했다.
에코·소재·교정의 물리적 타당성이나 actual thickness를 자동 승인하지 않는다.
native 시계·원본 근거·입력·m 단위·원 상태를 확인했고 원 재료 소스를 변경하지 않았다.

## 기존 탱크 형상과 실험 신호

`research/tank_inventory_results.json`은 lh2dt.HorizontalVesselGeometry의 수평
평판/타원 끝단 두 형상과 각 17점의 체적을 실제 비교했다. 최대 차이는 각각
0 및 약 3.48e-7m³다. 이 교환은 체적 형상만 확인하며 원 wetted area·열·층상
동역학·기존 DPT/압력 보정을 검증한 것으로 확대하지 않는다. 원 모델은 복제하지 않는다.

결합 탱크 실험 1의 원 상대 시각과 단위 매핑을 유지해 81행×3채널을 기존
SignalBatch로 전달했다. 마지막 명령 결측을 NaN/valid=False로 유지했고 시계는
수신 객체 metadata.clock_id에 있다. 원본의 형상·단위·영점·상태는 미확정이며
명령 %는 유량이 아니다. 단위/형상 가정의 3240개 스냅샷은 검출 성능·질량 수지
검증이 아니다. 소스 트리 해시와 변형 여부, PI-X01의 후속 계약을 함께 확인한다.

## 원 PID·계측·탱크/밸브의 폐루프

control_exchange_results.json은 원 PID·1차 계측·HomogeneousTank·유한 스트로크
Valve를 실제 호출한다. 탱크의 압력/온도 기본·시간 세분화·관측 공백 6사례와
밸브 유량 150판단은 명령을 다음 구간에 적용하는 폐루프다. 탱크 최대 질량
잔차는 약1.5e-16kg, 전체 에너지 잔차는 약4.4e-11J 이내다. 원 장부와 수치
기준이며 독립적인 물리 실험 검증은 아니다. 온도는 120초에서 목표보다 약0.916K
높아 성능을 승인하지 않는다. 구동기 위치는 명령 시각에 즉시 이동하지 않는다.

9개 계측 오류 시나리오의 지연/미래 보고 시각/dropout은 PID에서 보류했다.
공개 결합 탱크 10실험·800개 shadow 명령은 원 관측·pump %를 대체하지 않는다.
기존 모니터링 SignalBatch와 HRA SignalSeries에는 원81표본의 시계·단위·원점·
마지막 명령 결측을 보존했다. 추가 모드·추종·인터록·실제 시간/교정·사람의
완료 근거는 PI-X01/06과 CONTROL_EXCHANGE를 따른다. 원 소스는 변경하지 않았다.

## 원 재료 압력·열·단면 모델 7개

material_sections_results.json은 원 Lamé·평면 응력·균일 열구속·평판 층·lumped
온도·1D 고체 과도·평면 두께 열응력을 실제 호출한다. 4종 압력 단면 각129점은
표면 압축 응력과 횡/장축 힘 평형을 확인했다. 열/단면16사례는 원 적용 영역과
검토 누락·탄성 한계·물성 범위를 확인했다. 원 무열 단열은 상대 에너지 판정1로
out_of_scope라 그대로 기록했고 PI-X04에 절대+상대 기준을 요청했다.

원 FVM의 셀 평균과 원 평면 응력 프로필은 자동 연결하지 않는다. 실제 기하·
끝점·프로필 투영·평면/원통 조건을 먼저 검토한다. 새 MaterialJob은 모델/스키마·
단위/확률 의미·물리 위치/clock·원 입력/결과 해시를 보존한다. 과도 고체와 평면
열응력2개를 기술 목록에 추가했고 기존5개와 함께 실제 인터페이스를 확인했다.

원 봉재77CSV/215001행은 기존 reader로 읽어 숫자를 원 파일과 전부 대조했다.
50cu3의1937표본은 모니터링/HRA에 전달했다. excess K 기준은 별도 매핑에 남으며
unit=K만으로 절대 온도 한계와 비교하지 않는다. 조건부21점 열 경계는 가정한
형상/물성/ambient/sensor-face이며 독립 성능이나 실제 관 응력 검증이 아니다.

## 원 Mode-I·Paris·사용자 경계와 raw crack-arrest

원4모델을20사례로 호출했다. Mode-I 저항 초과도 computed로 남고 qualified critical
깊이는 geometry/wall 범위 안에서만 제공한다. 일정 진폭의 m=2/3 수명과 독립
quad 적분은 약1.8e-15/1.1e-13cycle 차이다. 순서 성장의 threshold/R/calibration/
저항/깊이 종료와 부분 궤적을 보존한다. 사용자 경계의 inside/above/outside-support/
미검토 상태를 실제 확인했다. 규격 곡선·표는 복제하지 않았다.

중첩 tuple 계약은 원 run_json의 strict JSON으로 처리한다. 기존 MaterialClient의
strict Python 기본 연동도 실제 재실행했고 고체 열/단면7모델 기준은 JSON 경로로
갱신했다. 공학적 status·하중/위치·원 context와 input/result digest를 유지한다.

C1/C2 18CSV/79341행을 원 reader와 독립 raw CSV로 대조했다. 한 header 표기 결함과
signed N/m·원 cycle·filename 온도를 보존하고4631행을 원 monitoring/HRA에 전달했다.
이를 KIC·성장계수·실제 dynamic arrest·HEP·파손률로 바꾸지 않았다. 계약·결과와
추가 자격 요청은 FRACTURE_EXCHANGE 및 PI-X04/02를 따른다.


## 구조 프레임의 소유자와 신호 교환

새 프레임 강성/해석 엔진 대신 PyNiteFEA 3.2.0을 선택 호출한다. 배포 wheel과
실행 Python 소스23파일의 해시·버전을 기록했다. Mat의 검토한 원시 시험 판독기를
재사용해 signed 하중32개를 가정한 축 프레임에 연결하고, 실제 monitoring
SignalBatch32×24와 HRA SignalSeries32개를 생성했다. 출력 origin은 simulation,
원 하중은 laboratory로 유지한다. 실제 시험 치구·동역학·오류확률을 검증하지
않았다. PI-X01/04는 열/압력의 기계적 하중 경로·관 부속품 유연성·국소 응력
복원과 실제 교정 근거를 요청한다. 소유 프로젝트의 소스는 수정하지 않았다.


## 온도와 압력의 기계적 효과

기존 Mat free thermal strain과 Lame radial/hoop/axial 결과를 읽고 PyNite의
강성 해석을 재사용했다. 열 구속률은 원 입력에서0으로 두고 실제 지지로
구속을 구한다. 원통의 total cap traction과 -ν(σr+σθ)/E를 분리해 입력하고
초기변형률의 실제 부재력·노드 수지를 복원한다. 같은 기준시각/context의
준비 기여를 조합하며 원본 변경·중복을 거부한다. 새20사례와 원 온도차
16snapshot의 실제 모니터링16×24배치·HRA16표본 교환을 기록했다. 실제 rod
구조 응답 검증과 분기 압력 경로·열 구배/곡률·상태별 물성은 후속 요청이다.


## 전력·일정·기대횟수의 재무 연결

기존 cashflow/energy를 version2로 확장하고 재사용했다. EPS1 4회차의24000개
원값과 시간·독립 적분을 대조하고 각59.99초를 실제 모니터링/HRA 클래스에
전달했다. 마지막0.01초를 채우지 않았다. 단위가W인 표본을 declared 횟수/요금/
배출계수와 명시적으로 연결한다. 원 데이터로 연간 duty를 추정하지 않는다.

실제 CP-SAT5작업의 선택 모드/기간/시각에 별도 요금을 적용했다. nativeHRA의
0.2/demand와30demand/year는6expected events/year로 전달됐고 조건부25REF와
150REF 기대 재작업 비용을 구했다. 실제 노출·손실·실패율이나 투자 절감률이
아니다. 원 조건·소비 사건·source hash·valuation context와 상태를 보존한다.
다른 손실 partition·관측 작업/중단·sample 의미는 확장 요청서에 남겼다.

## 동일 불확실성 표본의 전달

외부 엔진은 `EnsembleNode.evaluate(values, context)`에서 원 API를 호출하고
`EnsembleValues`에 각 결과의 unit/status/native_payload를 반환한다. 원 asset,
scenario, clock과 metadata.ensemble의 design_digest/draw_id를 모두 유지한다.
이 좌표는 관측 타임스탬프가 아니므로 SignalFrame 시계에 기록하지 않는다.
모델 결함·실제 입력 부적합·미상 입력의 처리를 구분하며 원 오류를 무조건
삭제하거나 표본을 재추출하지 않는다. 실제 HRA 연간 기대횟수와 비용 교환은
check_uncertainty.py의 실행 기록을 따른다. 공유 변수의 copula 행렬은 잠재
정규 공간의 상관이며 실제 변수 Pearson 상관으로 바꾸지 않는다.

## 크리프·열화와 기간 사건

creep_exchange는 원 Mat7모델·CMSX-6 시편/이력 판독기를 사용한다. native7모델과
독립12기준·12시편9파단/3검열·140032이력 행을 실제 확인했다. 원 온도 적합 보류와
반복시각23구간을 보존했다. CREEP_KO의 원 receipt·모델 해시·지원·시계를 따른다.

조건부 첫 파단을 research mission_probability로 만들고 원 HRA의 일반 Boolean
함수에서 단일 사건 수치가 같음을 확인했다. 기존 demand 전용 fault_tree와
annualize의 보류를 유지한다. 별도 다사건 기간/생존/의존성 연결은 현재
PERIOD_RISK_KO를 따른다. 실제 봉쇄 상실·누설·연간 고장 빈도를 추정한 것으로
집계하지 않는다.

## 여러 설비의 공통 기간과 공동 상태

PERIOD_RISK_KO의 별도 어댑터로 기존 HRA의6수치 기준과 CMSX-6 12시편의
재적합/두 가상 부품 공동 시나리오를 확인했다. 원 확률·물리 범위/endpoint·
나이/기간·목표 context·공통 조건과 원 사건/방호 계보를 보존한다. 공유 모델의
독립 계산, 원 집계/구성 사건의 중복, 다른 기간/조건은 보류한다. 원 엔진과
기존 demand 경계를 수정하지 않았다. 실제 시간 순서·수리·누설 전이는 후속이다.

## 시험 하중·팽창 일·순 기체 재고

PRESSURE_TESTS_KO의4원모델·12작성 기준과 공개10이력/310행의 보류/가정 조건을
실제 실행했다. 원 Mat의 strict JSON·상태·단위/시계/근거와 공통 CoolProp 상태를
재사용한다. 몰밀도를 원 R 기준의 Z로 연결하고 부호/참조 상태·이력 수지를
보존한다. PE 변형·대기압·대표 gas T/조성·누설 기구와 승인 기준은 PI-X04에 남겼다.

## 용접·제작의 기존 엔진과 실제 원기록

WELDING_KO의 WeldingRequest와 전기/열 helper는 원8모델과 MaterialJob/receipt,
signed power 및 기존 단위를 재사용한다. Native8/22기준·EBW7cooling/5band·Tandem
4기록/명시창4개의 실제 교환을 확인했다. HDF 원점과 유효성·선택 근거는 새
reader가 보존하며 raw배열과 source 파일은 공급자checksum으로 고정한다.

열효율/경로·대표 센서와 온도·야금/절차 근거가 미지이면 보류한다. BAM의
S355J2+N/ER70S-6와 별도 EBW HSSS 실험을 같은 용접으로 합치지 않는다.
희석 조성을 완전한 assay로 바꾸거나 경도 기준의 satisfied를 전체 용접 승인으로
바꾸지 않는다. 확장 계약은 PI-X04의 센서/원소/질량/geometry·규칙 공급을 따른다.

## 경보·명령 endpoint와 물리 사건 강도

AlarmPolicy, HumanResponseClient/HRA, native finite-stroke와 기존 관망을
RESPONSE_TRACE_KO의 source-bound 시간 계약으로 연결했다. 실제 개도/면적/공정
유량과 작성 command시간 모델의 출처를 유지한다. command 성공 ETA말단은
누설/피해 endpoint가 아니다. 같은 timing/recovery 효과를 다시 사용하지 않는다.

물리 신뢰도2모델도 기존 Mat의 MaterialJob/receipt를 그대로 쓴다. posterior
mean intensity만 명시 year로 연결하고 survival probability는 mission_measure와
물리 나이/기간을 유지한다. 원 source pin이나 완전한 포착/노출·조건 검토가
없으면 보류한다. gate/FTA/exclusive path를 복제하지 않았다. 상세한 입력과
지원 경계는 PHYSICAL_RELIABILITY_KO를 따른다. 원자료로 실제 사람·물리 고장을
보정하려면 PI-X02/03/04/06의 후속 계약이 필요하다.

## 제작·경도·인장 원 API와 관측 의미

제작3·압흔2·인장/변형률4모델을 기존 Mat MaterialJob/receipt로 호출한다. 시편·
재료 상태·시계·위치·원 source/evidence를 보존하고 SI와 서로 다른 hardness
표현·계산 status/사용자 요구를 나눈다. 새 재료/압흔/토크 구성식을 복제하지 않았다.
연계는 FABRICATION_KO, HARDNESS_KO, CHARACTERIZATION_KO를 따른다.

체결 원48시험은 각도·표본 격자/속도·마찰과 실제 K/인장면적 의미를 유지한다.
구리 합금의20LIS와23압흔보고서는 source metadata·게이지/여행·힘/길이 단위를
보존한다. 시편 raw processing이나 가정 기하 계산을 현장 재료 자격·규격 적합·
HRA오류율로 넘기지 않는다. 누락 조건·다른 축의 정합·부분 출력과 재료 모수의
후속 계약은 PI-X04를 따른다. 다른 소유 프로젝트의 소스는 수정하지 않았다.

## 원 검사 네 모델과 반복 하중 세 모델

다음 일곱 모델을 원 Mat의 `MaterialClient`와 `MaterialJob/Receipt`로 실제
호출했다. 이쪽에는 원 통계 적합·신뢰한계·기하 계산·재료식·경로 적분 또는
근찾기 구현을 복제하지 않았다. 각 원 모델 버전은 확인한 실행에서 `1.0.0`이며
입력 schema digest와 출력 단위·확률 의미를 원 registry에서 고정한다.

| 소유 모델 | 이쪽 경계와 보존하는 의미 |
|---|---|
| `inspection.binomial_evidence` | `InspectionStatisticsRequest`의 실제 binary truth/response·크기 또는 blank·독립 기회 |
| `inspection.hit_miss_pod` | 실제 시편·결함 크기·검출·cluster·절차 조건; POD는 크기/절차 조건부 검출확률 |
| `inspection.tofd_depth` | 같은 frame의 lateral/diffraction pick·probe/wedge·교정 근거; 제한된 대칭 경로 기하 |
| `geometry.thickness_grid` | 원 행/열·셀 면적·좌표계·미상 mask·signed 순손실; 측정 범위의 기술량 |
| `fatigue.goodman` | `CyclicFatigueRequest`의 인장 평균·half-range 진폭·일치하는 강도와 재료 조건 |
| `fatigue.hysteresis_path` | 원 경로 순서·부호·일 켤레·cycle 경계·명시 선형 폐합과 미상 닫힌 일 |
| `fatigue.strain_life` | 완전반전 일정 진폭·cyclic 계수·교정 cycle 구간·cycle/reversal 구분 |

[검사 통계 가이드](INSPECTION_STATISTICS_KO.md)와
[반복 하중 가이드](CYCLIC_FATIGUE_KO.md)에 API·전필드·단위·review 키와
원 solver·재현 방법을 기록했다. 기본 review와 evidence가 없거나 필수 값·
의미가 미상이면 원 엔진 import 전에 `needs_data`다. 잘못된 타입·비유한값·
단위·시계·예산·pin 위반은 예외다. 검사 미상 cell처럼 원 모델이 지원하는
명시적인 부분 관측은 그 계약에 맞춰 유지한다.

검사 15사례·24개 독립 수치 기준과 피로 원 응답 16개·10개 독립 기준을
확인했다. 공개 요약은
[검사 실행 증거](../research/inspection_statistics_results.json)와
[피로 실행 증거](../research/cyclic_fatigue_results.json)다. 원 요청·관측행·
상세 receipt는 `artifacts/inspection_statistics`와 `artifacts/cyclic_fatigue`에
보관한다. 원 native가 반환한 out_of_scope·needs_data·비유계·음의 값·null을
합격으로 고치지 않는다. TUBERS/ILI 관측으로 독립 hit/miss 정답을 만들지
않았고 공개 관측으로 cyclic 계수를 보정한 결과도 없다.

## 공통 원 소스 고정과 배포 경계

동시에 수정되던 원 개발 소스의 변경을 실행 전후 hash 검사가 검출했다.
그 실행을 소급 통과로 바꾸지 않고 최종 재현에는 별도로 검토한 private
source snapshot을 사용했다. Python 소스 76파일의 tree SHA-256은
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다.
MIT LICENSE·외부 고지와 원 package가 필요로 하는 catalog JSON을 유지했다.
snapshot은 이 프로젝트의 배포 경계 밖에 있으며 원 코드·원관측·개인
절대경로를 wheel·source archive·공개 결과에 포함하지 않는다. 과거의 69파일
및 다른 76파일 소스 실행은 각 당시의 hash를 유지한다. 소스 tree가 같다는
사실도 독립 실험 검증이나 고정되지 않은 실행 환경 전체의 동일성을 뜻하지
않으므로 dependency/runtime 기록을 함께 확인한다.

`material_source_guard(source_root=None, expected_source_sha256=None, client=None)`는
원 package/evaluator와 공급된 registry가 선택 root의 `src` 소속인지 확인한다.
정렬한 상대 Python 파일명과 바이트를 전후에 해시하며 계산 예외가 나도 변동을
검사한다. 공개 `source_pin`에는 전후 tree SHA·파일 수·불변 여부만 남긴다.
`with` 종료 후에 `after`가 완성되므로 그 뒤에 직렬화한다. pin을 생략한 호출은
소스 고정을 수행했다고 주장하지 않는다.

저장된 보고서는 공통 `verify_material_source_pin(report, expected_source_sha256,
executed=True)`로 검사한다. 기대 SHA와 전후 fingerprint·양의 정수 파일 수·
불변 확인이 맞아야 한다. 원 계산 전 보류는 `executed=False`이며 report는
`None`이어야 한다. source fingerprint를 별도로 조사한 기록과 실제 원 계산의
guard 실행 보고서를 혼동하지 않는다. 이 검사는 기록의 내부 일치 검사다.
전자서명·공급자 인증·실행 진위나 독립 물리 검증을 제공하지 않는다.

```powershell
python scripts/check_inspection_statistics.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT --source-kind private_pinned_snapshot
python scripts/check_cyclic_fatigue.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
```

스크립트는 `check_integrations.revision`의 전후 tree SHA를 기록한다. Mat의
정식 배포 주소나 commit이 확인되지 않았다면 추정하지 않고 선택한 소스와
라이선스·고정값을 사용자가 별도로 관리한다. 상세 계약은 API_KO와 각
가이드를 따르며 core 단독 설치와 선택적인 원 엔진 실행을 구분한다.

부식·마모 추가 경계 `corrosion_wear`의 power-law·Archard·면적 최대 Gumbel
세 모델의 실제 연동도 같은 고정 소스에서 확인했다. 원 모델 버전은 모두
`1.0.0`이고 원 응답 12개·작성 스칼라 기준 11개·거부 경계 5개를
[부식·마모 실행 증거](../research/corrosion_wear_results.json)에 기록했다.
[CORROSION_WEAR_KO](CORROSION_WEAR_KO.md)의 입력·단위·지원 조건과
`python scripts/check_corrosion_wear.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT`을
따른다. 이 스칼라 대조는 원 Gumbel 적합·재표집 또는 물리 꼬리의 독립 검증이
아니다. 별도 [공개 구리·AFM 자료](CORROSION_SOURCE_KO.md)의 열네 원조건
요청은 단위·노출 원점·접촉 대응·면적·모집단 부족으로 모두 `needs_data`이고
원 수치 모델 호출은 0회다. 작성 조건의 소프트웨어 검증을 이 자료의 물리
교정이나 현장 전이 검증으로 표시하지 않는다.

## 조성·수소 수송·감육 용량과 기존 위험 엔진의 연결

같은 원 소스 스냅샷에서 다음 아홉 기능의 실제 호출을 확인했다. 원 모델은
각각 `1.0.0`이며, 기존 `MaterialClient`·`MaterialJob/Receipt`·공통 소스 guard를
재사용한다. 이쪽에 원 조성 비교·적합·수송 solver·면적 scan·FTA/ETA를 복제하지
않았다. 아래 수치 기준은 독립 작성 조건의 소프트웨어 확인이다.

| 원 기능 | 실제 확인과 생산 소유 경계 | 상세 가이드·공개 집계 |
|---|---|---|
| chemistry reconciliation·pitting index·impact transition 3종 | 원 13응답·독립 기준 18개·거부 9개. `MaterialQualityRequest`가 질량 분율/백분율·같은 assay·완전 impact 시리즈와 지원 범위를 보존 | [재료 품질](MATERIAL_QUALITY_KO.md), [실행 집계](../research/material_quality_results.json) |
| semi-infinite·trap equilibrium·slab kinetic 3종 | 원 16응답·독립 기준 21개·거부 4개. 원자 몰·점/국부/셀 평균·양면 inward flux·독립 속도·원 solver 상태를 유지 | [수소 수송](HYDROGEN_TRANSPORT_KO.md), [실행 집계](../research/hydrogen_transport_results.json) |
| structural profile capacity | 원 12응답·독립 기준 10개·거부 4개. 동일 형상/재료의 기준 압력차·팽창계수 공급자와 전체 axial profile의 nodal scan | [감육 용량](PROFILE_CAPACITY_KO.md), [실행 집계](../research/profile_capacity_results.json) |
| independent gate·exclusive paths 2종 | 원 Mat 10응답·기존 HRA API 5사례·독립 기준 16개·거부 4개. 생산은 기존 위험/기간 API를 사용하며 새 중복 게이트 함수 없음 | [위험 수치 대조](MATERIAL_RISK_REFERENCE_KO.md), [실행 집계](../research/material_risk_reference_results.json) |

재료 품질의 `computed`와 조성 outcome의 `unknown`은 함께 존재할 수 있다.
검열 구간과 미측정 원소를 점값이나 0으로 채우지 않는다. 조성 지수를 실측
부식률로, impact crossing을 파괴인성·허용 사용 온도로 바꾸지 않는다.
한 재료의 shelf 지원과 수치 식별을 충족하지 못한 원 적합 진단도 보존한다.

수소 수송은 원자 몰과 격자 기준을 명시한다. 국부 미분 확산계수를 전체 투과
계수로 재표시하지 않으며 총 전류를 경계 농도로 역변환하지 않는다. 유한 슬랩의
독립 연속 급수 대조와 두 격자의 오차 감소는 선택 조건의 수치 확인이다.
작은 질량 잔차나 두 격자 비교를 전체 오차 상계·현장 투과 검증으로 확대하지
않는다. 부분 solver 실패의 궤적은 완료 결과로 넘기지 않는다.

감육 용량의 기준과 demand는 내압−외압이다. 원주 범위·형상·좌표·측정 편향을
해결하지 않은 ILI/2D grid를 단일 축 방향 트랙으로 자동 환산하지 않는다.
원 절점 창 비교는 연속 끝점 최적화와 구분하며 손실이 지원 범위를 넘으면
공급자 표를 외삽하거나 불리한 창을 삭제하지 않는다.

위험 원시 기능의 수치 일치는 사건 의미의 일치가 아니다. 같은 사건 A를 두 번
참조하는 FTA와 A의 두 독립 복사본은 다른 계산이다. `unscoped_event_probability`와
1/s의 배타 경로 빈도에 별도의 기간·조건·생존·기회를 공급해야 기존 위험 계약으로
검토할 수 있다. HRA·FTA·ETA·기간 위험의 생산 소유자는 유지한다.

```powershell
python scripts/check_material_quality.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_hydrogen_transport.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_profile_capacity.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_material_risk_reference.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT --hra PATH_TO_HRA
python scripts/acquire_data.py --only bam_s355_charpy ahss_hydrogen_permeation --workers 2 --maximum-seconds 45
python scripts/review_material_process_data.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
```

새 두 CC BY 4.0 자료군의 5자산 1,620,960바이트는 공급자 체크섬과 원 metadata를
보존했다. [자료 판독](MATERIAL_PROCESS_DATA_KO.md)의 BAM 한 강판 6시편은 한 온도·
미파단이며 제조사 조성 선언의 시험 lot 대응이 미상이다. AHSS 두 연속 이력의
1,859행은 총 전류이며 독립 시편·유효 면적·농도 경계·D/포획/초기 조건이
미상이다. 원조건의 9경계 요청은 모두 `needs_data`이고 원 수치 호출은 0회다.
원관측·변환 행·상세 receipt는 `artifacts/material_process_data`에만 두고
[공개 집계](../research/material_process_data_results.json)에 포함하지 않는다.

실제 연계 후속 조건은 PI-X04의 assay/검열·수소 관측변환·공급자 정규화·원
배포 재현과 PI-X02/03/06의 사건/기간/조건·공동 의존 계약이다. 필요한 원 엔진의
정식 저장소/배포 URL·버전·라이선스·소스/자료 핀·의존성이 확인되기 전에는
주소를 추정하지 않는다. 고정 소스와 공개 집계가 존재해도 원 엔진을 별도로
설치해야 실제 계산을 재현할 수 있다.

## 감시·pool·지식·정상 열 경계의 실제 소유 API

다음 경계는 기존 계산 소유자를 직접 호출한다. 표의 확인 범위와 전체 기술의
자격 상태를 구분한다. 공개 집계에 상세 관측·receipt나 개인 source 경로를 넣지 않는다.

| 경계 | 실제 소유·고정 조건 | 확인 범위와 남은 제한 |
|---|---|---|
| monitoring 7 families | PMT 0.1.0, 28 Python source tree SHA `a8856f3e30f7bddf7539dbe0b74ea7d47ada4127f52f765bf61e4a134300fe0a`; 원 Monitor/factory/SignalBatch/features와 원 DPCA/neural/graph/diffusion | 선택 59 ID의 작성 3채널 smoke, 대표 수치/미상·창·holdout·seed·학습 상태 불변. 모든 형상·모수·공정 성능 또는 원 논문 실험 재현을 입증한 것은 아님 |
| liquid pool | DynamicLH2PoolX SDK 0.2.0rc2, commit `6a700e8e183039bbb81803bacc416f6c9117ed71`; 8 Python modules와 고지/선언 pin | spreading solid/water와 fixed-area solid의 동적 원 ledger. 지면 도달 유입·질량 수지는 연결하지만 jet/deposition·기상 경계·전체 에너지 원장은 미계산 |
| knowledge assistance | SAGA project/CITATION 2.0.1, loaded namespace 2.0.0, source/resource tree SHA `572eca2cd0bfae7363920a2680b2559a10a1d2797922fc084b447ed5d7545ffd` | 원 SQLite hybrid 검색·인용·HAZOP 준비도·무서비스 응답. 외부 추론·실제 안전 지식·HTTP 서버·현장 적용은 미검증이므로 partial |
| buried steady heat | 설치 ht 1.2.0의 `R_cylinder/S_isothermal_pipe_to_plane`, 기존 cooling과 실제 함수 source pin | 단일 긴 관의 등온 평면 정상 열 경계 2모드. transient/moisture·복수관·현장 보정은 미완료이므로 전체 soil coupling은 partial |

[감시](MONITORING_EXCHANGE_KO.md), [pool](POOL_EXCHANGE_KO.md),
[지식](KNOWLEDGE_EXCHANGE_KO.md), [매설 열](BURIED_THERMAL_KO.md)의 전체 입력과
실행 CLI·부분 실패·단위를 따른다. `station_exchange`는
[충전소 가이드](STATION_EXCHANGE_KO.md)의 원 StationNetwork 및 유한 full-reference
episode를 호출한다. full-reference의 질량/에너지 원장 부재는 needs_data이며,
능동 fault·HyRAM·충전소 전체 적합성 확인으로 확대하지 않는다.

앞선 네 family 경계에 dynamic_pca·neural 6종·temporal_neural 14종을 실제 추가했다.
일곱 family의 선택 59 ID, native 137사례에서 수치/미상29·인과/holdout88·창17·
seed20·CPU 상태 복구2를 확인했다. 156개 모두를 독립 수치 정답으로 취급하지 않는다.
신경망 receipt의 재해시 오염 10개와 기존 거부 8개도 별도 증거다.
classification·incremental·subsequence·engineering은 선택하지 않았으며, 원 registry의
다른 기법이나 실제 정상/고장 truth·성능 교정까지 지원한다고 표시하지 않는다.

### 실행 환경과 배포 경계

소유 프로젝트 간 의존성이 다를 때 별도 JSON worker를 사용한다. 감시는
Python 3.12/NumPy 2.3.5/SciPy 1.18.1/sklearn 1.9.1/PyOD 3.6.7의 별도 환경,
충전소는 원 SciPy <1.16 조건을 만족하는 NumPy 2.2.6/SciPy 1.15.3/fluids 1.3.1/
ht 1.2.0 환경으로 실행했다. 주 환경의 의존성을 이 두 worker 조건으로 덮어쓰지 않았다.
신경망 추가 환경은 torch 2.14.1+cpu, CUDA build=None이다. 명시한 작은 seed/epoch/
hidden/window·CPU thread 조건을 보존하며 학습 가중치나 pickle을 배포하지 않는다.
원 모델의 training 이후와 보정/평가 뒤 tensor·전처리 SHA가 달라지면 거부한다.
누락됐던 충전소 property table은 보존한 별도 snapshot에서 원 생성기와 CoolProp 8.0.0으로
생성했고 생성기·결과 테이블도 source pin에 포함했다. 원 소스를 수정해 호환성 문제를
숨기거나 서로 다른 property basis를 같은 문자열로 바꾸지 않는다.

SAGA worker는 pydantic/settings·OpenAI SDK·PyMuPDF의 별도 환경을 사용한다.
원 API 서버는 정적 UI 자산을 요구해 이번 source 묶음으로 import하지 못했으므로,
실제 지원 library 경로만 사용했다. 외부 API/LLM 호출은 없고 임시 DB는 원 source/DB
밖에 생성한다. 원 `rag → external_sources → indexer → pymupdf` 계보에서 PyMuPDF가
실제로 import됐다. 문서 추출 호출이 없다는 사실은 import 의존성의 라이선스를
없애지 않는다. SAGA의 MIT와 PyMuPDF의 AGPL/상업 조건을 분리해
[외부 고지](../THIRD_PARTY_NOTICES.md)에 기록했다. 이 프로젝트 배포에는 해당 환경을 넣지 않는다.

### 물리 결과에서 감시·위험으로 넘어가는 의미

pool `to_signal_frames`는 같은 RunContext와 검토한 boundary를 요구한다.
source event/port·ENU·clock offset과 evidence를 유지하며 누적 증발에서 구한
구간 평균 질량률만 내보낸다. 초기 미계산 평균은 None/invalid다. 감시에는 별도
population/run/split 정책을 추가해 training/calibration/evaluation을 구성해야 한다.
계산된 물리 궤적을 정상 현장 관측이나 고장 truth로 자동 표시하지 않는다.

감시 점수/threshold/경보, 지식 검색 점수/후보 준비도, 정상 열률은 서로 다른
의미다. 이 값에서 HEP나 기간 고장확률을 생성하지 않는다. 기존 HRA/FTA/ETA·
기간 위험 소유 API로 전달하려면 사건 endpoint·조건·기회·기간·생존·의존과
중복 방호를 따로 검토해야 한다. 지식 후보는 advisory/pending이고 운영 제어로
자동 적용하지 않는다. 미상·timeout·source 변화·부분 prefix·지원 밖 값은 원 상태로 보존한다.

### 후속 세션의 검증 범위

원 source/version/spec·라이선스·property table·실제 import 위치와 전후 불변을
다시 확인한다. 해시는 기록 일치 검사이며 서명·원 실행 진위·물리 자격의 증명이 아니다.
감시의 단일 채널 lag_ridge와 CBLOF 실패는 PI-X05에, station/pool의 물리 원장·경계는
PI-X01/03에, 지식의 독립 library/API/권한·의존성 분리는 PI-X07에 요청한다.
매설 정상 열의 검증을 유지하면서 층별 시간 축열은 아래 원통 경계로 연결한다.
수분·일반 지표 단면·유체 되먹임과 실측 조건은 별도의 적용성 검토가 필요하다.
전체 계획 상태는 STATUS와 기술 카탈로그를 따른다.

## 원통 과도 열과 실제 토양 관측의 연결 경계

`pipe_soil_transient`는 기존 원 `thermal.transient_conduction_1d`의 원통 기하,
다중 물성·층·비선형 축열·시간 경계를 호출한다. 동일한 76 Python 파일의 Mat
source pin `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`와
원 라이선스·고지·자원을 보존한다. 별도 FV solver나 열물성 적분 모델을 복제하지 않는다.
새 adapter는 공통 자산·위치·event·clock과 검토한 material/boundary evidence,
원 모델/schema/소스, 모든 원 입력/기본값을 정확히 결합한다.

원통 셀의 초기 평균과 출력 평균 K, 실제 반경/길이와 면적, 두 경계의 고체
유입 양수 부호를 유지한다. 가변 Cv 적분의 독립 최종 축열은 원 substep 누적
경계 J와 비교하고, 각 sample의 W는 반 셀 저항·표면/셀 K와 활성 경계에
부합해야 한다. 원 computed와 reported energy error·step/반복 진단의 모순,
native 추가 입력도 저장 검증·신호 export에서 거부한다.

[원통 과도 가이드](PIPE_SOIL_TRANSIENT_KO.md)의 13사례·원10호출·34개 작성 수치/계약
기준과 재해시 의미 오염12개는 소프트웨어 증거다. 원통 Bessel 모드의 공간/시간
오차 감소, 정상 log profile과 ±열유속 적분을 실제 토양의 예측 정확도로 세지 않는다.
원 영 순열량의 상대 판정 한계·실행 중 지원 이탈은 원 상태로 보존한다.
이쪽 최종 감사 통과만으로 원 out_of_scope를 승격하지 않는다.

[실제 토양 자료](SOIL_DATA_KO.md)는 두 source의 선택 7자산을 공급자 MD5로
확인했다. 시추공 processing clock·수직 depth·처리 관측과 피복 실험의 일
극값·미상 단위를 보존한다. source reader의 21계약·5변조 거부와 신호 재생은
배관 열모델 물리 검증 0회와 구분한다. source 셀/배열·method 파일·임시 신호와
상세 receipt는 배포 밖에 두고 공개 집계에는 출처·핀·집계·제한만 남긴다.

원통 바깥 면과 실제 지표 평면은 다른 기하다. 수직 관측을 원통 far-field로
자동 투영하지 않는다. 지표/복수관 2D 단면을 연결할 때도 cavity의 위치·다각형
경계와 원 관측의 좌표·처리 시계·센서 위치를 별도로 검토해야 한다.
현재 원통 경계는 유한 접촉 저항·수분 이동·지하수/상변화·유체 되먹임을
모델링하지 않는다. PI-X04의 계면·구간 에너지/partial prefix와 원 배포 인수
조건, 별도 단면 solver의 기하/격자 품질 계약을 후속 범위로 유지한다.

## 제한된 FiPy 지표·관 공동 단면의 소유 경계

`soil_cross_section`은 optional FiPy 4.0.3의 실제 Mesh2D·확산/축열·SciPy 해석을
사용한다. 현재 지원은 수평 지표와 실제 다각형 cavity를 가진 **직교** 2D 토양
mesh다. 복수 cavity의 열 상호작용을 계산하지만 일반 비직교·곡면·모든 배관
기하의 자격은 미완료다. 전체 soil coupling과 buried multi 기술은 partial이다.
지표면과 cavity를 포함한 기하를 원통의 등가 반경으로 환산하지 않는다.

하나의 backend 경계에 vertex/face/cell adjacency·group face 순서·extrusion 길이,
상수 k/Cv·온도 지원·초기 cell 평균과 face별 시간 경계, 공통 clock/offset·
component/evidence를 보존한다. 단계별 저장량과 경계 BE 누적 J를 비교하고
국부 implicit cell equation·온도 지원도 검사한다. 저장 결과 검증은 같은 FiPy에서
기록된 온도장의 gradient/face 상태·열류를 재구성하며, 바깥 digest만 맞춘 잘못된
경계 W/표면 K를 수용하지 않는다.

감시 신호의 boundary power는 이전 implicit step에 적용한 BE W이고 초기에는
None/invalid다. previous-knot hold의 knot에서는 왼쪽 구간 적용 값이며 새 경계
직후의 순간 물리 열률이 아니다. 원 누적 J를 유지하고 partial prefix는 명시 opt-in을
요구한다. 물리 측정이나 HEP·고장확률로 이름을 바꾸지 않는다.

직교 서비스의 감사 통과를 비직교 지원 근거로 사용하지 않는다. caller 허용각과
별개인 내부 상한을 유지하고, skewed mesh의 무보정 열류 오차와 correction 경로의
호환/물리 문제를 미완료 증거로 남긴다. 격자 품질·직교성·conjunction·경계 위치와
실제 선형/과도 해·세분 오차를 확인한 뒤 지원을 확장해야 한다.
[단면 가이드](SOIL_CROSS_SECTION_KO.md)와 PI-X01/04의 인수 조건을 따른다.

새 주 환경 의존성은 optional `[soil]`의 FiPy 4.0.3과 부가 설치 패키지이며 기존
NumPy/SciPy/Matplotlib은 바꾸지 않았다. backend의 실제 코드/버전 pin과 NIST 및
구성요소 고지는 [외부 고지](../THIRD_PARTY_NOTICES.md)에 있다. 소유 solver·mesh 구현,
라이선스 자원·원 실험 자료·환경 바이너리를 이 패키지에 vendor하지 않는다.
