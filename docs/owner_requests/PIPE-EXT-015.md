# PIPE-EXT-015 — 모니터링 입력의 시계·전달시각 보존

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: process-monitoring-toolkit

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.instrument_exchange`: `TransmitterScenario`, `TransmitterMonitoringInput`, `replay_transmitter`, `transmitter_monitoring_input`, `packet_observation` — [소스](../../src/pipeline_integrity/instrument_exchange.py), SHA-256 `33b41f8de4785f1fb7619e6f0e1d1d3160172f115a48c01d376c80571d63651b`.

## 이 저장소에 이미 작성한 범위

- A source-bound adapter converts complete zero-delivery-delay transmitter packet slices into existing MonitoringSplit records, preserving source order, reported clock identity and dropout/invalid rows. Delayed packets and negative reported-time rows are rejected because the current MonitoringSplit schema cannot represent their availability or timestamp.

## 요구 동작

- Accept per-row source acquisition time, instrument-reported time and packet availability time as separate quantities with explicit source/reported clock identities and mapping evidence.
- Preserve invalid, dropout, unknown, future-reported and not-yet-available states in training, calibration and evaluation splits without zero-fill, time compression, row sorting or accidental future access.
- Make split chronology and event-window purging operate only on a declared common clock mapping; refuse cross-clock comparisons when mapping evidence is absent.
- Bind the monitoring result to the transmitter replay digest, source frame IDs, channel/unit mapping and clock transformation so any change invalidates dependent evaluations.

## 필요한 수락 증거

- Exact-clock, offset, rate-error, negative reported-time, delayed delivery, dropout and invalid-input examples preserve both time domains and yield explicit withheld or invalid states without dropping original packet identities.
- Chronological calibration/evaluation rejects overlapping source rows and prevents a sample from being visible before its declared availability time, including after checkpoint/replay.
- Changing source/reported clock identity, mapping, packet availability, invalidity, units, replay digest or split membership invalidates the sealed monitoring input and downstream result.
- Independent acquisition/playback traces verify clock mapping, transport latency and missing-packet semantics; injected fault examples remain simulations and are not counted as field calibration.

## 공통 계약

- Preserve SI quantity, channel identity, source and reported clock, acquisition time, arrival time, packet order and invalidity separately.
- Bind asset/scenario/source/configuration/replay digest and evidence to every monitoring split and result.
- Unknown timing, unsupported clock mappings and unavailable packets remain explicit; no inferred sensor reliability, alarm probability or safety authorization.
- Monitoring owner owns detection/statistics; transmitter adapter only preserves declared signal and timing semantics.

## 인수인계

[상세 매뉴얼](../CONTROL_EXCHANGE_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `instrument.sensor_faults`.
