# 동적 파괴 이력의 공급·표본 비교 경계

현재 상태는 **partial / 수치·원 공급자·현장 검증 대기**다. 코드와 시험을 작성했으며 로컬에서는
Ruff와 컴파일만 수행한다. pytest, native 계산, 수치 예제는 실행하지 않았다. `computed`는 검토한
공급 이력의 표본 비교를 수행했다는 뜻이며 연성파괴 진행/정지 해석기가 완성됐다는 뜻이 아니다.

## 기존 엔진 조사와 범위

실제 로컬 matintegrity의 `src/matintegrity/models/mechanics.py`와 capability catalog를 읽었다.
`fracture.stress_intensity`, Paris 성장 모델과 crack-arrest 관측 reader는 존재한다. 현재 확인한
소스에서 동적 가스 감압과 연성 균열 전파를 결합하는 propagation/arrest solver는 찾지 못했다.
원 소스의 공개 원격 주소는 추정하지 않는다. 기존 `fracture_exchange`는 개시·성장·사용자 경계의
별도 API이며 정적인 Mode-I 저항을 동적 정지 저항으로 바꾸지 않는다.

이 모듈은 기존 `FractureIdentity`를 재사용한다. 압력·균열 위치·속도·온도·동적 energy release·
저항을 원 공급자가 동시 계산하거나 검토한 이력으로 제공하는 계약과 비교만 담당한다.
정적 KI, KIC, Charpy 관측, 기계 load-displacement 또는 FAD 통과에서 동적 arrest를 추정하지 않는다.
새 가스 감압식, crack-speed 법칙, 규격표, 경험계수나 재료 하한을 내장하지 않았다.

## 입력 경계

`FractureArrestRequest`는 asset/location/flaw/material condition/clock/evidence에 추가로 event,
coordinate frame, crack path, 재료 방향, 가스 조성·EOS basis, outer diameter와 wall thickness(m),
space/time/temperature 지원 범위를 받는다. propagation_direction은 path coordinate의 증가/감소를
명시한다. 이는 균열 속도와 재료 압연 방향을 서로 같은 것으로 취급하지 않는다.

`ArrestTrajectory`의 모든 `ArrestPoint`는 다음 동시 값을 제공한다.

| 값 | 단위와 의미 |
|---|---|
| time_s, crack_path_position_m | 원 분석 시계의 시간 s와 등록된 path 좌표 m |
| local_pressure_pa_abs | 균열 위치의 동적 local absolute pressure Pa |
| temperature_k | 해당 위치·시각 재료/모델 적용성을 검토한 K |
| propagation_velocity_m_s | 선언 방향의 비음수 균열 전파 속도 m/s |
| driving_energy_release_j_m2 | 원 동적 해석의 신생 균열 면적당 구동 에너지 J/m² |
| dynamic_resistance_j_m2 | 해당 재료·방향·온도·속도에서의 동적 저항 J/m² |
| arrest_pressure_envelope_pa_abs | 같은 상태의 검토된 동적 정지 압력 envelope Pa absolute |

구동력과 저항은 `new_crack_area_basis_id`로 같은 projected/single-face/both-face 면적 정의를
가져야 한다. 단위 J/m²만 같다고 호환되지 않는다. `pressure_envelope_definition_id`는 원 envelope의
물리적 정의·적용 상태를 식별한다. KI를 제곱해 동적 J로 변환하거나 source receipt의 다른 시간
압력과 저항을 맞춰 끼우지 않는다. 모든 항목은 명시적 값 또는 None이며 하나라도 미상/무효이면
전체 비교를 needs_data로 보류한다. 미상 재료 방향·감압 source·형상·EOS·물리 basis도 보류한다.

네 가지 applicability review는 동적 coupling, 재료/방향/온도/속도 저항, 시공간 형상, 비교 기준의
의미를 근거와 함께 검토한다. 검토 플래그는 실제 model qualification 증명이 아니다. 입출력 source
identity는 asset/event/path/material/orientation/clock과 일치해야 하며 원 origin도 바꾸지 않는다.
계산된 비교 결과 origin은 simulation이다. 원 trajectory의 origin은 별도로 그대로 남긴다.

## 공급 방식

`assess_fracture_arrest(request, context, trajectory=..., provider=...)`는 둘 중 하나만 사용한다.

1. `source_mode='reviewed_trajectory'`: 호출자가 검토한 `ArrestTrajectory`를 전달한다.
   `trajectory_payload_sha256(p)`는 evaluation seal을 제외한 payload를 선택한다. 이 SHA를 request에
   넣고, context metadata를 구성한 다음 `trajectory_binding(request, context, p)`로 evaluation seal을
   완성한다. 이 순서로 해시의 순환 참조를 피한다.
2. `source_mode='provider'`: `provider(request, context)`가 원 동적 엔진을 호출해 같은 계약을 반환한다.
   provider source/version/configuration 및 decompression source SHA는 요청과 같아야 한다.
   `native_receipt`에는 원 엔진 입력·물성/EOS·경계조건·실행 환경·질량/에너지 잔차·수렴 기록을
   보존한다. 현재 저장소에 적합한 원 동적 엔진은 등록되지 않았으며 API가 그 엔진을 발명하지 않는다.

`RunContext.metadata['fracture_arrest_boundary'] = boundary_metadata(request)`는 전체 요청을 묶는다.
`examples/fracture_arrest.py`의 `make_case(reviewed=False)`는 직접 작성한 synthetic 동시 이력이다.
가스 물성·재료값·운전조건의 실제 calibration이 아니며 기본 검토 해제로 계산을 보류한다.

