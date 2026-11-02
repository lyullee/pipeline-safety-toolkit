# PIPE-EXT-001 — 비등온 가스의 일반 EOS와 분포 운동량

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: lh2dt_models, hydrogen-station-sim

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.gas_thermal_transient`: `GasThermalTransientRequest`, `GasThermalCell`, `GasThermalWindow` — [소스](../../src/pipeline_integrity/gas_thermal_transient.py), SHA-256 `741310009bbfb7c060ff44224f6d664e55570acd39e9405ed5b21f379a13d68d`.

## 요구 동작

- Expose a versioned pure/mixture/user-EOS interface with rho-u flash, enthalpy/internal energy reference identity and transport coefficients.
- Extend the original cell network with supported momentum/geometry terms and declared acoustic, elevation and heat-conduction domains rather than relabeling quasi-steady faces.
- Return accepted-stage and cumulative mass, transport-energy, ambient-heat and wall-energy ledgers with native failure reasons.

## 필요한 수락 증거

- Closed/adiabatic and steady limiting cases, time/cell refinement and independent source-qualified comparisons.
- Reference-state change consistency and explicit unsupported EOS/phase/domain refusal.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../GAS_THERMAL_TRANSIENT_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `transient.gas_nonisothermal`.
