# 가열 액체 수송의 온도·점도·압력손실 연계

구현·시험 시나리오 작성 완료, 누리온 수치 검증 대기. 로컬에서는 Ruff와 컴파일만 확인한다. 계산 예제가 실행되었다거나 수렴·독립 검증을 통과했다고 주장하지 않는다.

## 현재 지원 범위

`heated_transport`는 기존 `models.thermal.cooling`, `models.hydraulics.liquid_pressure_loss`와 `thermo.CheckedProvider`를 연결한다. 마찰계수는 기존 hydraulic owner가 Fluids를 호출해 구하며, 열경계는 기존 일정 cp plug-flow 냉각/가열 해를 이용한다. 물성은 기존 비캐시 CoolProp 공급자 또는 사용자가 제공하는 동일 PropertyProvider 계약을 사용한다. 새로운 점도식·원유 조성·pseudo-fluid·표준 계수표를 내장하지 않았다.

현재는 양의 지정 질량유량, Newtonian 단상 액체, 정상 상태, 동일 관경의 명시적 셀 행진을 지원한다. 각 셀은 입구 밀도·점도·cp를 고정해서 기존 열 및 압력손실 모델을 평가한다. 셀 출구의 압력과 엔탈피로 물성 공급자를 다시 호출하고 다음 셀 입구에 연결한다. 따라서 상류 가열에 따른 하류 점도/압력손실 변화가 실제로 연결되지만, 셀 내부의 연속적인 완전 연성이나 양방향 비정상 모델은 아니다. 셀 길이와 온도 변화에 대한 mesh 수렴 검토가 필요하다.

원소유 `lh2dt.Pipe`도 조사했지만 수소 속성 facade, 별도 입구 물성 고정 및 마찰 처리 계약을 가지므로 일반 가열 원유에 재사용하지 않았다. 원유 물성이 필요하면 사용자가 검증하고 공개 조건을 확인한 공급자를 연결해야 한다. 예제 Water는 API 연결 시연용이며 원유의 대리 물성이 아니다.

## 입력과 공급자 계약

`HeatedTransportCell`에 길이 m, 내경 m, 거칠기 m, 국부손실 계수, 출구-입구 높이차 m, 열전달 conductance W/K, 주변 온도 K, 유체에 직접 들어오는 heater W를 지정한다. 마지막 세 열경계는 미상일 때 None, 알려진 무열전달/무가열은 0을 쓴다. conductance는 유체와 지정 주변 사이의 전체 계수이며, 단열재나 지중 전도 계산을 여기에서 중복하지 않는다. 기존 지중 열모듈 또는 외부 검토 결과를 해당 셀의 근거로 연결할 수 있다.

`HeatedTransportRequest`에는 inlet StateRequest, 질량유량 kg/s, 사용자가 검토한 압력·온도 범위, 중력, 전이영역 정책, 마찰 모델 선택과 수치 예산을 지정한다. 압력은 모두 절대압이고 유량은 inlet에서 outlet으로 흐르는 양수다. inlet 상태는 P/T 또는 P/Hmass의 단상 요청이어야 한다. 공급자는 모든 계산에서 같은 fluid/backend와 엔탈피 기준을 유지하고, 압력·온도·엔탈피의 독립 입력을 보존해야 한다. cp와 점도가 누락되면 계산하지 않는다. 현재 phase='liquid'만 허용하며 두 상, 기체, 초임계 및 비Newtonian 유체는 지원하지 않는다.

기본 `property_provider_kind='coolprop'`는 `CoolPropProvider(use_cache=False)`를 사용한다. 다른 객체를 조용히 대신 주입할 수 없다. `custom`은 `resolve(StateRequest)->ThermodynamicState`와 `runtime_identity()->dict`를 제공해야 한다. runtime identity에 명시적인 enthalpy reference_basis를 포함하고, 공급자 소스 파일을 해시할 수 있어야 한다. 데이터표·외부 바이너리·다른 모듈을 참조하는 공급자는 그 의존성 및 데이터 체크섬도 runtime_identity에 포함해야 한다. 공급자는 요청한 backend 의미를 실제로 준수해야 하며, 현재 공유 StateRequest가 지원하지 않는 backend나 조성 계약은 공유 소유 모듈 확장이 필요하다.

## 사용 절차와 추적성

1. 실제 물성·기하·열경계 근거를 `EvidenceRef`로 작성하고 context에 넣는다.
2. 네 `REVIEWS`에 검토 결과 및 실제 근거 ID를 연결한다. 셀/물성/검토가 선택한 모든 근거는 실제 context의 고유 멤버여야 한다.
3. 검토 시점의 `native_backend_metadata(request, property_provider=...)`를 owner_pins로 저장한다. CoolProp 바이너리/버전/리비전, 공급자 기준, 비캐시 입구 상태 기준 마커, Fluids와 공유 열·유압·물성 adapter 소스, 이 adapter 해시를 기록한다.
4. 최종 요청으로 `boundary_metadata(request)`를 만들고 context.metadata['heated_transport_boundary']에 보관한다. 자산·이벤트·시계·두 포트·snapshot 시각·요청 전체를 바인딩한다.
5. `evaluate_heated_transport(request, context, property_provider=...)`를 호출한다. 미검토 또는 누락 경계는 needs_data이며 물성/native 함수를 부르지 않는다. pin이나 실제 context 불일치는 오류다.
6. `verify_heated_transport_record`는 봉인을 확인한 후 실제 모델과 공급자로 재실행한다. custom 공급자는 검증할 때도 같은 객체 계약을 제공해야 한다.

