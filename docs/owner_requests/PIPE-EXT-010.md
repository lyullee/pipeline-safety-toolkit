# PIPE-EXT-010 — 동압 취득·교정·센서쌍 근거 계약

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: caller-owned data-acquisition metadata provider, process-monitoring-toolkit ingestion boundary

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.pressure_wave_data`: `pressure_source_publication_metadata`, `qualify_pressure_pair`, `build_pressure_pair_trace_record`, `verify_pressure_pair_trace_record` — [소스](../../src/pipeline_integrity/pressure_wave_data.py), SHA-256 `fcf21bc4b94d2e1b52171896ebe04eaa681c28ac6fe29c583c9f31c660e4d215`.
- `pipeline_integrity.pressure_wave`: `PressureWaveRequest`, `bind_pressure_wave_pair_request` — [소스](../../src/pipeline_integrity/pressure_wave.py), SHA-256 `e4d4380e89aca7123d5580387d50e083833b1a0d8cac16093086d6de645b2e65`.

## 이 저장소에 이미 작성한 범위

- Source-bound CSV parsing, P1/P2 qualification, fixed-grid trace construction, explicit sensor-role binding and NPW pre-native gating now exist; complete CSV unit/clock/trigger/role evidence remains unavailable.

## 요구 동작

- Provide a versioned per-channel mapping artifact that binds archive/member hash, stored columns, exact value-to-Pa equation, pressure reference, coordinate-to-second equation, clock origin, nominal acquisition frequency, hardware channel and calibration evidence without inferring them from filenames.
- Provide P1/P2 common-trigger and synchronization evidence with offset, drift, uncertainty/support interval and sample-alignment policy; preserve unresolved or contradictory evidence instead of setting a shared clock from equal coordinate prefixes.
- Provide surveyed sensor positions and explicit left/right or network-node roles with coordinate reference and evidence; keep leak actuation/onset truth, hydraulic transient changes and detected feature arrivals as separate event fields.
- Return immutable source/mapping/pairing/role evidence identities that can be pinned by pressure_pair qualification, trace bridge and pressure-wave request receipts.

## 필요한 수락 증거

- Replay both complete original CSV members to the same SI records and show independent calibration checks across supported amplitudes, pressure reference and clock coordinates; no prefix-only or publication-frequency substitution.
- Changing archive bytes, member, column equation, calibration, trigger, clock mapping, sensor role or event identity invalidates all dependent receipts before native feature search.
- Independent known-event experiments report arrival-time error and false/missed event behavior by held-out experiment while preserving ambiguous, reflected, truncated and missing signals.

## 공통 계약

- SI and pressure-reference/clock-origin meaning preserved
- asset/scenario/clock/sensor/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred leak cause, probability, position or safety authorization
- original acquisition/monitoring owner supplies metadata and calibration; consumer adapter must not copy or invent them

## 인수인계

[상세 매뉴얼](../PRESSURE_WAVE_DATA_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `leak.negative_pressure_wave`.
