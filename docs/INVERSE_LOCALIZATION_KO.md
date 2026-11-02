# 유한 누설 위치 후보의 조건부 비교

현재 상태는 **partial, 수치 검증 대기**다. 코드와 HPC용 시험을 작성했으며 로컬에서는
Ruff와 컴파일 검사만 수행한다. 시험을 작성했다는 사실은 통과·현장 검증을 뜻하지 않는다.

`pipeline_integrity.inverse_localization`은 검토된 위치 후보마다 원 물리 엔진의 예측을
받아 관측과 비교한다. 수리·과도·음향 방정식을 다시 구현하지 않는다. 기존 `inverse.Prediction`
반환 계약을 재사용하며, 공급자는 원 엔진을 호출하거나 사전에 검토된 고정 예측을 반환한다.
위치 사이를 자동 보간하거나 임의의 감쇠식을 추가하지 않는다.

## 입력과 의미

`LocalizationRequest.rows` 순서가 잔차 벡터와 공분산의 순서다. 각 행은 채널·센서·물리 포트,
정확한 보고 시간(s), `MeasurementBasis`, 값과 유효성을 가진다. 단위는 coherent SI이며 압력은
absolute/gauge/differential 기준을 명시한다. 공분산 원소의 단위는 대응하는 두 행의 단위 곱이다.
서로 다른 물리량도 하나의 완전 공동 공분산으로 연결할 수 있다. 임의 독립 가정, 보간, 행 삭제,
결측값 대체는 하지 않는다. `covariance_row_ids`는 행 ID와 정확히 일치해야 한다.

각 `LocationCandidate`는 위치·좌표계·3차원 m 좌표·물리 포트와 고정된 파라미터를 가진다.
파라미터마다 SI basis를 선언한다. 누설 크기·발생 시각·경계조건·물성·센서 편향 등 결과에 영향을
주는 양은 공급자 구성 또는 후보 파라미터로 고정하고 근거를 남겨야 한다. 현재 API는 이를
프로파일 최적화하거나 적분하지 않는다. 후보당 누설 크기가 다르면 결과는 위치만의 비교가
아니라 그 크기를 포함한 고정 가설의 비교다.

`REVIEWS`의 네 항목을 근거 ID와 함께 검토해야 한다. 미상 시계 오프셋, 보정되지 않은 편향,
미해결 모델오차, 미상 형상/파라미터/관측/공동잡음은 `needs_data`로 남긴다. residual covariance는
측정오차뿐 아니라 검토된 모델 불일치의 영향을 포함해야 하며 후보에 공통인 Gaussian 법칙이다.
상관관계를 포함한 합성 과정은 호출자가 검토한다. 단순 공분산 합산으로 독립성을 추정하지 않는다.

## 수치 경로와 출력

공분산은 유한·정확 대칭·양의 정부호여야 한다. 대각 표준편차로 스케일한 상관행렬을 native
SciPy Cholesky로 분해하고 triangular solve로 잔차를 whitening한다. 역행렬·의사역행렬·jitter
수리는 사용하지 않는다. 후보별 `mahalanobis_squared`는 whitened 잔차 제곱합이다.
`relative_log_likelihood`는 최고 적합 후보에 대한 `-0.5 * (loss - min_loss)`이다.
공통 공분산·공통 행에서만 상수 정규화항이 소거된다. 후보별 서로 다른 공분산은 지원하지 않는다.

`best_candidate_ids`는 유한 후보 집합에서 가장 낮은 손실을 가진 모든 정확 동률 후보다.
공급자가 하나라도 예측을 보류하거나 선택 값이 미상이면 전체 winner는 보류하고 계산 가능한
후보의 명목 적합도만 남긴다. 전체 후보가 관측을 잘 설명하는지 판정하는 goodness-of-fit 검정이나
오탐률 기준은 제공하지 않는다. 최고 후보조차 실제 공정을 잘못 설명할 수 있다.

`pairwise_distances`는 후보 예측 사이의 공동잡음 기준 제곱거리다. 호출자가 선언한
`indistinguishable_distance_squared` 이하인지 표시한다. 이 값은 관측 구성에서의 유한 후보 분리도이며
빈도주의 검정·오분류율·신뢰구간·공간 분해능 보증이 아니다. 작은 거리의 대안 후보가 있으면
정확한 최소 손실이 하나여도 실제 위치를 확정해서는 안 된다.

출력의 `selection_meaning`도 이 한계를 명시한다. `best_indistinguishable_candidate_ids`는 최소 손실
후보와 선언한 threshold에서 분리되지 않는 직접 대안 후보들을 함께 담고,
`best_is_separated_from_every_alternative`는 그 집합이 단일 후보인 경우에만 True다. 후보가 미상이면
이 플래그는 None이다. True도 유한 후보·고정 모델·선언 threshold에서의 결과일 뿐 현장 확정이 아니다.
Prediction은 model_copy로 구성됐더라도 전체 필드를 재검증한다. nonfinite 입력은 거부하며 수치
overflow/invalid 연산은 오류로 중단한다. 손실과 후보 간 거리의 유한성을 별도 확인하여 NaN/Inf를
computed 레코드나 누락값으로 바꾸지 않는다.

