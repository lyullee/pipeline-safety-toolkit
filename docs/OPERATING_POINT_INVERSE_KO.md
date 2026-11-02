# 실제 시계가 없는 운전점의 bounded 보정

`inverse.fit_operating_points`는 source의 원 운전점 ID로 train/heldout을 명시한다. 숫자 행 번호를 초로 바꾸거나 측정 시각을 생성하지 않는다. 기존의 물리 시계 기반 `fit_parameters`, `Observations`, `Prediction`, `FitRequest`는 기존 필드·호출 방식·시간 분할 의미를 유지한다. 새 경로는 동일 SciPy bounded least-squares/Jacobian/local-identifiability 커널을 재사용한다.

## typed 관측 계약

`OperatingPoint` 필드는 다음과 같다.

- `id`: 원 운전점 식별자. source CSV SHA와 원 행 번호를 결합한 ID 등을 사용할 수 있다.
- `state`: 그 운전점의 이름 있는 수치 상태 값 또는 null.
- `state_units`: state와 정확히 같은 키를 가진 명시 단위.
- `group_id`: 장치/실험 group의 공급 식별자.
- `evidence_ids`: 실제 문맥의 중복 없는 근거 ID.
- `source_digests`: 이름 있는 source SHA256.

`OperatingPointObservations(id, asset_id, origin, source_digests, points, channels)`는 point 순서에 맞춘 기존 `ObservedChannel`을 받는다. 각 관측 channel은 원래와 같이 `ResidualScale`, 값, 유효 여부를 가진다. point ID와 channel ID는 유일해야 한다. point의 source hash는 관측 집합의 같은 키/값에도 포함되어야 한다. 같은 ID에 다른 상태/단위/source를 몰래 붙이지 않는다. state null을 추정값으로 채우지 않으며 그 상태에서 모델을 계산할 수 없으면 provider가 withheld를 반환한다.

각 point의 evidence는 실제 `RunContext`에 존재해야 하고 최소 하나의 evidence SHA가 그 point의 source SHA와 일치해야 한다. residual scale의 source ID도 실제 문맥 근거에 있어야 한다. 이 검사는 근거 참조와 일관성을 확인하며 공급자의 측정 정확도나 원자료의 독립성을 인증하지 않는다.

## 명시적 train/heldout과 수치 옵션

`OperatingPointFitRequest`는 기존 FitParameter/provider ID/version과 수치 옵션에 더해 `training_point_ids`, `heldout_point_ids`를 받는다. 두 목록은 비어 있지 않고, 각각 유일하며, 서로 겹치지 않아야 한다. 보고되지 않은 point ID를 선택할 수 없다. 목록에 선언한 순서를 그대로 provider에게 전달한다. 관측 저장 순서나 ID 문자열 정렬을 측정 시간으로 해석하지 않는다. 선택하지 않은 운전점은 unused로 기록한다.

손실 함수, loss scale, 차분 폭, local rank 허용오차, model/optimizer 평가 예산과 parameter tolerance는 기존 시간 경로와 같은 의미이다. 파라미터 ID/단위는 provider의 선택적 `parameter_units`와 대조한다. 유효 train 관측 수보다 파라미터가 많으면 거부한다. 선택 channel마다 유효 train과 heldout 값이 있어야 한다. 유효하지 않은 channel 값을 조용히 채우거나 source point를 재번호 매기지 않는다.

## provider 호출과 typed 예측

호출은 `fit_operating_points(observations, request, context, predict)`이다. provider 서명은 다음과 같다.

```python
predict(parameters: dict, requested_points: tuple[OperatingPoint, ...], context: RunContext)
    -> OperatingPointPrediction
```

`OperatingPointPrediction`은 status(computed/withheld), asset_id, point_ids, point_digests, values, units, provider_source_digest, evaluation_digest 및 선택 diagnostics를 가진다. `point_ids`는 요청 순서와 같아야 하고 `point_digests`는 `{p.id: digest(p) for p in requested_points}`와 일치해야 한다. 전체 state/group/evidence/source가 결합되므로 이름만 맞춘 다른 상태를 반환할 수 없다. withheld도 요청 asset/point ID/digest를 유지하고 값을 비워 반환할 수 있다.

예측 channel 단위와 길이는 실제 관측 channel/ResidualScale과 대조한다. 계산된 예측에 미상/비유한 선택 값이 있으면 fitted로 처리하지 않는다. provider source가 fitting 도중 바뀌거나 요청 point의 상태/source 또는 실제 문맥을 provider가 변경하면 거부한다. core는 캐시와 평가 trace에 실제 point ID/group/digest를 보존한다.

## 하나의 수치 커널

