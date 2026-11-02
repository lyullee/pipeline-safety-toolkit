# 비등온 가스의 유한 저장량·벽체·압력 네트워크 어댑터

상태: 구현·시험 시나리오 작성 완료, 누리온 수치 검증 대기. 이번 작업에서는 pytest, owner smoke, 수치 계산 또는 벤치마크를 실행하지 않았다. 정적 검사 통과를 시간/공간 수렴이나 독립 물리 검증으로 표시하지 않는다.

## 실제 모델과 지원 범위

이 모듈은 기존 lh2dt의 `DynamicHEMPipe`, `dynamic_hem_pipe_node`, `DynamicNetworkSimulator`, `SteadyNetwork`를 실제 연결한다. 각 관 셀은 유체 질량·내부에너지와 벽체 온도를 갖는다. 유체와 벽체 사이 및 벽체와 주변 사이의 열전달이 유체 상태를 바꾸고, 질량/내부에너지로 복원한 EOS 압력이 매 RK4 단계의 관손실 네트워크에 전달된다. 따라서 등온 모델에 이름만 바꾼 기능이나 0D 탱크 재명명이 아니다.

다만 셀 사이 운동량 전달은 원소유의 준정상 half-pipe 관손실이다. 시간 의존 운동량 PDE, 음향파 전파, 충격파, line-pack 파동의 정확한 이동시간을 풀지 않는다. 저 Mach·작은 half-link 밀도/압력 차이의 유한 저장량 네트워크에 한정되며 전체 비등온 가스 과도해석 완성을 주장하지 않는다. 같은 셀 내부는 uniform 유체 상태와 uniform 벽체 상태다. 시간 간격·셀 분할 수렴을 별도 확인해야 한다.

## 공간 포트와 시간 경계

`GasThermalCell` 배열의 순서가 관의 공간 순서다. 각 셀 중심은 native dynamic boundary node, 인접 셀 사이 face는 native zero-storage junction으로 만든다. 셀의 입구/출구 native hydraulic half-component를 해당 face와 연결하므로 두 셀 사이 길이를 누락하거나 두 번 더하지 않는다. 양 끝은 지정 P/T reservoir다. 내부 face 압력과 엔탈피는 매 RK stage 원소유 네트워크가 푼다.

각 셀에는 독립 inlet/outlet material port ID가 있고, 내부 face namespace는 예약되어 있다. 자산·이벤트·시계·시작 시각·유체/기준·기하·벽체·포트·운전 구간 전체가 request digest와 `boundary_metadata`에 묶인다. `RunContext.metadata['gas_thermal_transient_boundary']`는 최종 요청으로 작성해야 한다.

`GasThermalWindow`는 일정 reservoir P/T의 물리 시간 구간이다. 구간이 바뀔 때 native 유체/벽체 보존 상태를 그대로 넘기고, 새 native simulator에 초기 상태로 전달한다. 경계 불연속을 한 RK 스텝 중간에 섞지 않는다. native 누적 장부에는 앞 구간 prefix를 더해 전체 이력을 유지한다. 실제 적분 시간, 요청 시간, native 종료 조건의 작은 미적분 꼬리 시간을 별도로 기록한다.

## EOS·전달 물성·열경계

현재 순수 HEOS 유체의 P/T 초기·reservoir 상태만 지원한다. 혼합물 조성 문자열은 거부한다. 원소유 HydrogenProperties facade는 generic CoolProp fluid 식별자로 사용할 수 있으므로 예제는 Nitrogen이다. 기존 gas_network._GasProperties를 신규 파일 내 subclass로 확장해 가스상 제한, rho/u 복원, 압력/온도 support와 변환 예산을 적용했다. 공유 gas_network 파일은 변경하지 않았다.

모든 RK 및 pressure trial 상태는 가스·supercritical gas/단일 supercritical 영역에 있어야 하고 quality가 없어야 한다. 단상 액체·응축/두 상·상분리는 이 adapter에서 거부한다. 점도는 원소유 CoolProp 전달 물성으로 읽으며 양수·유한해야 한다. 열전달 UA는 사용자가 근거와 함께 지정한다. conductivity/cp로 새로운 열전달 상관식을 추정하지 않는다.

P/H flash와 rho/u flash의 에너지 기준은 같아야 한다. 실행마다 새로운 native property 객체와 run-local P/T 및 P/H cache를 만들며 rho/u 복원은 cache하지 않는다. 계산 전후 CoolProp의 비캐시 H/S 기준 마커를 다시 확인하므로 이전 실행의 cache가 reference reset을 가리는 것을 방지한다. property_basis는 의미를 설명하는 사용자 식별자이고, 수치적인 reference 동일성은 실제 runtime marker와 source/binary pins로 확인한다.