출력은 **누설이 있다는 고정 가설 집합에 조건부인 상대 적합도**다. 누설 존재 확률·위치 posterior·
탐지확률·HEP·위험 발생률로 정규화하지 않는다. 모든 레코드의 `location_posterior`와
`leak_existence_probability`는 None이고 `field_qualified`, `work_authorized`는 False다.
누설 없음/다른 고장 가설, 사전확률, 독립 검증과 의사결정 규칙은 상위 모듈의 별도 책임이다.

## 원 엔진 공급자 연결

`LocalizationProvider`의 `id/version/source_sha256/configuration_sha256`는 요청과 같아야 한다.
공급자 호출은 `provider(candidate, sorted_unique_report_times, context)`이며 반환은 기존
`inverse.Prediction`이다. 채널마다 요청한 모든 보고 시간 값을 SI 단위로 반환해야 한다.
행이 없는 채널/시간 조합은 평가에 쓰지 않지만 임의 보간으로 채우면 안 된다.
압력 기준·참조상태·포트·방향은 요청과 공급자 구성에서 동일하게 해석하고 근거로 검토한다.

공급자는 원 엔진 호출 후 원 엔진 receipt와 진단을 `Prediction.diagnostics`에 보존하고,
임시 evaluation digest를 가진 Prediction을 만든 다음
`prediction_binding(request, candidate, context, prediction)`으로 실제 evaluation digest를 갱신한다.
이 binding은 원 엔진의 역사적 실행을 증명하지 않는다. 공급자 자체가 실제 엔진 소스와 설정,
라이브러리/물성/경계조건 버전을 기록할 책임이 있다. wrapper 파일 하나만의 해시로 원 엔진 전체를
검증했다고 주장하면 안 된다.

작성된 예제는 `examples/inverse_localization.py`의 `make_case(reviewed=False)`다. 기본값은 검토
해제로 계산을 보류한다. `AuthoredTable`은 원본 작성된 고정 예측표이며 물리 모델·현장자료가 아니다.
실제 transient 엔진 연결에서는 후보 포트를 원 그래프에 매핑하고 정확 보고 시간을 원 엔진 출력과
대조해야 한다. 지원하지 않는 위치를 임의로 가까운 노드에 붙이지 않는다.

## 재현과 검증 인계

`RunContext.metadata['inverse_localization_boundary'] = boundary_metadata(request)`로 전체 요청을
asset/clock/event와 묶는다. 모든 관측·파라미터·후보·검토 evidence가 context에 있어야 한다.
원자료 SHA, 공급자 source/configuration SHA, 요청/응답 binding, 모듈 및 공통 의존 소스 SHA,
Python/NumPy/SciPy/Pydantic 버전과 LAPACK binary SHA를 출력에 남긴다.

`verify_inverse_localization_record(record, provider, request=..., context=...)`는 seal 확인 후 원 공급자를
다시 호출해 현재 동일 소스·환경의 결과 전체를 비교한다. 저장된 손실/후보/자격을 바꾸고 seal만
재계산해도 native replay와 다르면 거부한다. 다른 환경의 부동소수점 차이나 비결정적 공급자는
엄격 replay에 실패할 수 있다. 서명·실행시각·과거 실행의 독립 인증 기능은 아니다.

HPC 시험은 analytic 2행 상관잡음 손실, 동일 예측 후보의 동률, 미상/무효 입력과 공급자 보류,
비대칭·부정부호 공분산, 원 공급자 source/clock/units/binding 변경, rehashed 출력 변조,
요청 이벤트와 context 불일치를 포함한다. 로컬 pytest·수치 예제·벤치마크는 실행하지 않았다.

후속 확장: 원 transient/steady 공급자 실증, held-out 공정 검증, 검토된 nuisance profile 또는
joint uncertainty 모듈 연결, 위치 후보 망의 적응 세분화, 관측 구성별 분리도와 sensor_placement
연계. 이러한 확장 전까지 broad inverse localization 항목을 완료로 계산하지 않는다.

공식 API 근거는 [SciPy Cholesky](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.cholesky.html)와
[triangular solve](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.solve_triangular.html)다.
문서의 설명과 예제 수치는 직접 작성했으며 외부 문장·표·계수·원자료를 배포하지 않는다.

## 교차 정적검토: 큰 공통 baseline과 식별성

후보 사이 식별 거리는 이제 절대 예측값을 각각 whitening한 뒤 빼지 않고, 원 예측값의 차이를 먼저 구한 후 같은 공동 covariance로 whitening한다. 관측 잔차의 whitening과 같은 연산 순서이며 수학적 의미는 같다. 큰 공통 압력 baseline에 매우 작은 noise scale이 있는 경우 불필요한 overflow와 상쇄를 피한다. 유한한 차이와 공동 noise가 해석 가능해도 절대 whitened prediction이 표현 범위를 넘을 수 있던 문제의 미실행 회귀를 추가했다. 수치 테스트는 누리온으로 유보했다.
