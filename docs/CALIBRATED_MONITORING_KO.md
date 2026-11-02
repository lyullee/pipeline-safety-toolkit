# 교정 적용 결과의 모니터링 입력 연결

`pipeline_integrity.calibrated_monitoring`은 `calibration_application`의 source-bound
application 영수증을 기존 PMT `MonitoringSplit`으로 전달한다. 교정/모니터링 계산을
복제하지 않고, 행·시계·단위·결측 경계를 확인한다.

## 입력 계약

`build_calibrated_monitoring_input`은 계산된 calibration application, 원 `SignalFrame` 배열,
각 frame의 고유 observation ID, 채널과 stream 식별자, 그리고 monitoring split의 역할·
실행·그룹·근거 metadata를 받는다. Application context에 source/mapping/application evidence
ID가 포함되어야 한다. 모든 frame은 같은 stream, clock, origin이어야 하며 calibration context와
clock/origin이 일치해야 한다. 호출자는 원 asset/stream crosswalk를 근거에 따라 확인해야 한다.

application의 x값과 예측 x/y/unit은 원 frame과 비교된다. 모든 valid/non-null row를 원 순서로
정확히 하나씩 포함해야 한다. invalid/null 행은 관측 ID와 시각을 그대로 유지하고 값도 null,
valid=false로 남긴다. 일부 유효행을 빠뜨리거나 시계·단위가 다르면 거부한다. 기존 split이
단일 채널 단위만 지원하므로 지정한 채널만 교정 출력 단위로 변경하고 나머지 채널은 보존한다.

```python
from pipeline_integrity.calibrated_monitoring import build_calibrated_monitoring_input

result = build_calibrated_monitoring_input(
    application_record, source_frames, source_observation_ids,
    calibrated_channel="flow", source_stream_id="FT-101", role="training",
    split_id="train-1", physical_run_id="run-1", source_group_id="campaign-1",
    source_evidence_id="raw-campaign", application_evidence_id="calibration-application",
    mapping_evidence_id="stream-crosswalk", reference_kind="caller_declared_reference",
)
monitoring_split = result.split
```

결과는 원/변환 frame digest, 교정 application digest, 원/교정 observation ID, 채널 단위와 context
asset/scenario/clock/origin을 봉인한다. `verify_calibrated_monitoring_input`은 receipt와 frame
digest를 검사한다. 교정 application의 별도 owner replay 검증 책임을 대체하지 않는다.

## 한계

application receipt는 구조/영수증 검사 후 사용되며 이 어댑터가 calibration owner의 독립 재생을
대신하지 않는다. 불확실도는 전파하지 않고 시계 변환, 외삽, 결측 보간, drift/reset, field custody
자격을 제공하지 않는다. 이는 monitoring split으로 전달 가능한 단일 단계 변환이며 독립 calibration
및 evaluation 자료 분할은 `monitoring_exchange`가 별도로 요구한다.
