# PIPE-EXT-003 — 압축기 성능과 운전 배치 영수증

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: lh2dt_models, hydrogen-station-sim

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.compressor_train`: `CompressorTrainRequest` — [소스](../../src/pipeline_integrity/compressor_train.py), SHA-256 `0323f3086ef68767e7fc686ef044fff47b549be802b5289260f63dba9d31df68`.
- `pipeline_integrity.operating_optimization`: `OperatingOptimizationRequest`, `SuppliedOperatingBatch` — [소스](../../src/pipeline_integrity/operating_optimization.py), SHA-256 `e5a7bb81405a5adda09a8107745159bd499a4883eb6c8100efb599dd45c8d15c`.

## 요구 동작

- Export source-bound delivered mass and integrated electric/auxiliary energy for the exact dispatch window, service definition and forecast.
- Preserve composition, map-speed/flow support, cooling/property basis and native state/balance results.
- Provide dynamic surge or transient capability only when the original engine implements and qualifies it.

## 필요한 수락 증거

- Altered provider/window/service/forecast or ports cannot reuse an old performance receipt.
- Independent map/part-load and cooling cases with actual mass/energy budgets and explicit unsupported regions.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../COMPRESSOR_TRAIN_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `equipment.compressor_map`, `economics.optimization`.
