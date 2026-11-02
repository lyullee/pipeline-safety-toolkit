# 계측 시계 오차 보정 후보

`pipeline_integrity.clock_correction`은 caller가 검토한 시계 오프셋·율 오차 모델을
원 계측 시각에 적용해 기준 시계의 후보 시간축을 만든다. 입력 프레임은 재정렬하지
않으며 원 시각·프레임·관측 순서를 영수증에 함께 보존한다. 원 `RunContext`도 남기고
동일 asset/scenario/origin/evidence/metadata에 기준 clock ID만 투영한 후보 context를 함께
제공해 downstream 호출자가 새 context를 임의 작성할 필요가 없게 한다.

`ClockCorrectionModel`에는 source/reference clock ID, 두 epoch, epoch에서의 offset,
fractional rate error, 유효 source 시간 구간, 잔차 상한, 모델 근거, 별도 검토 근거가
필요하다. `caller_reviewed=True`만으로 충분하지 않고 두 근거 ID가 `RunContext`에
포함돼야 한다. 이 플래그는 호출자가 검토를 완료했다고 선언하는 계약이다.

```python
from pipeline_integrity.clock_correction import (
    ClockCorrectionModel,
    apply_clock_correction_candidate,
    verify_clock_correction_candidate,
)

candidate = apply_clock_correction_candidate(
    frames,
    reviewed_clock_model,
    context,
    observation_ids=observation_ids,
)
verify_clock_correction_candidate(candidate)
```

보정식은 `reference_epoch + (source_time - source_epoch - offset)/(1 + rate_error)`다.
입력 순서가 시간상 비단조이거나 모델의 검토 구간을 벗어나면 거부한다. 출력은 후보
시간축이며 교차 센서 동기화, 인과적 사건 정렬, 잔차 분포 전파, 현장 교정 성능을
입증하지 않는다. 기존 `instrument_exchange.TransmitterScenario`의 clock fault 합성은
가상 송신기 출력이고 이 모듈은 caller가 제공한 검토 모델을 원 관측 프레임에 적용하는
별도 경계다. 보정된 프레임을 실제 분석에 쓰기 전에 downstream 소비자가 후보 clock ID를
허용하는지 검토해야 한다.

합성 회귀 5개를 작성했으나 실행하지 않았다. 컴파일·정적 감사와 누리온 검증은 별도 상태로
관리한다.
