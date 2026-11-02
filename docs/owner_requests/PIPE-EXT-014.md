# PIPE-EXT-014 — 펌프 정지 동특성·액체 MOC 동시 결합 계약

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: caller-owned pump and drive transient solver, pipeline-integrity-toolkit liquid MOC coupling boundary

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.pump_transient`: `PumpCoastdownRequest`, `PumpTransientProvider`, `evaluate_pump_coastdown`, `verify_pump_coastdown_record` — [소스](../../src/pipeline_integrity/pump_transient.py), SHA-256 `10c8fc2bc3e45da395324bd20212b441f96f37fd81f494d36e3184e6a29b88a2`.

## 이 저장소에 이미 작성한 범위

- A source-bound adapter validates owner speed/flow/head/power histories, power integrals, rotor kinetic energy, energy closure, convergence and replay. It does not yet implement direct simultaneous liquid-MOC coupling.

## 요구 동작

- Provide a versioned pump/drive transient API for trip and coastdown with signed four-quadrant flow, head or torque, angular speed, rotor inertia, drive torque, hydraulic torque, loss torque, check-valve state and convergence histories; do not derive time response from a static affinity point alone.
- Couple the owner pump equations and the liquid MOC characteristic/node equations at the same accepted time level so flow and head are solved together rather than replaying a one-way prescribed trace as a coupled solution.
- Return source-bound power histories and an independently checkable rotor energy ledger with initial/final 0.5*I*omega^2, drive work, hydraulic work, dissipated energy and bounded numerical adjustment.
- Detect and type unsupported cavitation, column separation, reverse rotation, motor regeneration, check-valve impact and operating-map extrapolation without clipping them into the supported domain.

## 필요한 수락 증거

- Zero-inertia/quasi-steady, no-hydraulic-load, constant resisting torque, energy-conserving rotor and pump-trip limiting cases agree with independent analytic or arithmetic references across time-step refinement.
- The simultaneous MOC/pump node residual, network volume balance, rho*g*Q*H power identity, torque-power identity and full rotor energy ledger each meet caller-declared tolerances without excessive numerical adjustment.
- Independent pump coastdown or trip experiments provide synchronized speed, pressure/head and flow with inertia, fluid properties, sensor calibration and clock uncertainty; calibration and held-out validation runs remain separate.
- Changing pump curve/map, inertia, drive/control logic, check-valve rule, fluid property, clock, source or solver version invalidates dependent receipts.

## 공통 계약

- signed flow/head/torque, angular speed, absolute pressure and energy sign conventions preserved
- asset/scenario/clock/pump/drive/fluid/source/configuration and evidence bound to the response
- unknown/unsupported/nonconverged stays explicit; no inferred cavitation safety, field accuracy or operating authorization
- pump owner implements dynamics and drive physics; the consumer validates exchange and MOC coupling

## 인수인계

[상세 매뉴얼](../PUMP_TRANSIENT_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `transient.liquid_moc`, `equipment.pump_system`.
