# PIPE-EXT-012 — 다품종 액체 배치 수송 원 해석기 계약

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: caller-owned multi-product transport solver, lh2dt_models thermal/property owner where applicable

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.batch_transport`: `BatchComposition`, `BatchInletStep`, `BatchTransportRequest`, `BatchTransportProvider`, `evaluate_batch_transport`, `verify_batch_transport_record` — [소스](../../src/pipeline_integrity/batch_transport.py), SHA-256 `3c07f4f6c73eadd4d3bf493c16918cf84674d414ec0f41072e33939588a4041d`.

## 이 저장소에 이미 작성한 범위

- A source-bound provider adapter now validates grids, composition closure, positive property fields, total/per-component ledgers, ledger-sum consistency and deterministic replay; no owner transport solver or independent product-batch validation is claimed.

## 요구 동작

- Implement the owner numerical model for multi-product advection, axial dispersion and interface tracking on the requested route and inlet schedule; do not relabel the EPANET dilute-water quality adapter as a product-batch solver.
- Return source-bound time-by-position component fractions, density, dynamic viscosity, temperature and absolute pressure in the exact declared layout, with property package/version/basis and any CoolProp or reviewed mixture-model provenance.
- Return total and per-component signed mass ledgers using initial + inlet - outlet - final + numerical_adjustment, and where thermal coupling is enabled return an independently checkable energy ledger without hiding closure in numerical adjustment.
- Preserve phase, pressure reference, flow direction, boundary interpolation, geometry, clock, source and convergence meaning; unsupported phase change, mixing rules, pig interaction or reversal stays explicit.

## 필요한 수락 증거

- Piston-displacement, zero/finite-dispersion, constant-property and conservative multi-component limiting cases agree with independent arithmetic or analytic references, with mesh/time-step refinement and residual histories.
- Every component and total mass ledger closes within caller-declared tolerances and component ledger terms sum to their total counterparts; energy closure is reported separately when heat transfer or variable properties are active.
- Held-out experimental or operational interface arrival and contamination-width observations quantify error and applicability by product pair and operating regime; calibration rows are not reused as independent validation.
- Changing source, geometry, inlet schedule, property model, boundary interpolation, grid or provider version invalidates replay receipts.

## 공통 계약

- SI units, absolute pressure, temperature and mixture/property basis preserved
- asset/scenario/clock/route/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred field accuracy or operating authorization
- original transport/property owner implements physics; pipeline consumer validates and records the exchange

## 인수인계

[상세 매뉴얼](../BATCH_TRANSPORT_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `transport.batch_interface`.
