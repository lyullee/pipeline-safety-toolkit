# 공개 관 마찰 자료의 조건부 모델 연구

`friction_study.py`는 확보된 `pipe_friction_roughness` 자료의 실제 6개 운전점을 기존 유압·역산 계산기에 연결한다. 입력 해석과 빠진 경계 조건이 검토될 때만 forward 진단을 수행한다. 선택적 보정은 명시적 원 운전점 ID 분할과 잔차 척도·경계 검토를 사용하거나, 실제 외부 측정 시계가 있는 기존 시간 경로를 사용한다. 자료의 저자가 이미 구한 파라미터를 독립 정답으로 재사용하지 않는다. 이 작업은 코드 준비이며 연구의 수치 결과나 현장 검증 완료가 아니다.

## 실제 소스에서 확인한 내용

`friction_data.py`, 확보된 `dataset.csv`, `metadata.yml`, `README.md`를 소스 텍스트로 읽었다. CSV는 Hin/Hout/Q/nu 열과 6개 운전점을 가진다. head는 m, 유량은 m³/s, 동점도는 m²/s이다. 물의 동점도는 온도에서 추정한 값이며 별도 독립 점도 측정이라고 표시하지 않는다. 같은 시험 배관의 운전 조건을 바꾼 자료이므로 6개의 독립 장치 실험으로 세지 않는다.

metadata의 물리 직관 길이는 84.58 m, 내경은 0.0486 m, 중력은 9.79 m/s²이며 elbow 18개가 보고되어 있다. 코드가 이 값들을 기본값으로 자동 채우지 않는다. 그 자료에는 적합된 상대 거칠기와 등가 길이도 있으나 이를 physical straight length나 검증 정답으로 가져오지 않는다. source metadata가 권하는 보정값 활용 방식과 이 연구의 독립 근거 요구는 구별한다. 거칠기는 이 API에서 절대 길이 m이고 상대 거칠기와 혼동하지 않는다.

밀도, 실제 압력-head 기준점 일치, 수직 고도 차, 개별 fitting의 합성 K, 관측 오차/공분산 및 각 행의 실제 측정 시각은 이 4개 파일만으로 확정하지 않았다. 10초 평균 구간이라는 설명을 연속 운전점의 시각 간격으로 바꾸지 않는다. elbow 개수를 경험적인 K로 바꾸거나 밀도를 임의의 물 상수로 넣지 않는다.

## API와 출처 결합

`evaluate_friction_study(request, context, source_root=...)`는 `read_friction_observations`로 실제 4개 파일의 byte/checksum/license 정책을 확인하여 새 자료 영수증을 만든다. `FrictionStudyRequest.source_record_digest`가 현재 자료 영수증과 일치해야 한다. source evidence의 SHA256도 실제 dataset.csv와 대조한다. geometry/property, 외부 시계, 잔차 척도 및 보정 경계의 근거 ID는 실제 `RunContext`에 유일하게 있어야 한다.

`friction_study_boundary_metadata(request)`로 자산·사건·시계·양단 포트·전체 요청을 문맥에 결합한다. 파라미터, 경계 조건, source pin 또는 검토 상태를 바꾸면 문맥 결합도 다시 만들어야 한다. source_root의 원본 MATLAB 파일 등을 실행하지 않는다.

공급해야 하는 head forward 입력은 물리 직관 길이/내경/중력, 고도 차, 절대 거칠기와 총 minor-loss K이다. 원 Q와 kinematic viscosity nu를 이용하는 head 비교에는 밀도가 필요하지 않다. `density_kg_m3_by_source_row`는 Pa/질량유량/동적 점도/일률이 필요할 때 6행 모두 명시적으로 공급한다. `geometry_density_viscosity_and_local_loss_boundary_reviewed` 및 `pressure_head_reference_and_elevation_reviewed`의 기본값은 거짓이다. 첫 플래그는 실제 사용한 geometry/nu/fitting 경계와, 밀도가 공급되었다면 그 근거를 검토한다. `head_interpretation`도 기본 unknown이다. 완전한 자료를 읽었다고 부족한 모델 가정을 자동 검토 처리하지 않는다.

## 기존 forward owner와 단위

실제 registry ID는 `hydraulics.liquid_loss`이다. 별도의 `hydraulic.loss` alias를 만들지 않았다. 기존 `LiquidPressureLoss`와 등록 모델을 사용하며 마찰계수식을 복제하지 않는다. 원 모델이 전이 영역을 보류하면 해당 진단도 적용 범위를 벗어난 것으로 처리한다. 사용한 correlation과 transition policy는 요청 및 native 기록에 남긴다.