## 셀 장부와 압력 일

기존 열 owner의 `heat_loss_w` 부호를 바꾸어 유체 유입 열로 기록하고 heater를 합친다. 기존 hydraulic owner에 셀 입구의 실제 체적유량·밀도·점도를 전달한다. hydraulic owner의 요구 입구-출구 압력을 입구 절대압에서 빼고, 엔탈피는 유입 열/질량유량에서 중력 위치에너지 증가를 뺀 값만큼 갱신한다. 출구를 P/Hmass로 다시 flash한다.

이 축약 에너지 방정식은 운동에너지 변화를 생략한다. 실제 입출구 밀도에서 속도와 운동에너지 flux 변화를 계산해 셀 및 전체에 대해 사용자가 정한 `maximum_omitted_kinetic_energy_rate_W` 이하인지 확인한다. 생략량을 0으로 감추지 않으며 full_energy_residual_W에도 포함한다. 기존 일정 cp 열 owner의 예측 온도와 실제 출구 flash 온도의 차이는 별도 closure error로 남기고 사용자 한계를 넘으면 diagnostic_rejected다.

엔탈피에는 이미 유동의 p/rho 일이 포함된다. 마찰 손실 power를 heater에 다시 더하지 않는다. 각 셀에서 내부에너지 flux 변화, p/rho 유동 일 flux 변화, 엔탈피 flux 변화와 항등식 잔차를 기록한다. native dissipated_power는 별도 기계 손실 진단이다. 중력 energy flux와 정수압 항도 분리한다. 압력 총강하는 높이차 때문에 음수가 될 수도 있으므로 마찰 소산과 동일시하지 않는다. 모든 장부는 정상 상태 power W이며, 운영 시간을 가정해 J로 바꾸지 않는다.

질량유량은 외부에서 지정한 정상 경계이므로 모든 셀에서 동일하며 mass_flux_residual은 0이다. 이 값은 유동 질량 보존 방정식을 독립적으로 푼 결과가 아니다. 펌프 동력, 관내 저장량 변화, 압축성 압력파, 시간 누적 유출량을 예측하지 않는다. 관경이 달라지는 인터페이스는 추가 운동량/운동에너지 owner가 필요하므로 거부한다.

## 오류·검증 정책

입출구 상태가 사용자 온도·압력 support를 벗어나거나, 전이영역에서 기존 hydraulic owner가 결과를 보류하거나, 물성이 다른 상으로 바뀌면 out_of_scope다. 부분 셀 결과를 완성 결과로 남기지 않는다. 수치/물리 모델 출력이 유한한지 검사하고 요청과 복사된 모델 입력도 재검증한다. 계산 전후 source/runtime/reference를 다시 비교한다. 수지·생략 운동에너지·온도 closure가 허용범위를 벗어나면 diagnostic_rejected다.

`tests/test_heated_transport.py`에는 검토 차단, unknown 열경계와 0의 구분, context/비유한 복사 입력, 물리 도메인, Water 가열과 점도 갱신, 상류 열이 하류 hydraulic owner 입력에 미치는 영향, 압력 소산의 중복 가열 방지, 관경 변화 거부, source pin과 기록 변조 시험을 작성했다. 아직 실행하지 않았다. 누리온에서 실제 CoolProp/Fluids 환경과 source pin을 보관하고 실행한다. 추가 qualification에는 셀 분할 수렴, 난류/층류/전이 정책, 가열·냉각·상변화 근접, 높이차, 물성 reference 변경, custom provider 데이터 변경 및 독립 측정 비교가 필요하다.

## 소유 모듈 확장 요청

공유 열·유압 owner에 상태 의존 계수의 보존형 연속 에너지·운동량 결합, 속도 변화와 관경 전환, 비정상 저장량, 열벽체의 시간 이력 및 펌프 경계 결합이 필요하다. 임의의 원유 모델을 추정해서 내장하기보다, composition/backend/reference 및 rheology 지원 범위를 갖는 property provider 계약을 먼저 확장해야 한다. 지중/단열 owner는 재사용 가능한 conductance와 적용 온도·기하 범위 및 evidence를 제공하는 포트를 유지해야 한다.

## 자료와 공개 조건

정적 교차검토 보강: 기존 유압 `liquid_pressure_loss`와 열 `cooling`의 실제 출력키·부호를 대조했다. `u + p/rho = h`의 cell 및 전체 유동 장부를 계산해 두고도 판정에서 빠뜨리지 않도록 두 분해 잔차를 실제 에너지 gate에 포함한다. 전체 internal/enthalpy/pressure-flow-work 변화도 별도로 반환한다. 마찰 소산을 별도 외부 열원으로 다시 더하지 않는다. 보강 시험은 작성만 했으며 수치·물성 계산이나 pytest를 실행하지 않았다.

원문·수치표를 복제하지 않고 문서·주석·합성 경계를 새로 작성했다. 사용자 데이터와 공급자의 재사용 조건은 EvidenceRef에 그대로 보존한다. CoolProp 및 Fluids의 실제 배포 라이선스·고지 조건을 충족해야 한다. 이 문서는 특정 규격 전체에 대한 적합성이나 원유 전체에 대한 물성 정확성을 보장하지 않는다.

공식 기술 자료: [CoolProp 상태 입력·상·기준 상태 API](https://coolprop.org/coolprop/HighLevelAPI.html), [Fluids 마찰계수 API](https://fluids.readthedocs.io/fluids.friction.html). 이 adapter는 해당 함수들의 구현을 복사하지 않고 기존 프로젝트 owner 경유로 호출한다.