두 API는 내부 `_fit_axis`를 공유한다. 단위 구간 파라미터 정규화, bounded SciPy least_squares, train 잔차의 차분 Jacobian, SVD rank/condition, bound 활성 상태, train/heldout metrics, source 변화 검출과 model budget 처리는 한 구현이다. 별도의 운전점 전용 optimizer를 만들지 않았다. 시간 wrapper는 기존 실제 time_s와 `time_range_s` trace를 유지하고, point wrapper는 point ID trace를 제공한다.

optimizer와 Jacobian은 train 값만 사용한다. 파라미터가 정해진 뒤 baseline/fitted heldout metrics를 계산하며 heldout을 다시 학습시키지 않는다. 반환 상태는 기존과 같이 fitted, not_converged, unidentifiable, withheld, budget_exhausted 등을 구분한다. local full rank는 파라미터의 전역 유일성이나 실제 물성 식별의 증명이 아니다.

## group·잔차 척도·검증 의미

결과 schema는 `pipeline_integrity.operating_point_fit.v1`이다. 원 point 상태/단위/source/evidence, 선택한 train/heldout ID, group, 사용하지 않은 행 수와 실제 예측 trace를 보존한다. `same_group_holdout`은 두 선택 집합의 group이 겹치는지 나타낸다. 한 배관의 여러 운전점은 같은 장치 내 holdout이다. group 이름이 달라도 독립 실험이나 현장 검증을 자동 인정하지 않는다. `independent_experiment_validation`, `field_validation_claim`은 항상 거짓이다.

ResidualScale은 공급자가 명시한 정규화 척도이다. 그것을 측정 표준편차로 자동 해석하거나 공분산/오차 독립성을 추정하지 않는다. 신뢰구간을 계산하지 않는다. 아래의 optional 공동 잔차 metric을 명시한 경우에는 그 검토된 행렬을 사용한다. Q/점도/head의 errors-in-variables 자체는 여전히 별도 소유 확장 범위이며, 잔차 행렬을 받는다고 입력 상태의 불확실성까지 전파한 것은 아니다. 모르는 상관을 독립 확률로 바꾸지 않는다.

RunContext에는 실행 추적용 clock_id가 계속 존재하지만 point 관측 계약에는 clock/time_s 필드가 없다. 결과의 context clock은 실행·출처 문맥일 뿐 측정 축이 아니다. 원 자료의 실제 시계가 나중에 확보되면 기존 시간 API를 그대로 사용할 수 있다.

## 기존 소비자와 재현

`water_inverse`의 Prediction(asset, clock, exact time_s, values, units, source/evaluation digest) 반환과 `inverse_localization`의 같은 Prediction 소비를 실제 소스로 대조했다. CLI의 기존 시간 FitRequest parsing, `calibration`의 `solve_bounded_least_squares`, `uncertainty_fit`의 ResidualScale도 기존 서명을 유지한다. friction 소비자는 새 point 계약을 선택적으로 사용할 수 있으며 기존 실제 시계 경로와 구분한다.

결과 digest는 요청·문맥·관측 digest/source, point metadata, provider trace와 owner 구현 지문을 포함한다. 재현하려면 실제 원자료에서 같은 OperatingPointObservations를 구성하고 같은 요청·실제 provider로 다시 호출하여 비교한다. 무서명 hash와 현재 재계산은 과거 실행 신원이나 과거 실행 사실을 인증하지 않는다. 물리 domain·보존 장부 및 source 라이선스 검토는 각각 실제 forward owner와 소비자가 유지해야 한다.

## 현재 검증 상태

코드와 회귀 시험 정의를 작성했다. Ruff와 Python 컴파일만 수행했으며 pytest, SciPy 수치 계산, forward native 모델과 driver를 실행하지 않았다. 기존 시간 API와 새 point API의 수치 회귀는 누리온에서 검증 대기이다. 후속 명령은 `python -m pytest tests/test_inverse.py -q`이며 water/inverse localization 및 friction 소비자 회귀도 함께 실행해야 한다.

새 시험에는 비시간 ID 순서, 같은 장치 train/heldout, heldout 값이 선택 파라미터를 바꾸지 않음, source axis를 압축하지 않는 결측 처리, 원 source/evidence/단위/ID 중복/겹침 거부, provider order/digest/단위/상태/문맥 변조, budget/withheld/rank 상태의 공통 커널 재사용을 포함한다. 시험 정의나 정적 검사 통과를 수치·현장 검증 완료로 세지 않는다.


## 공통 커널 교차 정적 검토 후 보강

시간축 wrapper도 운전점 wrapper처럼 Observations/FitRequest/RunContext 및 반환 Prediction을
원 Python 필드에서 다시 검증한다. model_copy로 만든 잘못된 enum, bool/int 예산 혼동,
NaN/문자열 예측, 잘못된 residual scale과 중복 시간축을 유효한 입력으로 취급하지 않는다.
observations_from_frames 역시 원 SignalFrame과 ResidualScale을 재검증한다. 기존 callable
서명과 반환 key는 유지하고 시간 기반 train/validation 구간 및 source ID 의미는 바꾸지 않는다.

