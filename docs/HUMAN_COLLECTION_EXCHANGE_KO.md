# HRA 수집자료와 위험 입력 연동

이 경계는 `human-reliability-toolkit` 0.5.0의 기회별 `CollectionBundle` 계산 결과를 이 프로젝트의 기존 `RiskProbability`와 위험 엔진으로 전달한다. 수집 denominator·posterior·missingness 해석은 HRA owner가 수행한다. 여기서는 owner 계산을 복제하지 않는다.

이 adapter를 사용하려면 먼저 `python -m pip install -r requirements-hra.txt`로 MIT 라이선스 upstream HRA 0.5.0 commit `d2754bbea16e12d00240e88d13cb4badbe672ac3`을 설치하고, 이어 `python -m pip install -e ".[hra]"`를 실행한다. HRA Git source는 PyPI 배포 metadata에 직접 의존성으로 넣지 않는다. 실행 시 collection 계산에 쓰인 다섯 owner 파일의 SHA-256도 대조한다. 원본 collection 자료는 별도 private 경로에 보관한다.

## 적용 범위

`bind_human_collection_estimate`는 upstream collection source와 HRA release가 0.5.0일 때만 동작한다. protocol, 관측 창, 등록 기회, 결과 판정, registry reconciliation, HRA source 모듈 다섯 파일 hash를 고정하고, owner의 `require_reconciled()`와 `hep()`를 호출한다. 추정값의 collection audit가 호출 전후 동일한지 검사한다.

배관 위험으로 옮길 때 분석자는 다음을 명시해야 한다.

- 고정 protocol과 일치하는 HRA event ID, task ID, conditioning, failure definition, opportunity definition
- 대상 설비의 condition ID 및 opportunity ID
- 두 정의 사이의 적용성 설명을 담은 `RunContext.evidence` 항목. 이 evidence는 검토 artifact의 SHA-256이 있어야 하며 synthetic 근거는 거부된다.
- opportunity 독립성 검토와, 제공할 경우 결측 메커니즘 검토 각각의 `RunContext.evidence` ID. 검토 artifact의 SHA-256이 필수이고 synthetic 근거는 거부된다. review 문장만으로 근거를 대신할 수 없다.
- `CollectionBundle.require_reconciled()`의 `collection_digest`를 SHA-256으로 갖는 observation evidence ID. 형식은 HRA owner가 생성하는 `{protocol.id}:{collection_digest}`이다.
- HRA protocol, coverage, registry, adjudication, collection authorization, 각 window/demand/outcome 및 추정값의 evidence ID를 포함한 `RunContext`
- 초기 또는 최종 estimand, Beta prior, independence review, 필요한 missingness review 및 선택 window/context

HRA의 posterior 결과는 원 Estimate와 비교 가능하도록 그대로 별도 보존한다. 위험 handoff 복사본은 mapping·독립성·선택적 결측 검토 evidence를 추가하고 status를 항상 `research`로 둔다. owner가 `natural_field` 설정에 `empirical`을 부여해도 그것은 upstream 기록의 setting 설명일 뿐, 이 배관 application으로 이전된 HEP 자격을 뜻하지 않기 때문이다. 결과의 `record_digest`와 `verify_human_collection_risk_binding`이 원 Estimate, 적용성·검토 근거, research-only risk handoff와 owner 재생을 함께 검사한다.

## 사용 예

아래 예시는 호출 구조만 보인다. 실제 HRA protocol, EvidenceRef, event와 task 정의는 사용자 자료에 맞게 검토해 채운다.

```python
from human_reliability.collection import load_collection
from human_reliability.probability import BetaPrior
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.human_collection_exchange import bind_human_collection_estimate
from pipeline_integrity.risk_exchange import HRARiskClient

bundle = load_collection("private/hra_collection")
audit = bundle.require_reconciled()
observation_id = f"{bundle.protocol.id}:{audit['collection_digest']}"
client = HRARiskClient()
result = bind_human_collection_estimate(
    bundle,
    reviewed_human_failure_event,
    client=client,
    # Include protocol/row, mapping, review and prior evidence,
    # and EvidenceRef(id=observation_id, sha256=audit["collection_digest"]).
    context=reviewed_run_context,
    estimand="final",
    prior=BetaPrior(alpha=1.0, beta=1.0, evidence_id="reviewed-prior"),
    independence_review="reviewed operator/demand dependence basis",
    independence_review_evidence_id="opportunity-independence-review",
    mapping_evidence_id="process-mapping-review",
    target_condition_id="pipe-isolation-alarm",
    target_opportunity_id="one-isolation-demand",
    missingness_review="reviewed missing outcome mechanism",
    missingness_review_evidence_id="missingness-review",
    selection={"window_ids": ("window-01",), "conditions": {"mode": "alarm"}},
)

result.check_integrity()
risk_probability = result.risk_probability
```

`BetaPrior`의 파라미터와 selection은 예시일 뿐이다. 독립성, prior, event mapping 또는 누락 검토를 코드가 추론하지 않는다. 이후 FTA에는 기존 `HRARiskClient`와 risk exchange 계약을 사용한다. 연간 환산에는 일치하는 별도 기회 노출 자료를 제공한다.

## 한계와 검증 상태

기록의 `industrial_hep_validated`와 `field_population_transfer_qualified`는 항상 false다. upstream collection이 natural field setting을 반환해도 이 어댑터는 독립·대표성·산업 이전 자격을 보장하지 않는다. simulator, synthetic 또는 편향 가능 데이터는 그 출처·조건을 유지하며 현장 HEP로 표시하지 않는다.

공개 Human Control Room workbook은 집계 adapter에서 참가자별 행과 시간이 노출되지 않고, 전체 참가자 수 reconciliation도 해결되지 않았다. 따라서 이 공개 workbook을 이 API에 자동 변환하는 코드는 제공하지 않는다. HRA 원본 수집 양식으로 수집한 적격 기회 자료만 별도 protocol, source/rights review와 event mapping을 마친 후 사용할 수 있다.

이 모듈 회귀는 `tests/test_human_collection_exchange.py`에 작성되어 있으며 **미실행**이다. 후속 Nurion 검증 계획은 `research/validation_queue.json`의 최신 대상 계획에 반영한다. 기존 전체 test pass 기록은 이 신규 어댑터를 검증하지 않는다.

upstream 계약 근거: [Python API 안내](https://github.com/lyullee/human-reliability-toolkit/blob/main/docs/API.md), [0.5.0 변경 기록](https://github.com/lyullee/human-reliability-toolkit/blob/main/CHANGELOG.md), [collection API source](https://github.com/lyullee/human-reliability-toolkit/blob/main/src/human_reliability/collection.py).