사용자 EOS는 현재 공유 StateRequest/PropertyProvider의 rho/u 및 native transport 호출 계약이 충분하지 않아 이 파일에서 임의로 연결하지 않았다. 다른 EOS를 지원한다고 표시하지 않는다. 아래 owner 확장 요청을 먼저 해결해야 한다.

## 입력과 검토

셀 길이·관경·거칠기는 m, 질량은 kg, 내부에너지/누적 에너지는 J, 열용량은 J/K, UA는 W/K, 온도는 K, 압력은 절대 Pa, 시간은 s다. local loss는 무차원이다. fluid volume 기본값은 원통 기하 체적이다. `effective_fluid_volume_m3`로 override할 때는 셀 형상 근거와 다른 `fluid_volume_override_evidence_id`를 반드시 지정해야 한다. 해당 ID는 요청 `evidence_ids`에 선택되고 실제 context evidence에 존재해야 한다. override의 기하 대비 비율 기본 허용 범위는 0.5–1.5이며 `minimum_inventory_to_geometry_ratio`와 `maximum_inventory_to_geometry_ratio`로 사례별 검토 한계를 지정할 수 있다. 설정된 유효 체적은 이 범위 밖이면 요청 검증에서 거부된다. 결과의 `cell_fluid_volumes`는 기하 체적, solver에 사용한 inventory 체적, 적용 여부, 근거 ID, 기하 대비 비율을 함께 반환한다. 이는 독립 체적 측량이나 해당 volume이 압력·열 수지에 맞는다는 검증을 대신하지 않는다.

벽체 초기온도·주변온도·두 UA가 없으면 None이고 needs_data다. 알려진 단열·무열교환은 0을 명시한다. 네 `REVIEWS`에 실제 검토 결과와 근거 ID를 연결하고, 모든 cell/window/review 근거는 실제 context evidence 중 고유 멤버에서 선택한다. 미검토 입력은 native를 호출하지 않는다.

검토 시점의 `native_backend_metadata(request)`를 owner_pins에 저장한다. 기존 공통 source-tree/license/CoolProp binary/runtime receipt를 재사용하고, 실제 순수 유체의 H/S marker, 공유 property/gas adapter 및 이 구현의 해시를 추가한다. 평가 전후 pin이 달라지면 결과를 승인하지 않는다.

## 단계별 압력 서비스 검토

원소유 SteadyNetwork를 신규 파일 내 subclass로 호출한 다음, **모든 RK 단계와 수락 종료점**에서 다음을 검사한다.

- 압력 optimizer뿐 아니라 enthalpy 및 junction conservation을 포함하는 native 성공 판정.
- 모든 half-link의 native momentum residual 존재와 사용자 Pa 한계.
- 실제 CoolProp 음속으로 계산한 양 끝 속도/Mach.
- half-link 상대 압력/밀도 변화와 사용자 지원 한계.
- 생략된 kinetic-energy transport의 최대 W 예산.

실패 시 부분 궤적을 성공으로 남기지 않고 out_of_scope를 반환한다. 이 검사는 준정상 관손실의 서비스 범위를 제한하는 진단이며 음향 PDE를 구현하는 것은 아니다. 전달 운동에너지 flux를 0으로 감추지 않고 그 상한을 stage_service_records에 기록한다. state conversion 예산은 기존 wrapper가 세는 상태 flash 호출 기준이고, 음속 질의는 별도지만 network solve/셀 수 예산으로 제한된다.

## 실제 적분 장부와 수지

원소유 DynamicNetworkSimulator가 RK4 각 단계에서 적분한 cumulative_boundary_mass, cumulative_boundary_transport_energy, cumulative_boundary_ambient_heat, cumulative_boundary_energy를 그대로 보존한다. 종료점 유량에 기간을 곱해 적분량을 만드는 방식은 사용하지 않는다.

각 셀의 질량 변화와 native 경계 질량 적분을 비교한다. 유체 내부에너지와 벽체 열용량 곱하기 벽체 온도 변화의 합을 native 전체 경계 에너지 적분과 비교한다. 셀 내부 fluid-wall 열교환은 두 저장량 사이의 전달이므로 전체 셀 외부 열과 중복 합산하지 않는다. transport와 ambient 적분의 합도 전체 적분과 비교한다. 전체 네트워크의 질량/에너지 잔차에는 셀 예산과 별도의 global 절대/상대 예산을 적용한다.

