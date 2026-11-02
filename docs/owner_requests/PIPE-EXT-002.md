# PIPE-EXT-002 — 벤트와 PSV 사건 적분

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: lh2dt_models

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.tank_breathing`: `TankBreathingRequest`, `TankBreathingWindow`, `TankBreathingVent` — [소스](../../src/pipeline_integrity/tank_breathing.py), SHA-256 `9a70eb1153eb83d7ce9068502213bf86f70f8ef906f7a7e68e1c36414de5cb36`.

## 요구 동작

- Update PSV latch only at accepted events/steps; trial RHS evaluation must have no permanent latch mutation.
- Return each port integrated mass/enthalpy and separately signed/gross phase-transfer ledgers on the same native steps.
- Declare multi-component headspace and entrained-liquid support instead of treating bulk outflow as pure vapor vent.

## 필요한 수락 증거

- Repeated replay and rejected-step evaluation do not change latch state.
- Opening/reseating events and port/phase budgets converge under time refinement.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../TANK_BREATHING_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `tanks.breathing`.
