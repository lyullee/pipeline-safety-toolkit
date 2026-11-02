# 공통원인 관측 근거를 기존 FTA로 전달

`pipeline_integrity.common_cause`는 caller가 검토한 공통원인 사건 정의와, 같은 기회 모집단에서
함께 관측한 원인/구성요소 Boolean count를 기존 `risk_exchange`의 joint-state FTA 경계로
전달한다. FTA 계산은 owner API가 맡는다.

## 계약과 입력

`CommonCauseBinding`은 공통원인 event ID, 검토자가 정의한 원인 정의 ID, 종속 구성요소 event ID
두 개 이상, 원인 분류 근거 ID와 `reviewed_shared_cause` 상태를 요구한다. 전달하는
`JointStateCountTable`은 원인 event와 영향을 받는 모든 event를 정확히 포함해야 한다. 각 사건의
확률 출처 context에는 공동 count 표의 근거와 원인 분류 근거가 있어야 하고, 영향 event의
`dependency_groups`에는 해당 cause event ID가 선언돼야 한다. Count table의 전체 event ID는
`RiskProbability` 묶음과 일치해야 한다. 추가로 FTA root에 원인 사건과 모든 영향 사건의 leaf가
실제로 있어야 한다. 사용하지 않는 관측 사건을 표에만 넣는 매핑은 거부한다.

```python
request, receipt = fault_tree_request_from_common_cause_counts(
    root=reviewed_fault_tree,
    events=events,
    table=observed_joint_count_table,
    count_receipt=verified_joint_count_receipt,
    binding=CommonCauseBinding(
        cause_event_id="shared_header_disturbance",
        cause_definition_id="reviewed_header_pressure_event",
        affected_event_ids=("seal_failure", "support_failure"),
        cause_evidence_id="reviewed_process_evidence",
        review_state="reviewed_shared_cause",
    ),
)
```

먼저 `joint_states_from_counts`로 만든 count 영수증을 `count_receipt`로 공급한다. 함수는 표를
추정/보정하지 않고 기존 `fault_tree_request_from_verified_joint_counts`를 호출한다. 반환 영수증은
원 표 digest, count receipt, FTA request digest와 원인 정의를 함께 봉인한다. 조건, 기회 ID,
관측 모집단, grouping unit, 영향 사건 집합, 그리고 FTA leaf 집합도 기록한다. 재생은
`verify_common_cause_binding_receipt`를 사용한다.

## 해석 경계

이 계약은 같은 그룹 관측에서 제공된 결합 상태를 정규화해 넘긴다. 주변확률에서 상관 또는
공통원인 계수를 추정하지 않고, beta-factor·공통 원인 빈도·샘플 불확실도를 산출하지 않는다.
`RiskProbability`의 주변값은 joint count에서 계산된 주변값과 owner FTA가 대조한다. Count 표가
누락된 상태를 exhaustive로 대표하는지, grouping unit이 실제 독립 기회인지, 원인 분류가 인과적으로
타당한지는 caller의 자료 검토가 필요하다. 따라서 이 영수증은 관측·가정의 lineage이며 인과효과,
현장 빈도 또는 독립 현장 검증이 아니다.

회귀는 합성 fixture만 사용하며 누리온 대기다. 현재 상태와 manifest는
`research/common_cause_status.json` 및 `research/validation_queue.json`을 참조한다.