엔탈피 유량에는 flow work가 이미 포함되어 있으므로 별도 pQ 일을 유체에 다시 더하지 않는다. 내부 face의 질량/엔탈피 전달은 반대 부호의 셀 경계 장부로 상쇄되며, zero-storage junction의 보존 오차는 native solve의 단계별 한계로 제한된다. 저장 운동에너지·위치에너지·높이차·축방향 벽체 전도는 이 모델에 포함하지 않는다. 관 고도나 축방향 전도가 중요한 경우 추가 owner가 필요하다.

## API와 출력 사용

`evaluate_gas_thermal_transient(request, context)`는 qualification, source/runtime/context, 초기 상태, 실제 cell fluid volume, raw native accepted-step 결과, cell/global 수지 및 stage service 진단을 반환한다. 수지를 넘으면 diagnostic_rejected이며, solver/EOS/service 범위 실패는 out_of_scope다. 모든 결과는 JSON 유한성 검사와 봉인을 거친다.

`verify_gas_thermal_transient_record`는 봉인만 확인하는 기능이 아니라 현재 원소유 모델로 실제 재실행한다. context/request를 명시적으로 비교할 수도 있다. `to_signal_frames`는 실제 matching context와 computed_diagnostic/replay 검증이 된 이력만 변환하고 셀별 압력·온도·질량 신호를 만든다. 신호 valid는 수치 이용 가능성으로 설비 안전 판정이나 실험 검증을 뜻하지 않는다.

예제의 두 셀 Nitrogen 운전은 새로 작성한 합성 경계다. 기본 실행은 미검토 needs_data를 만든다. reviewed=True는 시험 객체 작성 편의로 실제 현장 검토의 증거가 아니다.

## 누리온 시험과 확장 요청

시험은 검토 차단, context/비유한 복사 입력, 미상 UA 보존, 혼합물/포트/지원 domain 거부, 실제 multi-cell 질량·fluid+wall 에너지 장부, 단열 평형, 시간 구간 연속성, native 작업 예산·source pin·기록 변조와 신호 변환을 작성했다. 아직 실행하지 않았다. lh2dt가 없어서 skip된 native 검사는 통과로 계산하지 말고 누리온 환경에서 실제 원소유 패키지를 설치해야 한다.

후속 qualification에는 dt/공간 셀 수 수렴, reservoir 변경·역류, 온도에 따른 점도/압력손실 변화, 원소유 solver 오차 경계, 총열량의 독립 해석/실험 비교, 서로 다른 effective volume, 상경계와 Mach/domain 거부 시나리오가 필요하다.

원소유 확장 요청: 사용자 EOS의 rho/u↔P/h, energy-reference, viscosity·음속·필요 열전달 물성과 source/runtime/data identity를 하나의 검증 가능한 protocol로 노출해야 한다. generic fluid facade의 이름/계약, 사용자 조성 및 혼합물 변화를 먼저 정리한다. 시간 의존 운동량·음향 wave, 고도, 상변화/상분리, 축방향 벽 전도, 전이/압축성 관손실과 자동 adaptive time/mesh refinement는 원소유 solver에서 추가하고 이 adapter는 그 결과와 같은 수락 스텝 보존 장부를 연결해야 한다. RK trial 평가 중 영구 장치 상태를 바꾸지 않는 확정 계약이 필요하다.

공식 소스: [원소유 lh2dt_models](https://github.com/lyullee/lh2dt_models), [CoolProp 상태·상·기준 상태 API](https://coolprop.org/coolprop/HighLevelAPI.html#reference-states). 원소유 구현을 복제하지 않고 import/실제 호출하며 기존 MIT 및 의존성의 배포 고지 조건을 보존한다. 이 문서·주석·예제는 새로 작성했다.

정적 교차검토: 실제 `network.NetworkSolution`에서 `pressure_solver_success`는 optimizer 정지뿐 아니라 enthalpy 수렴과 junction 질량/에너지 closure를 모두 포함하는 값임을 확인했다. 각 성분 flag와 원소유 junction별 질량/에너지 허용오차도 stage 영수증에 따로 보존하고 서로 모순되는 성공 flag를 거부한다. `dynamic_network`가 RK4 stage의 signed mass/enthalpy 및 ambient heat를 같은 가중치로 적분한다는 실제 입출력 키를 대조했다. 추가 회귀 시험은 작성만 했으며 native/수치 실행으로 이 경로를 검증한 것은 아니다.