모든 행의 head는 기존 공통 owner `LiquidHeadLoss`/`liquid_head_loss`에 Q, nu, geometry와 손실 경계를 전달해 직접 구하고 Hin−Hout과 비교한다. 밀도 1이나 임의 물 밀도를 넣는 우회 계산이 아니다. 두 센서 head가 같은 압력 기준을 공유한다는 근거가 있어야 한다. head를 절대압으로 바꾸거나 대기압을 추정하지 않는다.

밀도 rho가 공급되면 같은 원행 nu로 μ=ρν를 만든 뒤 기존 registry의 pressure/work 경로도 호출한다. 같은 행 양단에 동일한 공급 밀도를 적용하는 비압축성 조건의 검토가 필요하다. 그 Pa 결과를 ρg로 나눈 값과 density-free owner의 head를 독립 경로로 대조한다. 차이는 `pressure_to_head_crosscheck_residual_m`에 남고, 요청의 명시적인 절대/상대 numerical crosscheck budget을 넘으면 거부한다. 이 예산은 관측오차나 calibration 잔차 척도가 아니다.

elevation은 원 모델의 중력 항으로, fitting은 공급 K로 분리한다. 보정된 total equivalent length를 쓰면서 같은 fitting K를 다시 더하는 방식은 허용되는 근거로 제시하지 않는다. 반환에는 실제 source row, 원 관측 head 차, 예측값, signed 잔차 m과 `head_native` 영수증이 포함된다. 밀도가 없으면 rho/μ, Pa, kg/s, 압력 일률·위치에너지 일률·마찰 소산 W 및 pressure `native`는 모두 None이다. 밀도가 있으면 기존 Pa/work 영수증과 값을 유지한다. 마찰 소산을 외부 열원으로 다시 더하거나 온도 상승을 예측한 것으로 표시하지 않는다.

## 선택적 calibration: 원 운전점 ID 또는 실제 시계

`calibration`과 `operating_point_calibration`이 모두 None이면 보정을 요청하지 않는다. 두 요청은 동시에 지정할 수 없다. 실제 시간 경로는 기존 `inverse.fit_parameters`, 원 운전점 경로는 공통 `inverse.fit_operating_points`를 호출한다. 별도 optimizer·Jacobian·식별성 엔진을 복제하지 않는다. 지원 파라미터는 `roughness_m`(m), `minor_loss_coefficient`(1)이며 공급한 비음수 경계·초기값을 사용한다. 범위 전체가 원 유압 owner의 roughness domain 안에 있어야 한다. 자료에 실린 적합 거칠기를 초기값이나 정답으로 자동 입력하지 않는다. 두 파라미터의 상관/식별성이 나쁠 수 있으며 원 inverse owner의 local rank/condition/bound 결과를 그대로 읽어야 한다.

`operating_point_calibration`은 `OperatingPointFitRequest`이며 training_point_ids와 heldout_point_ids를 명시한다. `build_friction_operating_points(request, source_record)`는 `dataset.csv SHA256:row:<원행번호>` ID와 Q, nu, 공급 rho, 물리 길이·관경·중력·고도 및 SI 단위, 원 source hashes와 evidence를 만든다. 관측 시간이나 순번에서 만든 가짜 시각은 없다. 같은 공급 장치의 모든 행을 한 group으로 유지한다. 실제 원행의 상태·단위·그룹·출처 digest를 바꾼 prediction 요청은 typed provider가 거부한다. 대상 관측값은 압력 head 차 m이며 측정값과 입력 운전 상태를 구분한다.

기존 `calibration:FitRequest` 경로는 외부의 실제 6행 measurement times, clock evidence와 검토가 있을 때만 유지한다. 현재 source에 없는 0..5초나 10..60초를 만들어 넣지 않는다. `source_row_time_s`가 없어도 새 point 경로는 가능하지만, 기존 시간 요청을 자동 point 요청으로 바꾸지는 않는다. FrictionStudyRequest.clock_id는 실행 context 식별자로 유지되고, 새 OperatingPointObservations/Prediction 자체에는 clock/time_s가 없다.