withheld 예측도 원 요청 asset/clock/exact report times 또는 point ID/digest를 유지해야 한다.
계산 불가 시 values를 비우고 예상 units를 남기는 기존 friction provider 형태는 계속 허용한다.
Prediction에 전체 values/units key 일치를 강제하는 새 전역 제약은 만들지 않았다. 관측에
필요한 computed channel의 단위·개수·모든 요청 위치의 유한 값은 기존처럼 확인한다.
원 WaterForward의 report-grid/time 의미와 inverse_localization의 추가 채널 소비 계약도
소스로 대조했으며 이 두 모듈의 함수·Prediction 필드는 변경하지 않았다.

공급자 호출 전후에 실제 parameters dictionary와 실행 context를 확인한다. 공급자가 그
입력을 변경하면 거부한다. point의 ID/group/state/source 검사에는 요청 전 ID snapshot을
사용해 ID가 바뀌어도 dictionary KeyError가 아니라 명시 계약 오류로 거부한다. 반환
Prediction은 detached snapshot으로 캐시하고 trace의 parameter dictionary도 복사한다.
공급자가 하나의 mutable Prediction을 재사용하거나 이전 인자 alias를 보관하더라도 이미
검증한 캐시/trace 내용을 나중 호출이 덮어쓰지 않게 한다. source 일관성은 withheld로
보류하기 전에 검사하므로 source 변경을 계산 보류 뒤에 숨길 수 없다. 최초 withheld에도
반환된 source SHA는 기록한다. 이는 source가 실제 계산 성공을 증명했다는 뜻이 아니다.

물리 단위의 RMSE/MAE/bias와 normalized RMSE는 최대 잔차로 스케일한 뒤 계산한다.
최종 값은 표현 가능한데 단순 square/sum의 중간 단계만 overflow하는 경우를 피한다.
실제 residual/normalization이 비유한 경우에는 여전히 거부하며 미상 값을 0으로 바꾸지
않는다. 이 수치 안정화는 잔차의 확률분포·표준불확실도·독립성을 추정하지 않는다.

추가 회귀는 시간 input/Prediction의 model_copy 우회, withheld의 다른 asset/time, provider의
parameter/context/point ID 변경, 공유 prediction/cache와 trace alias, source가 다른 withheld,
큰 유한 잔차의 중간 overflow를 다룬다. 시험 정의는 작성했으나 이번 검토에서 pytest,
SciPy 계산, 물리 provider/native 또는 driver 실행은 하지 않았다. 원 시간 API·새 운전점
API·water/localization/friction 통합 수치 검증은 누리온 대기다.


## 선택적 공동 잔차 metric/covariance

`OperatingPointFitRequest.residual_metric`의 기본값은 None이다. 이때 기존 ResidualScale
정규화·loss·kernel·반환 의미를 유지한다. 시간 FitRequest/fit_parameters의 서명·schema·
출력 키는 바꾸지 않았다. 공동 행렬은 운전점 경로에만 명시적으로 추가한다.

계약은 `ResidualRow(point_id, channel_id, unit)`와 다음 모델이다.

```python
OperatingPointResidualMetric(
    kind="covariance",  # 또는 deterministic C인 "residual_scale_matrix"
    rows=tuple_of_explicit_rows,
    matrix=tuple_of_rows_or_None,
    source_sha256=reviewed_source_sha256,
    evidence_id="actual_metric_evidence",
    definition_id="reviewed_error_or_scaling_definition",
    applicability_reviewed=True,
    maximum_rows=512,
    maximum_matrix_cells=262144,
)
```

rows는 전체 보고 point×channel 조합을 정확히 한 번씩 포함하며 순서는 명시적으로 선택한다.
각 unit은 실제 ObservedChannel.scale.unit과 정확히 같아야 하고 coherent SI를 확인한다.
행렬 C_ij의 단위는 두 행 unit_i×unit_j다. **precision/inverse covariance 행렬을 받는
계약이 아니다.** residual_scale_matrix는 같은 단위의 양의 정부호 joint scaling C이며
오차 공분산이라고 자동 해석하지 않는다. covariance도 공급자가 선언한 공분산이지
측정 데이터에서 여기서 추정한 공분산이 아니다.

matrix 또는 개별 원소의 None은 미상으로 유지한다. metric evidence ID는 실제 context에서
유일해야 하며 알려진 evidence SHA와 metric source SHA가 다르면 거부한다. evidence SHA
자체가 미상이거나 미검토/선택 block 미상이면 forward provider·optimizer를 부르지 않고
withheld, fitted_parameters=None, model_evaluations=0, evaluations=[]로 남긴다. 형상·중복 행·
잘못된 단위·명시 source 충돌·비대칭·선택 block의 비정부호는 입력 오류로 거부한다.
해시 참조 일치는 matrix가 실제 원 파일에서 적절하게 산출됐다는 독립 인증이 아니다.

