# 사용자 절차와 검토 기록 API

설계·시공·검사·격리·운전·비상대응의 29절차 항목은 공통 `procedures` 모듈을
사용한다. 기관별 절차·규격 내용을 내장하거나 29개 별도 판정기를 복제하지 않는다.
사용자가 자신의 적용 기준·버전·필수 증거·선행 작업을 선언한다. 공통 기록 도구의
구현과 개별 현장 절차의 적합성/자격은 별개이므로 카탈로그 상태는 procedure_required다.

## 입력

`Procedure`는 id/version, 적용 설비군과 `ProcedureItem` 목록을 받는다. 항목마다
작업 설명, criterion_source, required_evidence_ids, prerequisites를 지정한다.
중복 작업 ID·외부 선행 작업·순환 의존성은 거부한다. 기준 원문을 넣는 대신
사용자가 이용 권한을 확인한 기준 식별자와 locator를 사용한다.

`ProcedureRecord`에는 item_id, satisfied/not_satisfied/not_observed, EvidenceRef,
reviewer_id, reviewed_at, note를 준다. reviewed_at은 UTC offset이 포함된 ISO 8601
시각이어야 하며, naive datetime과 해석할 수 없는 문자열은 거부한다. 원 offset을
직렬화·digest·재생 비교에 보존하므로 같은 순간을 다른 offset으로 재표현한 caller 입력도
변경으로 감지한다. 신원 인증·시간 서버 검증·전자서명 검증은 제공하지 않는다. 검토자
권한, clock 정확도와 timestamp 진위는 원 기록 시스템에서 관리해야 한다.

`RunContext`에는 실제 asset/scenario/clock/origin을 명시한다. 관측과 모의결과를
EvidenceRef.origin으로 구분하고, 가능한 원 자료에 SHA-256·출처·locator·조건을 붙인다.
같은 evidence ID를 다른 관측으로 재사용하면 거부한다. 한 기록/문맥 안의 중복 ID도
거부하며, 여러 항목에서 같은 원 근거를 참조하는 경우 식별 내용이 같아야 한다.

## 평가와 재현

`assess_procedure(procedure, records, context, asset_class=...)`는 입력을 다시
검증한다. model_copy로 만든 잘못된 enum/자료도 그대로 수락하지 않는다. 항목은
선행 항목/필수 근거의 준비 상태는 readiness에 blocked/needs_evidence/ready_for_recorded_result로
기록한다. state는 만족 기록의 경우 준비가 끝나야 recorded_satisfied가 된다. 명시된
not_satisfied/not_observed는 근거 부족이나 선행 항목 차단으로 숨기지 않고 그대로 남긴다.
recorded_result는 원 판정이며 blocked_by/missing_evidence_ids는 별도로 유지한다.
이 원 판정이 남는다고 증거의 타당성·결함 존재가 독립 검증됐다는 뜻은 아니다.
증거가 존재한다는 사실만으로 그 내용의 기술적 타당성을 자동 심사하지 않는다.

반환 기록은 원 절차·모든 원 검토 기록·설비군·문맥·결정·소스/의존성 SHA와
result_digest를 보존한다. `verify_procedure_result(result, procedure=..., records=...,
context=...)`는 현재 구현으로 같은 결과가 나오는지 재실행한다. caller 입력을
생략하면 기록에 저장된 입력으로 비교한다. 무서명 다이제스트는 인증 수단이 아니다.

`all_records_satisfied=True`여도 independent_physical_or_regulatory_acceptance는
False다. 이 결과를 자동 작업허가, 밸브 개방 명령 또는 HEP=0으로 변환하지 않는다.
물리 판정은 해당 구조/수리/재료 엔진, 인간 오류 확률은 기존 human-reliability
엔진의 별도 계약과 검토를 따른다. 단일 기록의 만족 여부에서 확률을 추정하지 않는다.

## 검증 상태

기존 선행 작업 차단 시험은 이전 전체 배포 회귀에 포함돼 있다. 추가한 입력
재검증·충돌/중복 근거·재생/재봉인 변조 시험은 작성했으나 사용자 지시에 따라
실행하지 않았다. 누리온에서 tests/test_procedures.py와 기존 test_composition.py를
함께 실행하고 실제 결과를 보존해야 한다. 절차별 현장 검증이나 인적 모델 검증은
이 공통 도구의 회귀만으로 완료되지 않는다.

## 원 모듈 확장 요청

기록/작업관리 소유 시스템에는 서명된 관측 ID·불변 기록버전·표준시각·reviewer 권한·
철회/대체 이력과 대상 asset/scenario의 계약을 요청한다. HRA 엔진에는 이 작업의
task/context/evidence를 수신하는 경계를 연결하되 검토기록의 상태와 확률 추정치를
별도 의미로 유지한다. 이번 패키지는 인증·전자서명·규제 승인 엔진을 복제하지 않는다.


## 교차 정적 검토 후 변경

명시된 부정/미관측 결과가 근거 부족이나 선행 작업 차단에 가려지는 문제를 수정했다.
원 판정과 readiness를 분리하며 any_recorded_not_satisfied는 원 부정 기록 존재만 표시한다.
이 boolean은 결함 확률·절차 위반의 법적 판정·작업 정지 명령이 아니다. 뒤따르는 항목은
여전히 모든 선행 항목이 recorded_satisfied여야 준비 상태가 풀린다. 무서명 원 기록의
asset/scenario 귀속과 reviewer 권한은 원 시스템의 책임이며 본 API가 인증하지 않는다.
새 부정 결과/차단/근거 부족 동시 회귀는 작성했고 실행하지 않았다.