`ResidualScale`은 m 단위이며 외부 근거 ID가 필요하다. 기존 `deterministic_scaled_residuals_only`는 명시적인 기술적 잔차 정규화이고 측정 표준편차나 독립 Gaussian 오차라는 뜻이 아니다. 새 `explicit_joint_residual_metric` 모드는 아래의 명시적 공급 행렬을 공통 point inverse owner에 전달한다. 기본 unknown covariance를 대각행렬이나 단위행렬로 가정하지 않는다. 통계적 신뢰구간·확률적 적합성이나 errors-in-variables 추론은 하지 않는다.

원 inverse owner는 명시한 training point IDs 또는 fitting 기간의 유효 행만 optimizer와 Jacobian에 사용하고 파라미터를 정한 다음 heldout 행을 평가한다. 같은 배관에서 분리한 행은 운전점 holdout이며 독립 실험 검증이 아니다. 보정의 scale/경계/분할 검토가 부족하거나 실제 시간 경로의 clock이 없으면 baseline forward 행을 유지하면서 전체 상태를 `needs_data`로 표시한다. owner가 budget_exhausted/not_converged/unidentifiable/withheld 등을 반환해도 fitted라고 바꾸지 않는다. 새 축이 공분산·독립성·오차 법칙을 만들어 내지 않는다.

## 재현과 source/runtime pin

기록은 전체 요청·문맥·실제 source 영수증, 원 유압 결과, 원 inverse 기록, 소유 reader/inverse/registry/builtins/hydraulic adapter와 Fluids/SciPy source 지문, 런타임 버전을 보존한다. `verify_friction_study_record(record, source_root=..., request=..., context=...)`는 실제 파일을 다시 읽고 현재 owner로 다시 계산한다. 영수증의 미상/검토/독립성 표시도 비교한다. 무서명 digest와 현재 재계산은 과거 실행자의 신원이나 과거 실행 사실을 인증하지 않는다.

## 검토 driver와 현재 실행 상태

`scripts/review_pipe_friction_source.py`는 `--source-root`만 주면 자료 영수증을 읽는 경로이다. `--request`와 `--context` JSON을 함께 공급했을 때만 조건부 study API를 호출한다. `--output`이 없으면 JSON을 stdout에 출력하고 지정하면 그 파일로 보존한다. 검토 플래그를 driver가 대신 승인하지 않는다. 보정 실행은 JSON의 명시적 calibration 요청과 모든 보정 전제에 달려 있다.

이번 작업에서는 driver, forward, inverse, native 계산, pytest 및 benchmark를 실행하지 않았다. source 텍스트 형식만 확인했고 Ruff와 Python 컴파일을 수행했다. 현재 실측 연구 결과는 생성하지 않았다. 후속 누리온 환경에서 `python -m pytest tests/test_friction_study.py -q`를 실행하며, 실제 source 파일과 원 의존성을 함께 전달해야 한다. 시험 fixture의 밀도/elevation/K 및 시계는 계약 시험을 위한 독립 작성 가정이며 원자료에 존재하는 측정 사실로 취급하지 않는다.

시험 정의에는 unknown 보류/no-native, 원 6행과 head/viscosity/pressure-work 단위, 실제 시계·척도 미상 시 fitting 차단, 명시적 train/heldout 전달과 owner budget 상태 유지, 거칠기 경계/단위, source/event 및 재봉인 변조 검출을 포함한다. 시험 작성과 정적 검사만으로 수치·현장 검증을 완료 처리하지 않는다. 전체 기술 완료 수에도 추가하지 않는다.

## 소유 확장 요청: FRICTION-X01

원 운전점 ID와 장치 group을 보존하는 train/heldout API 및 명시적 joint metric 전달은 공통 inverse owner로 연계했다. 남은 요청은 실제 관측/모델 차이 공분산의 실험 근거와 Q·nu·head의 errors-in-variables 계약이다. hydraulic/data owner에는 압력-head 센서 기준, 양단 elevation, 실제 물성 온도/밀도, fitting inventory와 독립 반복/장치 실험 자료를 source-bound 계약으로 공급하는 확장을 요청한다. 이미 적합된 거칠기/등가 길이는 이 자료의 독립 truth로 재사용하지 않는다.

밀도 없는 head 경로도 새 operating-point inverse와 기존 실제시계 inverse의 동일 typed provider를 사용한다. 관측값·잔차 척도는 모두 m이며, density None 상태는 point metadata에서도 보존한다. density 미상 자체는 head 보정을 막지 않지만 geometry/elevation/K/head 기준 및 scale/parameter bounds/group/분할 검토가 없으면 여전히 보류한다. μ/Pa/kg/s/W를 계산할 수 있다는 뜻으로 확장하지 않는다. 이번 추가 회귀는 density-free forward의 unknown 보존, rho 공급 pressure/head 대조, density 없는 point inverse 전달이다. 작성만 했고 수치/native/pytest/driver를 실행하지 않았다.

