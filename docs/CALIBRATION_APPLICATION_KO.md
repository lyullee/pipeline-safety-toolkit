# 교정 결과의 출처 고정 적용 기록

`calibration_application`은 기존 `calibration.fit_calibration`,
`verify_calibration_record`, `predict_calibration`을 호출하는 기록 어댑터다.
새 적합식이나 계량 엔진을 구현하지 않는다. 교정 원본 식별자, 계산된 교정 영수증,
적용 식별자, 자산·시나리오·시계·원점, 적용 입력을 하나의 재생 가능한 출처 체인으로
남긴다.

## 사용

호출자는 기존 교정 API에서 얻은 `record`, 그 record를 만든 동일한 provider와 context를
준비한다. 필요하면 fit request를 함께 전달해 기록의 원 입력이 예상과 같은지 대조한다.
적용 입력 공분산을 가진 경우 정규화한 전체 행렬의 digest를 요청에 선언하고 같은
행렬을 함수에 제공한다.

```python
from pipeline_integrity.calibration_application import (
    CalibrationApplicationRequest,
    apply_verified_calibration,
    verify_calibration_application,
)

# record, provider, fit_request, context는 기존 calibration API의 검토된 객체다.
# covariance = ((u11, u12), (u12, u22))
# request = CalibrationApplicationRequest(
#     source_id="campaign-2026-04",
#     calibration_record_digest=record["record_digest"],
#     application_id="inspection-window-12",
#     context=context,
#     x_values=(...),
#     input_covariance_digest=digest(covariance),
# )
# application = apply_verified_calibration(
#     record, request, provider, fit_request=fit_request,
#     input_covariance=covariance,
# )
# assert verify_calibration_application(application)["valid"]
```

공분산 행렬은 적용 입력 순서와 같은 정방 행렬이어야 하며 그 크기·digest만 결속한다.
현재 이 어댑터는 입력 공분산을 출력 예측 불확실도로 전파하지 않으므로
`application_uncertainty`는 null이다. 시스템 오차, 시간 상관, 영점·drift, 재교정,
이력 단절을 추정하지 않는다. calibration API 자체도 독립 현장 자격을 대신하지 않는다.

`verify_calibration_application`은 결과 digest와 선언된 source/application ID를 검사한다.
원 관측 또는 교정 수학을 다시 실행하는 기능이 아니다. 원본 재생에는 기존
`verify_calibration_record`와 source ledger를 다시 사용해야 한다. 예제 입력은
검증 fixture이지 실측 보정 계수나 배관 설계값이 아니다.
