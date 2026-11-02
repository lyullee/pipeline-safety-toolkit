# PIPE-EXT-013 — 누수 실험 가속도·하이드로폰 취득/교정 계약

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: caller-owned data-acquisition metadata provider, process-monitoring-toolkit ingestion boundary

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.leak_multisensor_data`: `inventory_leak_multisensor_archive`, `accelerometer_member_labels`, `read_accelerometer_member`, `parse_accelerometer_csv`, `verify_accelerometer_csv_record`, `qualify_accelerometer_pair`, `verify_accelerometer_pair_qualification`, `build_accelerometer_pair_trace_record`, `verify_accelerometer_pair_trace_record`, `bind_accelerometer_delay_request`, `hydrophone_member_labels`, `read_hydrophone_member`, `verify_hydrophone_source_record` — [소스](../../src/pipeline_integrity/leak_multisensor_data.py), SHA-256 `9d63c8030892b3361bbe9f6ef0371a9c876b475c2db38d3e0489bafee02f8407`.

## 이 저장소에 이미 작성한 범위

- The pinned archive inventory, explicit accelerometer CSV mapping/replay, reviewed A1/A2 qualification, fixed-grid SampledTrace bridge, source-sealed explicit-role delay request binding and opaque H1/H2 RAW source receipts exist; hydrophone RAW decoding, sensor calibration, paired clock evidence, surveyed roles and independent event truth are intentionally absent.

## 요구 동작

- Provide versioned per-channel accelerometer mappings that bind source/member hash, exact Sample-to-second equation, Value-to-m/s2 equation, offset, axis/orientation, mounting, calibration uncertainty, acquisition range, clock origin and hardware channel without inferring them from numeric spacing or filenames.
- Provide a reviewed decoder contract for each hydrophone RAW variant including byte order, signedness, sample width, framing/header/trailer rules, channel layout, sample rate/clock origin, saturation/missing-code meaning and exact code-to-pressure equation with pressure reference and calibration evidence.
- Provide A1/A2 and H1/H2 common-trigger, offset, drift, alignment uncertainty and surveyed sensor-position/role evidence; keep scheduled flow changes, leak actuation, physical onset and detected arrivals separate.
- Return immutable mapping/calibration/pairing/role evidence identities and decoder source/version/license hashes suitable for consumer replay; do not require bundling supplier executables into the public source release.

## 필요한 수락 증거

- Replay complete representative files from every supported acquisition variant and compare decoded sample counts, coordinates and calibrated amplitudes against an independently reviewed supplier export or documented reference vectors.
- Calibration checks span supported frequency/amplitude ranges and report uncertainty, clipping and transfer limits; altered bytes, decoder version, endian/layout, mapping, clock, calibration or sensor role invalidates dependent receipts.
- Held-out known-event experiments report arrival-time/localization and false/missed-event behavior by sensor family while preserving ambiguous, reflected, saturated, truncated and missing signals.
- Licensing evidence permits distribution of the implemented decoder or documents a clean-room/open specification path; raw observations remain separately licensed and excluded from source-only bundles.

## 공통 계약

- SI quantity/unit, pressure reference, axis/orientation and clock origin preserved
- asset/scenario/sensor/channel/source/configuration and evidence bound to each decoded record
- unknown/unsupported/nonconverged stays explicit; no inferred leak cause, probability, onset, position or safety authorization
- acquisition/monitoring owner supplies decoder and calibration truth; consumer adapter does not reverse-engineer or invent it

## 인수인계

[상세 매뉴얼](../LEAK_MULTISENSOR_DATA_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `data.qualification`, `leak.acoustic_localization`.