row/cell 상한은 raw before-validator에서 float 변환 전에 확인한다. 기본 512행/262144셀,
허용 최대 2048행/4194304셀이며 외부 tuple의 각 행/열도 2048개를 넘지 못한다. caller가
선택한 더 작은 상한도 먼저 적용한다. fit wrapper는 model_copy 입력의 크기도 복사 전에 확인한다.

### 마스크·분할과 중복 정규화 방지

원 valid mask는 관측 축을 압축/재번호화하지 않고, 각 split의 실제 유효 residual row만
principal submatrix로 선택한다. 선택 순서는 metric.rows의 선언 순서를 유지한다.
선택한 covariance C의 Cholesky를 **다시 분해**하며 전체 행렬의 whitening 결과에서 일부
행만 잘라 쓰지 않는다. 그렇게 자르면 다른 관측을 조건으로 한 오차를 섞을 수 있기 때문이다.

training은 C_train,train만 사용한다. heldout은 C_heldout,heldout marginal block만 쓰고
training 잔차로 conditioning하거나 Schur complement를 만들지 않는다. 알려진 cross-split
상관은 request와 receipt에 남긴다. 미상 cross-split 값을 0으로 채우지 않는다. 값이 알려진
0이어도 무상관을 독립 실험/독립 분포로 승격하지 않는다.

모든 원소가 알려진 C는 전체 SPD도 확인한다. 일부가 미상일 때에는 선택한 두 principal
block의 SPD만 확인하고 full_matrix_spd_confirmed=None으로 남긴다. 이것은 전체 부분 행렬에
유효한 joint covariance completion이 존재한다는 증명이 아니다. 선택되지 않은 행·미상
cross block의 물리적 적합성은 원 matrix 공급자 검토에 남는다. jitter·의사역행렬·prior·
명목 독립 보정을 사용하지 않는다.

잔차는 물리 단위 그대로 C로 whitening한다. 수치 conditioning의 표준편차는 C의 대각에서
얻고, 크기가 작은 표준편차로 먼저 나누어 극단적으로 다른 단위 크기에서도 표현 가능한
상관이 중간 underflow로 없어지는 일을 피한다. 원래 ResidualScale.value를 추가로 나누지
않는다. 기존 scale 객체는 user가 선언한 단위/근거 metadata로 유지하며 값은 미적용임을
기록한다. 새 경로의 normalized_rmse는 실제 joint-whitened 잔차 RMS다. 원 channel별 물리
RMSE/MAE/bias는 그 원 단위로 계속 기록한다. optimizer/Jacobian/local rank도 같은 training
whitening을 사용하여 별도 optimizer를 만들지 않았다.

공동 metric이 있으면 loss="linear"만 허용한다. Cholesky row order나 다른 whitening 회전에
따라 componentwise Huber/soft_l1의 의미가 달라지므로 조용히 robust loss를 적용하지 않는다.
metric 없는 기존 scalar 경로의 robust loss 지원은 그대로다.

### receipt와 검증 한계

metric을 선택한 결과의 residual_metric에는 다음을 기록한다.

- kind/definition_id/source_sha256/evidence_id/metric_digest와 전체 row_order
- training_rows/heldout_rows/masked_rows/unused_rows
- scalar_residual_scale_values_applied=False, training_block, heldout_block
- cross_split_block_known, cross_split_block_all_zero(bool 또는 None)
- cross_split_independence_assumed=False, distribution_assumed=None
- full_matrix_spd_confirmed(True 또는 None), selected_principal_blocks_spd_confirmed
- 실제 factor 계산 시 native NumPy/SciPy version·LAPACK binary SHA, 미계산 시 native=None

최적화의 수치 loss는 확률·Gaussian likelihood·posterior·confidence interval이 아니다.
field_validation_claim/independent_experiment_validation/parameter_confidence_intervals_calculated는
계속 False다. 기존 같은 장치 holdout도 독립 검증으로 승격하지 않는다.

새 전용 시험은 analytic quadratic form과 explicit row permutation, 비영 cross-split에도
heldout marginal 유지, 미상 cross block의 전역 SPD 미주장, valid mask principal 선택,
미상/잘못된 matrix의 forward 전 보류/거부, scalar 중복 정규화 방지, robust 제한, raw 예산,
극단적 크기의 상관 보존과 legacy scalar 경로를 다룬다. `tests/test_operating_point_inverse.py`는
작성만 했으며 pytest/수치/native/provider/driver를 실행하지 않았다. 기존 time/point 시험과
friction 소비자 통합 검증은 누리온에서 이어간다.
