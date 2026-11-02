# Hydrate·wax 경계 공급과 조건부 시계열 비교

현재 상태는 **partial / 수치·native·현장 검증 대기**다. 코드와 HPC용 시험을 작성했으며 로컬에서는
Ruff·컴파일만 수행한다. pytest·수치 예제·ThermoPack native 계산을 실행하지 않았다.
`computed`는 선택한 경계와 표본의 조건부 비교 결과이며 hydrate/wax 모델의 독립 검증을 뜻하지 않는다.

## 기존 엔진과 공개 구현 조사

기존 `thermo.StateRequest/CoolPropProvider`와 `phase_change_exchange` 소스를 검토했다. 이 API들의
fluid state·수소 기액/열교환 계산을 hydrate 또는 wax의 고체 평형 모델로 취급하지 않는다.
CoolProp의 기액 상태값이나 단일 포화온도로 두 현상의 경계를 만들지 않는다.

공개 [ThermoPack 저장소](https://github.com/thermotools/thermopack)에서 hydrate 전용 Python API를
확인했다. 이번에 검토한 source commit은 `d68c794c7342bfc6938eb424a1fbb88b7780b738`이다.
검토한 [hydrate.py](https://github.com/thermotools/thermopack/blob/d68c794c7342bfc6938eb424a1fbb88b7780b738/addon/pycThermopack/thermopack/hydrate.py)와
[thermo.py](https://github.com/thermotools/thermopack/blob/d68c794c7342bfc6938eb424a1fbb88b7780b738/addon/pycThermopack/thermopack/thermo.py)의
공개 method signature를 연결했으며 수식·물성표·기본 파라미터 소스를 복사하지 않았다.
해당 commit의 [LICENSE](https://github.com/thermotools/thermopack/blob/d68c794c7342bfc6938eb424a1fbb88b7780b738/LICENSE)는 Apache-2.0이다.

중요한 배포 차이: PyPI에서 확인한 최신 distribution은 **thermopack==2.2.3**이지만 이 배포본에는
검토한 hydrate module/API가 없다. 3,497,217-byte Windows x64 wheel을 설치·실행 없이 ZIP으로 열어
확인했으며 SHA256은 `621643cf9d79c2ae676224f3073423aa35767471db9a9c047a491bb1a06b36e7`이다.
이 wheel의 metadata는 MIT classifier를 표시하며 최신 source LICENSE와 구분해야 한다.
이를 tested dependency나 hydrate 지원 package로 기록하지 않는다. 다른 OS wheel의 지원 여부를
이번에 별도 검증하지 않았다. 이 원본 wheel은 private 연구 캐시에 있고 배포 소스에 넣지 않았다.

**기본 dependency 추가 없음.** `thermopack>=2.2.3` extra도 추가하지 않는다. 현재 optional native
adapter는 검토한 hydrate API가 있는 원 source build를 별도 준비하고 그 실제 distribution version,
Python source/native binary fingerprint를 선택한 경우에만 사용할 수 있다. 원 소스 빌드·native 동작은
HPC 검증 대기다. 모듈이나 method가 없으면 native 객체 생성 전에 명시적 오류로 거부한다.

## 고정 유체·억제제·wax basis

`DepositionBasis`는 실제 molar composition과 그 정의, water activity의 supplied/native-model/unknown
구분, inhibitor mass fraction과 농도 기준, wax characterization SHA, evidence를 가진다.
조성 합이 1인지 확인하고 자동 정규화하지 않는다. None은 보존하며 미상 성분·activity model·억제제
농도는 계산을 보류한다. supplied activity와 native-composition activity는 동일하지 않다.
`native_composition_model`은 선택한 원 모델이 조성에서 activity 효과를 처리한다는 검토 경계이며,
이 모듈이 실제 activity 값을 계산했다는 뜻이 아니다.

hydrate와 wax는 서로 다른 `phenomenon`이다. wax는 검토한 wax characterization SHA가 필요하며
hydrate curve를 재사용할 수 없다. 현재 wax native solver는 연결하지 않았고 검토된 envelope 또는
명시적 외부 공급자만 받는다. component label·pseudocomponent·탄소수/융점 분포·분자량·조성 기준·
inhibitor 및 salinity 의미는 source/configuration/basis에 보존해야 한다. 고정 basis가 바뀌면
이전 경계 digest와 불일치하므로 재사용하지 않는다. 조성·activity·inhibitor 불확실성을 임의로
temperature error에 흡수하거나 zero로 두지 않는다. 별도 검토한 모델/공급자 경계가 필요하다.

## 입력과 interpolation

`DepositionRequest`는 asset/location/physical port, 분석 clock, 원 observation generation origin,
검토 근거와 source SHA를 포함한다. `RunContext.metadata['deposition_boundary'] = boundary_metadata(q)`로
exact 요청을 묶는다. 입력 origin을 measured로 승격하지 않으며 비교 결과 origin은 simulation이다.

`DepositionEnvelope`는 pressure knots(Pa absolute)와 boundary-temperature lower/upper(K), 고정 basis SHA,
provider source/version/configuration, interpolation 모델, boundary uncertainty definition, native receipt를
받는다. pressure knots는 하나의 branch에서 엄격 증가해야 한다. 정렬·중복 병합·다중 branch 선택·
extrapolation은 하지 않는다. pressure에서 temperature를 piecewise linear로 연결하는 모델만 지원하며
log-pressure interpolation으로 자동 전환하지 않는다. 검토한 최대 knot gap도 초과하면 보류한다.

현재 지원 안정영역 방향은 **boundary temperature 아래**다. 다른 방향·재진입·다중 equilibrium branch는
다른 검토 envelope/provider가 필요하다. 숫자와 단위가 맞는다는 이유로 유효한 평형이라고 추정하지 않는다.

각 `DepositionObservation`에는 정확한 report time, 유계 pressure interval, 유계 temperature interval,
valid와 evidence가 있다. standard uncertainty를 자동 k배하거나 독립 Gaussian 가정으로 바꾸지 않는다.
zero-width interval은 호출자가 명시한 정확값이며 미상 uncertainty를 0으로 대체한 것이어서는 안 된다.
현재 interval은 가능한 P/T 조합의 rectangular outer support다. 상관이 있는 실제 support의 상계로
검토할 수 있지만 posterior나 confidence level을 추정하지 않는다.

pressure interval의 양 끝과 그 안의 모든 knot에서 경계 lower/upper를 평가하므로 temperature 경계가
비단조인 경우도 처리한다. `temperature_margin_bounds_k = [T_low - boundary_high, T_high - boundary_low]`다.
전체 margin이 양수면 entirely_above_boundary, 음수면 entirely_below_boundary, 0에 닿거나 겹치면
boundary_or_uncertainty_overlap이다. 위쪽이라는 결과는 부식·침식·다른 고체 형성 등 안전을 보증하지 않는다.
관측 pressure interval 일부라도 지원 범위를 벗어나면 그 행은 out_of_scope이며 잘라서 계산하지 않는다.
미상·무효 행은 needs_data로 남긴다. 경계 uncertainty가 미상이면 전체 비교를 보류한다.

## 표본 exposure와 미지원 물리

entirely_below_boundary 표본이 이어지고 sample gap이 선언 상한 이하이며 report-time span이 선언된
minimum window 이상이면 `sampled_below_boundary_windows`에 기록한다. 무효·미상·지원범위 이탈·
boundary overlap·위쪽 표본·큰 gap은 window를 끊는다. 표본 사이의 linear time interpolation,
crossing time 추정, gap을 가로지르는 duration 적분은 하지 않는다.

sample_span_s는 제공된 표본의 시간 폭이며 실제 연속 residence/exposure duration이 아니다.
`continuous_exposure_duration_s=None`, `kinetics_calculated=False`, `deposited_mass_kg=None`,
`plugging_probability=None`, `field_or_code_acceptance=False`를 유지한다. nucleation induction time,
heat/mass transfer, crystal growth·agglomeration·wall deposition·shear removal, wax transport와
막힘은 별도 물리 모델이다. equilibrium margin이나 표본 window를 plugging 확률·작업 승인으로 쓰지 않는다.

## API 사용과 native 공급자

`assess_deposition(request, context, envelope=..., provider=...)`는 두 방식 중 하나를 선택한다.

- reviewed_envelope: `envelope_payload_sha256(e)`를 request에 선택하고 context boundary를 구성한 뒤
  `envelope_binding(q, c, e)`로 evaluation seal을 만든다. evaluation 필드를 payload 해시에서 제외하여
  순환 참조를 피한다. `examples/deposition_risk.py`는 original synthetic 경계/관측 예제이며 reviews=False가 기본이다.
- provider: `provider(q, c)`가 원 모델의 `DepositionEnvelope`를 반환한다. 비교 때 source/version/configuration,
  고정 basis·evaluation binding을 검증한다. 공급자는 실제 물리 모델·자료 범위와 오차를 책임진다.

`ThermopackHydrateProvider(ThermopackHydrateConfig(...))`는 optional source-build 연결이다. 명시한
EOS/mixing/alpha와 ordered component IDs로 원 `init_thermo`, `init_hydrate`를 호출한 뒤, 원 이름
`get_hydrate_apperance_curve`로 T/P 배열을 받는다. 설정을 기본 EOS로 대체하지 않는다.
pressure/temperature mapping support, 원 hydrate parameter reference, native 최대 phase 수와
boundary error bound·정의는 사용자가 명시한다. boundary error bound는 자동 추정하지 않는다.

이 경로는 H2O 성분을 명시한 저수분 gas와 **알려진 inhibitor fraction=0**, native-composition activity만
허용한다. upstream source의 물 수분 수치지원 guard(250e-6 mole fraction)를 동일하게 적용한다.
이는 물리적 hydrate 임계값이나 규격값이 아니다. supplied activity·염분·inhibitor 효과를 이 API에
임의 보정하지 않는다. 원 최대 curve point 수는 5000이며 비단조 pressure/중복/불완전 curve는 거부한다.
반환 순서를 정렬하거나 누락구간을 채우지 않는다. 원 경계 아래 안정영역·branch의 실제 적용성은
별도 review가 필요하다. native mapping에는 hard wall-clock timeout이 없으므로 HPC에서 격리된
process/job의 시간·메모리 제한을 적용해야 한다. 본 코드는 원 DLL을 실행·검증한 것으로 표시하지 않는다.

`thermopack_source_fingerprint(package_root)`는 제한된 package Python/native library 파일 해시를 만든다.
정확한 설치 version과 해당 fingerprint를 request에 넣고, config digest를 provider_configuration_sha256에
선택한다. native receipt는 source/binary pin, 원 model ID·호출명·설정·NumPy version을 기록한다.
EOS parameter는 원 binary에 남겨두며 source table을 복제하지 않는다.

## 재현성과 검증 인계

입력 row/knot, 비교 수, receipt node/depth/byte 예산을 확인한다. nested model_copy를 재검증하고
NaN/Inf를 거부한다. 모듈·공통 thermo/contracts 소스 SHA, Python/Pydantic version, 원 native receipt를
보존한다. `verify_deposition_record(record, provider=..., request=..., context=...)`는 seal 확인 후 동일 경로를
재실행한다. provider mode는 저장 curve를 native 실행의 대용으로 사용하지 않는다. rehashed margin/
qualification 변경도 현재 replay와 다르면 거부한다. 해시는 자료 출처와 실행의 독립 인증이 아니며,
다른 native 환경·비결정적 실행은 strict replay에 실패할 수 있다.

HPC 시험은 선형 경계의 analytic margin, 비단조 interior knot의 interval extrema, extrapolation 거부,
미상/무효/gap, boundary equality, wax basis 미상, 원 curve 순서, rehashed 변조, 조성 변경, 공급자
재호출과 실제 method signature의 authored test double을 포함한다. test double은 native 검증이 아니다.

## 소유 엔진 확장 요청

수신 대상은 공정/열역학 모듈 owner와 선택한 hydrate/wax 원 엔진이다. 외부 issue를 전송하지 않았다.

1. 검증된 hydrate API가 포함된 source build/release의 distribution/version·OS별 binary·license·source
   fingerprint를 명시하고 pinned subprocess API를 제공한다. API 없는 PyPI 배포본과 구분한다.
2. 조성/pseudocomponent·물 activity/salinity·inhibitor phase와 농도 기준·wax characterization의 strict
   SI 계약 및 model parameter/evidence/uncertainty 지원 범위를 공개한다. 미지원 입력을 보류한다.
3. phase branch·stability side·fold/re-entry·critical endpoint와 curve truncation을 원 receipt에 표시한다.
   자동 정렬/nearest branch 대신 single-valued envelope로 매핑 가능한 구간을 검토한다.
4. 공급 boundary의 오차/계산잔차·parameter sensitivity·time/space support를 전달하고 본 interval 비교와
   independent reference 재현을 검증한다. CoolProp fluid properties와 hydrate/wax model을 별도 소유한다.
5. kinetics·wall transport·deposition/removal·plugging는 별도 dynamic API와 질량/에너지 잔차, 독립
   hold-out process data가 확보된 경우에만 추가한다. equilibrium margin을 plugging probability로 변환하지 않는다.

이 확장과 native·현장 검증 전까지 broad transport.hydrate 항목은 partial로 유지한다.
