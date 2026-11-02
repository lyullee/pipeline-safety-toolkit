# PIPE-EXT-006 — 위치 예측과 센서 설계 원자료

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: lh2dt_models, process-monitoring-toolkit

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.inverse_localization`: `LocalizationRequest`, `LocalizationProvider` — [소스](../../src/pipeline_integrity/inverse_localization.py), SHA-256 `b7e3f30cc7efeccc704ada3f303ee036f8e003ea2de5b3f66b9a0851fb7da254`.
- `pipeline_integrity.sensor_placement`: `SensorPlacementRequest`, `DesignLinearization`, `JointDesignNoise` — [소스](../../src/pipeline_integrity/sensor_placement.py), SHA-256 `4071c6c0e02f25ff790d1c868d5000afb7e83e82db0843101c11dcb8952d30c9`.

## 요구 동작

- Export original physical candidate predictions and reviewed local Jacobians with exact observation/parameter ordering, scales and native evaluation/configuration identity.
- Preserve covariance, model discrepancy, bias/clock corrections and source qualification without assuming independence.
- Return native unknown/nonconverged status and prevent finite-menu optimum from being called global physical optimum.

## 필요한 수락 증거

- Candidate/model/source/window mutations invalidate predictions and sensitivity receipts.
- Rank-deficient/indistinguishable alternatives and non-SPD/noise-unknown cases remain explicit.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../INVERSE_LOCALIZATION_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `leak.inverse_localization`, `leak.sensor_placement`.
