# PIPE-EXT-009 — 절차·사건·인적 추정값 의미 보존

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: human-reliability-toolkit, caller-owned record-management system

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.procedures`: `Procedure`, `ProcedureRecord` — [소스](../../src/pipeline_integrity/procedures.py), SHA-256 `ddf828f91b7173b6819fc86cb5259699073b40020d0278bd9356786cb44c9f0e`.
- `pipeline_integrity.response_trace`: `ResponseTraceSpec` — [소스](../../src/pipeline_integrity/response_trace.py), SHA-256 `f30bb933121d7d050e4fea3a974f36a6d54fe9d37e13c1b78fa446d2c4ce8cea`.

## 요구 동작

- Receive task/event/context/evidence and planned/observed/cancelled action completion as distinct fields.
- Preserve immutable observation identity, review/version/withdrawal lineage and source-system time/authority metadata.
- Keep procedure-record satisfaction, correct command, target attained and process rule achieved separate from HEP estimation.

## 필요한 수락 증거

- Conflicting or altered evidence/endpoint identity cannot reuse an earlier estimate or completion receipt.
- No observation completeness flag or recorded satisfaction is converted to HEP=0 or automatic work authorization.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../PROCEDURES_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `operations.control_room`, `maintenance.isolation`, `risk.dynamic_coupling`.