## 표본 비교와 미해결 구간

각 표본에서 `dynamic_resistance - driving_energy_release`, `pressure_envelope - local_pressure`를
기록한다. 두 margin이 비음수이고 supplied propagation velocity가 사용자가 선언한 stationary
tolerance 이하이면 해당 표본은 stationary-compatible로 표시한다. 물리적 stop 법칙을 새로 적분하지
않으며 원 공급자의 속도를 새로 계산하지 않는다.

연속된 compatible 표본 사이의 gap이 maximum_sample_gap_s 이하이고, path position excursion이
stationary_position_tolerance_m 이내이며, 표본 time span이 minimum_sampled_window_s 이상인 창을
`sampled_stationary_compatible_windows`로 반환한다. monotone path의 sliding window 비교이며 속도
적분과 위치를 임의로 일치시키지 않는다. 비교 기준과 허용값은 호출자가 선택하고 검토한다.
모든 표본의 time endpoints는 요청 time support와 정확히 같아야 한다. 원 순서를 정렬하지 않는다.
position이 선언 방향을 역행하면 거부하고, 지원 spatial/temperature 범위를 벗어나면 out_of_scope다.

**표본 사이에서 조건이 유지됐는지, 관성 에너지로 계속 전파하는지, 재시작하는지, 마지막 시각 이후
무슨 일이 생기는지는 이 비교로 증명할 수 없다.** 큰 sample gap은 창을 끊지만 작은 gap도 연속
보증은 아니다. compatible window가 없어도 propagation/failure를 증명하지 않는다. 따라서 항상
`dynamic_arrest_proven=False`, `continuous_hold_proven=False`, `field_or_code_acceptance=False`,
`failure_probability=None`을 유지한다. 이 결과를 설계 승인·방호 성공확률·FTA 기본사건 확률로 쓰지 않는다.

## 재현성과 검증

point 수와 receipt node/depth 예산을 복사/직렬화 전에 검사한다. nested model_copy 입력도 재검증하고
NaN/Inf·비표현적 margin을 거부한다. 결과는 원 request/context/trajectory/native receipt, 모듈·기존
fracture/material exchange 소스 SHA, Python/Pydantic runtime을 포함한다. provider는 원 native runtime
pin을 자신의 receipt에 추가할 책임이 있다. payload SHA는 source 선언의 결합이며 원 실험/엔진이
역사적으로 실행됐다는 독립 인증이 아니다.

`verify_fracture_arrest_record(record, provider=..., request=..., context=...)`는 seal 확인 후 재계산한다.
provider mode는 저장된 trajectory를 믿고 재사용하지 않고 원 provider를 다시 호출한다. 검토 이력
mode는 preselected payload와 evaluation binding을 검사한다. rehashed margins/자격 flag의 변경은
현재 동일 환경의 replay와 다르면 거부한다. 비결정적 공급자는 strict replay에 실패할 수 있다.

준비한 HPC 시험은 synthetic sample window, 낮은 에너지라도 속도가 큰 경우, 속도0이라도 위치가
움직이는 경우, 표본 gap·미상 입력·지원 범위 이탈·경로 역행, source/evaluation 변경, 점 예산,
rehashed qualification/score 변조, 원 provider 재호출을 포함한다. 아직 실행하지 않았다.

## matintegrity owner 확장 요청

현재 파일은 요청서 역할도 하며 외부 issue를 전송한 것은 아니다.

1. `fracture.dynamic_propagation` owner 모델에 gas composition/EOS/감압 boundary history, crack path와
   재료 방향, 온도·속도 의존 dynamic resistance 정의/자료, outer geometry와 구속 조건을 가진 strict
   SI 계약을 제공한다. 정적 initiation/fracture resistance와 모델 ID를 분리한다.
2. 동적 fluid-structure/fracture coupling의 질량·에너지·운동량 잔차, time/space convergence,
   kinetic energy 및 crack-growth area basis, 정지 후 재시작을 명시한다. pressure envelope만으로
   stop을 선언하지 않는다. 혼상/열역학적으로 미지원인 상태는 보류한다.
3. 원 registry에 typed native time×space trajectory와 source/environment/schema pin을 공개하고,
   이 모듈의 ArrestTrajectory 변환을 round-trip 검증한다. 공급되지 않은 time/space point를 조용히
   보간하거나 서로 다른 clock의 압력·저항을 결합하지 않는다.
4. sampled comparison과 연속 interval bound/validated arrest conclusion을 별도 필드로 제공한다.
   interval bound가 있다면 그 근거·오차·지원 영역을 검증하고 본 모듈의 flag를 자동 승격하지 않는다.
5. 독립 full-scale propagation/arrest 실험과 material-specific dynamic tests의 출처·라이선스·방향·온도·
   기하·가스 조건을 보존해 calibration과 hold-out validation을 분리한다. 본 저장소에는 재사용 조건이
   확인되지 않은 규격/경험계수/표를 옮기지 않는다.

수락 기준은 원 native solver 실제호출·잔차/수렴 audit·지원범위/미상 보류·독립 검증 evidence다.
기존 crack-arrest machine 관측 reader의 수치를 임의로 KIC 또는 배관 arrest pressure로 바꾸지 않는다.
이 확장과 검증 전까지 broad structural.fracture_arrest 항목은 partial로 유지한다.
