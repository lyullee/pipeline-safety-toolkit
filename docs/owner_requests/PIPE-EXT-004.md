# PIPE-EXT-004 — 상변화·원유·고형화의 물성 공급 경계

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: lh2dt_models, ThermoPack source-build provider

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.phase_change_exchange`: `PhaseChangeRequest` — [소스](../../src/pipeline_integrity/phase_change_exchange.py), SHA-256 `9f467f00524d299f9b232dd3c4f96edcc36d6f71b3fc30ecb358f7c40f76ffd6`.
- `pipeline_integrity.heated_transport`: `HeatedTransportRequest` — [소스](../../src/pipeline_integrity/heated_transport.py), SHA-256 `7e8c77fb0c4a92761b3d8e92f2347dd544ce8eff7222e491e9b051eae18f5e7a`.
- `pipeline_integrity.deposition_risk`: `DepositionRequest`, `DepositionEnvelope` — [소스](../../src/pipeline_integrity/deposition_risk.py), SHA-256 `643a80298ce0ceaccb4d5daf125fdd2e086768bd5566c43056299521b975e6fd`.

## 요구 동작

- Expose composition/activity/inhibitor/wax characterization with equilibrium uncertainty and source-build capability identity.
- Implement missing solidification, distributed conjugate heat/viscosity or deposition kinetics in the appropriate original solver, with separate support flags.
- Do not advertise PyPI ThermoPack2.2.3 as hydrate-capable; reject missing native methods before object execution.

## 필요한 수락 증거

- Phase/reference-state and energy consistency, supported-domain cases and original source/binary fingerprint evidence.
- Equilibrium exposure is not reported as deposited mass, continuous duration or plugging probability.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../DEPOSITION_RISK_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `thermal.phase_change`, `thermal.heated_oil`, `transport.hydrate`.
