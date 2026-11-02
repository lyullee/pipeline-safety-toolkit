# PIPE-EXT-005 — 검열·공간지지·노출시간 모니터링

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: process-monitoring-toolkit

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.external_sensing`: `ExternalSensingRequest`, `ExternalChannel`, `SensorFootprint` — [소스](../../src/pipeline_integrity/external_sensing.py), SHA-256 `495bf2bf6c361cb8ad3ded8a13d599fbb29480d8755b4ecd2f485c8d463358be`.

## 요구 동작

- Receive spatial footprints, source/report clocks, exposure supports and mapping uncertainty alongside exact values.
- Represent non-detect, saturation, missing and invalid observations explicitly without zero-fill or time compression.
- Keep species and point concentration, path/pixel column and DAS strain definitions separate.

## 필요한 수락 증거

- Censored/missing values never become normal exact observations or leak probabilities.
- Train/calibration/evaluation preserve original event/device/site grouping and reject incompatible geometry/species/unit support.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../EXTERNAL_SENSING_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `leak.external_sensing`, `instrument.sensor_faults`.
