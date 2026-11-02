# PIPE-EXT-011 — 공통원인 관측·모수·시간의존 위험 계약

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: human-reliability-toolkit

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.risk_exchange`: `JointStateCountTable`, `joint_states_from_counts`, `verify_joint_state_count_receipt`, `fault_tree_request_from_joint_counts`, `HRARiskClient` — [소스](../../src/pipeline_integrity/risk_exchange.py), SHA-256 `678b716f4d8e59b340545ea5fc143a89388f0e79ef0c58f56d039b4711aad37b`.

## 이 저장소에 이미 작성한 범위

- Exact observed Boolean state counts can be normalized, replay-verified and bound to the existing joint-state FTA request without fitting a common-cause model.

## 요구 동작

- Accept source-bound grouped joint observations with event/opportunity/population/grouping identities, exposure, censoring, observation window and missing-state meaning; do not treat omitted or unobserved combinations as observed independence.
- Implement reviewed common-cause parameter models and posterior or interval uncertainty in the owner risk engine, with explicit model family, parameter support, prior/likelihood or frequentist construction and held-out applicability evidence.
- Represent time ordering, repair/recovery, changing exposure and shared latent conditions separately from one-demand static Boolean states; retain compatibility with exact empirical joint-state evaluation.
- Return owner-native receipts that preserve original counts, fitted/evaluated population split, event marginals, dependence groups and probability measure without converting demand probability to annual frequency.

## 필요한 수락 증거

- Independent arithmetic for exact count normalization and model limiting cases, plus synthetic recovery of known dependence parameters with coverage/calibration checks appropriate to the declared uncertainty meaning.
- Held-out grouped observations or experiments demonstrate transfer limits; altered grouping, opportunity denominator, censoring, clock, source or event definition invalidates dependent FTA/ETA/LOPA receipts.
- Static joint states, fitted common-cause parameters and dynamic repair models produce distinct typed outputs and cannot silently substitute for one another.

## 공통 계약

- probability measure, conditioning and opportunity denominator preserved
- asset/scenario/clock/event/population/group/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred annual frequency, field validation or safety authorization
- human-reliability owner implements dependence mathematics; pipeline consumer only binds source evidence and calls it

## 인수인계

[상세 매뉴얼](../RISK_EXCHANGE_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `risk.common_cause`.