## 선택적 공동 잔차 metric

`OperatingPointFitRequest.residual_metric=None`이 기본이며 기존 scalar residual normalization 동작을 유지한다. covariance를 관측값이나 fit 잔차로 자동 추정하지 않는다. 공동 오차/척도 근거가 있을 때만 `OperatingPointResidualMetric`을 직접 넣고 `calibration_error_model='explicit_joint_residual_metric'`을 명시한다. 현재 joint 옵션은 point 축에서만 지원하며 기존 actual-time calibration을 조용히 다른 오차 모델로 바꾸지 않는다. scalar 모드를 선언한 채 joint metric을 같이 넣으면 거부한다. joint 모드를 요청하고 행렬을 공급하지 않으면 baseline head 진단을 유지하면서 calibration은 needs_data다.

metric의 `rows`는 `ResidualRow(point_id, channel_id, unit)`의 명시적 순서다. 이 자료에서는 실제 CSV SHA:row:2..7의 각 point와 pressure_head_difference_m/m 채널의 조합을 정확히 한 번씩 넣는다. source row 순서와 다른 행렬 순서도 명시할 수 있으며 consumer는 이를 자동 재배열하거나 행렬의 의미를 바꾸지 않는다. core owner가 실제 관측 좌표와 순서를 검증한다.

`kind='covariance'`는 공급한 covariance C를, `kind='residual_scale_matrix'`는 결정론적 공동 척도 C를 뜻한다. 두 경우 행렬 entry의 단위는 row_i unit × row_j unit이며, 현재 단일 head 채널에서는 m²이다. precision/inverse covariance를 직접 입력하는 계약이 아니다. covariance라는 이름만으로 Gaussian 확률 법칙·독립성·측정 정확도·신뢰구간을 도출하지 않는다. definition_id, source_sha256, 실제 evidence_id 및 applicability_reviewed를 공급해야 하고, 선택한 context 근거의 SHA는 실제 metric source_sha256와 일치해야 한다.

공통 inverse owner가 유효 training 관측에 해당하는 principal submatrix로 C^-1 quadratic whitening을 수행한다. heldout은 training 오차에 condition하지 않은 marginal submatrix로 별도 평가하며 파라미터 선택에 쓰지 않는다. 알려진 training/heldout 교차 entry는 그대로 보존하고, 미상 None을 0으로 채우거나 독립으로 가정하지 않는다. 미검토·필요 submatrix 미상은 보류하고 shape/unit/SPD 문제는 명시적으로 거부한다. robust loss 좌표 회전 의미를 임의로 정하지 않도록 metric 모드에서는 loss='linear'를 사용한다. whitening·Jacobian·식별성·heldout 수치는 모두 공통 inverse 엔진이 담당하고 friction consumer는 새 fit 엔진을 만들지 않는다.

기존 `residual_scale_m`은 공유 ObservedChannel 호환성과 m 단위 표시를 위해 여전히 사용자가 명시한다. joint 모드에서는 그 value를 행렬 whitening 위에 다시 곱하거나 나누지 않는다. consumer가 편의를 위해 scale=1 또는 대각 행렬을 만들어 주지 않는다. `scalar_residual_scale_role`과 `residual_metric_kind`에 실제 의미를 기록하며 `covariance_inferred=False`, independent validation 관련 False를 유지한다.

추가 미실행 회귀는 공급 metric의 비원순서/단위/source 보존과 typed core 전달, 실제 근거 SHA 충돌 거부, applicability 미검토의 보류, scalar/joint 의미 혼용 거부, metric 없는 joint 요청의 비대각화·보류다. 작성·Ruff·컴파일만 수행하며 수치/native/pytest/driver는 실행하지 않았다. 현장 오차 covariance나 calibration 성능 결과가 새로 확보되었다는 뜻이 아니다.

metric 근거 SHA가 미상이면 임의의 hash나 metric source 확인을 만들어 주지 않고 head forward를 보존한 채 calibration을 needs_data로 둔다. hash가 알려져 있으면서 공급 source_sha256와 다를 때만 명시 충돌로 거부한다. 이 미상 source 근거 보류의 추가 회귀도 작성했으며 실행하지 않았다.
