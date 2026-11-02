# 관측 위험 타임라인 입력

`pipeline_integrity.risk_timeline`은 기존 `response_trace.trace_response` 결과를
공통 asset/scenario/clock/origin 및 시간창 안에서 순서가 보존된 관측 패킷으로 묶는다.
응답 시계열을 확률로 변환하거나 FTA를 평가하지 않는다.

## 사용 경계

입력 trace마다 원 `result_digest`, context, usable cue, command issue, sampled endpoint,
그리고 고유 protection ID가 필요하다. `RiskTimelineRequest.mapping_evidence_ids`는
모든 원 trace context의 `EvidenceRef`에 실제로 포함돼야 한다. caller는 여러 자산·장치의
원 식별자를 하나의 asset/scenario/clock/origin에 대응시킨 근거를 검토하고
`timeline_mapping_reviewed=True`로 선언한다. 이 선언은 근거 자체를 대체하지 않는다.

```python
from pipeline_integrity.risk_timeline import RiskTimelineRequest, build_observed_risk_timeline

request = RiskTimelineRequest(
    id="campaign-window-01", asset_id="line-A", scenario_id="trip-01",
    clock_id="utc-reviewed", start_s=0.0, finish_s=3600.0, source_origin="measured",  # observed/laboratory/simulation/synthetic
    event_record_digests=(trace_record["result_digest"],),
    mapping_evidence_ids=("asset-clock-crosswalk",), timeline_mapping_reviewed=True,
)
timeline = build_observed_risk_timeline(request, (trace_record,))
```

실제 입력은 source context clock/origin과 정확히 일치해야 하며, 모든 시간이 단조 순서로
요청 창 안에 있어야 한다. unresolved trace, 변조 digest, 근거 누락, 중복 protection은
거부한다. 결과의 `dynamic_risk_probability`는 항상 null이며, 이것은 아직 각 사건의 빈도·
조건부 확률·의존성·복구·공정 손실을 갖춘 위험 계산 API가 없음을 뜻한다. 실제 FTA를
구성하려면 해당 모집단과 노출, 사건 관계, 공통 원인 및 결과 확률의 별도 근거가 필요하다.

trace의 sampled endpoint는 이산 표본 관측이다. 달성된 표본과 관측창 끝까지 달성되지 않은
표본 결과를 모두 보존한다. 구간 사이 연속 안전, 누설 제거, 현장 자격을 뜻하지 않는다.
합성 예제·시험은 사용 예시로만 취급한다.
