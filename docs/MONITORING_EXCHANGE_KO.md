# 일곱 모니터링 family의 분리 학습·보정·평가 경계

`monitoring_exchange`는 기존 process-monitoring-toolkit(PMT) `0.1.0`의
`Monitor`, `create_detector`, `SignalBatch`, `WindowFeatures`를 호출한다.
기존 `adapters.monitoring_batch`와 `SignalFrame`을 재사용하며 원 검출기·적합·
특징 계산·예측·보정 quantile 알고리즘을 복제하지 않는다.

아래 59개 등록 ID의 작은 작성 3채널 사례에서 fit→별도 calibrate→held-out
score를 실행했다. 이는 일곱 family의 **표에 명시한 방법과 시험 조건**의 인터페이스
확인이다. 각 분야의 모든 기법·입력 형상·모수 지원·현장 검출 성능을 검증한
것으로 표시하지 않는다. classification·incremental·subsequence·engineering family는
이 경계의 범위 밖이다. 신경망은 원 프로젝트의 연구용 구현이며 특정 논문의 실험 결과나
외부 benchmark와 동일한 성능을 재현했다는 뜻이 아니다.

| 이쪽 family | 원 registry family | 확인한 원 ID |
|---|---|---|
| statistical | statistics | robust_z, zscore, ewma, cusum, gaussian, seasonal, distribution_tail |
| classical | classical | pca, fuzzy_cmeans, kmedoids, isolation_forest, one_class_svm, lof, knn_distance, kmeans, gmm, ard, bayesian_ridge, ridge_residual, genetic_kmeans, genetic_fuzzy_cmeans |
| forecasting | forecasting | arima, lag_ridge, lag_ard, lag_bayesian_ridge |
| modern | modern_library | ecod, copod, hbos, abod, loda, sod, sos, loci, cblof, feature_bagging, pca_pyod, rod, inne |
| dynamic_subspace | subspace_temporal | dynamic_pca |
| neural | neural | autoencoder, sparse_autoencoder, denoising_autoencoder, vae, gan, diffusion |
| temporal_neural | temporal_neural | rnn, lstm, gru, cnn, tcn, transformer, cnn_lstm, lstm_autoencoder, graph_conv, graph_attention, graphsage, conv_lstm, deep_graph_infomax, resnet |

## API와 입력 전체

`evaluate_monitoring(request, splits, context, *, client=None, owner_source_root=None)`는
`MonitoringRequest`, 순서 있는 세 `MonitoringSplit`, `RunContext`를 받는다.
검토·근거·원 버전/spec/source pin 또는 전체 parameters가 미상일 때는
원 엔진 import 전에 `needs_data`와 `native_receipt=None`을 반환한다.
잘못된 입력·단위/시계·관측 중복·예산·핀은 예외이며 임의 수리하지 않는다.

| MonitoringRequest 필드 | 의미 |
|---|---|
| family, method | 위 일곱 family와 원 등록 ID. 실제 원 spec의 family와 normal training을 확인 |
| asset_id, clock_id | context와 같은 설비/자료 범위와 분석 시계 |
| population_id | 사용자가 검토한 적용 모집단. 독립성의 자동 판정이 아님 |
| split_policy | `chronological_within_run` 또는 `separate_physical_runs` |
| parameters | 원 `create_detector`에 전달할 JSON kwargs. `{}`는 고정 소스의 기본값 선택, `None`은 미상 |
| features | `MonitoringFeatures(window=1, statistics=("last",), max_gap_s=None)`가 기본 |
| normalize | 명시 bool. 일반 경로는 training만으로 scaler를 적합. lag 내부 정규화는 원 검출기 소유 |
| false_alarm_fraction | 0 초과 1 미만의 보정 quantile 정책. 보장된 미래 오경보율이 아님 |
| history_policy | `reset_each_split`만 지원. 앞 단계의 이력을 조용히 붙이지 않음 |
| expected_owner_version | 선택한 PMT 버전. 이번 실행은 `0.1.0` |
| expected_native_spec_digest | 원 `DetectorSpec` 메타데이터의 digest |
| owner_source_tree_sha256 | 실제 선택한 원 source tree pin |
| applicability_reviews, review_evidence_ids | 아래 네 검토 키의 bool과 선택 근거 ID |
| evidence_ids | context에 존재하는 중복 없는 근거 tuple |
| maximum_rows_per_split | 기본 20,000, 허용 2~100,000 |
| maximum_channels | 기본 128, 허용 1~512 |
| maximum_feature_cells | 기본 2,000,000, 허용 1~5,000,000. 행·채널·창/특징 크기의 사전 한도 |
| maximum_parameter_nodes | 기본 10,000, 허용 100~100,000. 깊이 15와 함께 직렬화 전 검사 |
| maximum_training_epochs | 기본 100, 허용 1~10,000. 신경망의 명시 epochs 상한 |
| maximum_hidden_units | 기본 1,024, 허용 1~65,536. 신경망 hidden 크기 상한 |
| maximum_training_work | 기본 100,000,000, 허용 1~10¹². training 행×채널×창×hidden×epochs의 사전 예산 |
| cpu_threads | 기본 1, 허용 1~32. 신경망 CPU intra-op thread 상한 |

검토 키는 `reference_and_calibration_population`,
`source_clocks_channels_units_and_missingness`,
`heldout_observations_and_causal_window_endpoints`,
`parameter_support_and_score_meaning`이다. 기본은 미검토다. 모든 true review에는
선택한 context evidence가 필요하다. 근거 ID가 있다는 것만으로 물리적 주장의
진위가 증명되지는 않는다.

`MonitoringFeatures`의 window는 1~2,000이다. 원 지원 statistics는 last·mean·std·
min·max·rms·slope·range·median·spectral_entropy이며 중복 없이 선언한다.
max_gap_s는 양의 경과 초 또는 명시 `None`이다. Native spec에서 temporal인
세 lag 방법과 dynamic_pca·temporal_neural은 자체 window/max_gap_s를 사용한다. 이때 features는 identity 기본값을
유지해야 하며 원 검출기의 변환을 다른 feature recipe로 표시하지 않는다.
원 ARIMA는 매 scoring segment의 상태를 다시 시작한다.

각 `MonitoringSplit`의 전체 필드는 role, split_id, physical_run_id, source_group_id,
source_evidence_id, observation_ids, frames, reference_kind다. role 순서는 training,
calibration, evaluation이다. 각 단계는 한 물리 run이며 원 관측 ID는 세 단계
전체에서 중복되지 않도록 source/run을 포함한 ID를 쓴다. frames는 `SignalFrame`
tuple이고 원 stream·clock·채널 순서·단위·origin을 보존한다. 순서를 바꾸거나
다른 단위로 이름만 바꾸지 않는다. UTC가 있으면 그대로 보관하고 없으면 만들지 않는다.

`reference_kind`는 training/calibration에서 `verified_normal` 또는
`caller_declared_reference`여야 한다. 미상은 `unresolved`다. evaluation은
`not_used`이며 정상 학습 표본으로 재사용하지 않는다. 이 API는 정상성·운전 조건·
라벨 진위를 자동 판별하지 않는다. 라벨·결과·미래 시각 정보가 측정 feature에
섞이지 않았는지도 caller가 검토해야 한다.

## 시간·물리 run·결측·지연

같은 run의 시간 분할은 source group·물리 run·stream·source clock·origin이 같고
training 끝 < calibration 시작 < evaluation 시작이어야 한다. 같은 관측을 ID만
바꾸어 다른 단계에 복사하지 않는다. 별도 run 분할은 세 물리 run과 세 source
group이 모두 달라야 한다. 각 run의 경과 시계는 원대로 유지하고 0에서 시작하는
시계를 임의 달력 시간으로 옮기지 않는다. source clock과 분석 clock은 기록에서
구분하며 이 어댑터가 시계 정합·리샘플링을 수행하지 않는다.

점수는 원 행 끝점에 정렬한다. 매 단계의 초기 창 미완성, 결측·mode 전환·
선언된 gap을 가로지르는 창은 원 상태를 유지한다. 창 4의 초기 3행이 미계산인
것과 score=0은 다르다. `None`은 valid=False와 함께 보관하며 NaN/Inf를 JSON
null로 바꾸기 전에 거부한다. 값을 보간하거나 invalid 행을 삭제해 시각을 압축하지
않는다. 특징 적합에서 원 엔진이 사용할 수 없는 행을 제외하는 것과 원 관측 행을
삭제하는 것은 구분한다. 각 단계는 native batch와 source digest를 보존한다.

lag 모델은 training에서 자신의 중심·척도를 적합한다. normalize=False가 이를
끄는 것은 아니다. 일반 경로의 PMT scaler는 training만 사용하며 calibration과
evaluation에서 다시 fit하지 않는다. 단계 간 history는 공유하지 않는다.
따라서 창에 의한 첫 점수 가용 시각과 실제 통신/처리 지연·탐지 지연은 별개다.
후자의 물리적 latency를 이 점수에서 생성하지 않는다.

## 신경망·동적 부분공간 설정

neural과 temporal_neural은 `parameters`에 `hidden`, `epochs`, `batch_size`, `seed`를
명시한다. temporal_neural에는 `window`도 필요하다. 이 중 빠진 값은 미검토 기본
학습으로 대신하지 않고 native import 전에 needs_data다. sequence의 실제 window를
행×채널×창 예산에 포함하며, epochs/hidden/명목 학습량도 직렬화 전에 확인한다.
이 명목량은 FLOP나 wall time의 보장이 아니다. 큰 모델은 별도 Python client의
제한시간과 함께 실행한다. 신경망의 노이즈·optimizer·학습·추론은 원 PMT/PyTorch가 소유한다.

이번 작성 조건은 hidden=4, epochs=2, batch_size=16, seed=73, temporal window=4다.
autoencoder 계열과 GAN의 latent=2, GAN latent_steps=2, diffusion steps=4와
noise_levels=[0.25,0.75]를 선택했다. 이는 CPU 경로·결정적 재실행을 확인할 작은
학습 예산이며 권장 학습량이나 수렴·검출 성능의 근거가 아니다. temporal의
max_train_windows=48은 원 엔진의 seeded 학습 창 선택 상한이다. 전처리의 정상
중심/척도와 optimizer가 이용한 창 수를 동일한 개수라고 가정하지 않는다.

graph_conv·graph_attention·graphsage·deep_graph_infomax는 정렬된 채널 순서와 같은
크기의 명시 adjacency가 필요하다. 이번 3센서 인접 행렬은 작성한 선형 연결이며
실제 배관 토폴로지나 학습된 인과 관계가 아니다. conv_lstm은 같은 채널 순서와
곱이 일치하는 spatial_shape=[1,3]을 명시했다. 공간 좌표나 연결을 자동 추정하지 않는다.
원 GraphInfomax의 정상 embedding 거리와 diffusion의 고정 query-noise 재구성 점수도
확률이 아니다. LSTM autoencoder의 마지막 행 재구성과 다른 sequence 모델의
마지막 행 예측은 같은 물리적 탐지 지연이나 손실함수라고 가정하지 않는다.

실행 중 CPU device·thread 수·deterministic algorithms를 지정하고 CPU RNG와
thread/결정성 설정을 반환 시 복구한다. 확인한 Windows runtime은 CUDA 없는 CPU
빌드다. GPU의 재현성·상태 복구·성능은 검증하지 않았다. 별도 worker를 사용하면
호출자의 라이브러리 환경과 상태도 프로세스로 분리할 수 있다.

`neural_training`은 명시 seed/epochs, 각 epoch의 원 loss, 실행 버전/CPU 조건과
학습 직후 및 calibration/evaluation 뒤의 tensor·전처리 배열 SHA/shape/dtype을 남긴다.
두 상태가 다르면 결과를 거부한다. GAN의 query latent 최적화가 generator/discriminator
가중치를 학습하는 것으로 바뀌지 않았는지도 같은 검사로 확인한다. 이 배열 해시는
가중치나 pickle을 배포하지 않고 저장 일관성을 확인하며 학습 데이터 적합성이나
최적화 수렴을 증명하지 않는다. 검증기는 재해시한 기록에도 seed·epoch 수·loss의
개수/유한값·CPU·원 버전·미변경 tensor 상태를 다시 검사한다.

## 원 버전·소스와 client 교체

`MonitoringClient` protocol은 `describe(method, *, expected_source_sha256)`와
`run(recipe, splits, context)`를 제공한다. `NativeMonitoringClient(source_root)`는
선택한 원 소스를 같은 Python에서 사용한다. `PythonMonitoringClient(python_executable,
source_root, maximum_seconds=180, maximum_payload_bytes=64000000)`는 선택 Python의
JSON worker를 사용한다. 시간은 최대 1,800초, JSON은 최대 64MB이며 shell을
실행하거나 pickle 모델을 읽지 않는다. client의 로컬 경로는 요청/공개 결과에 넣지 않는다.
worker의 의존성·입력 오류는 결과 대신 `RuntimeError`이며 `client.last_diagnostics`에
있는 로컬 진단을 확인한다. 이 진단의 stderr/원 오류는 공개 집계에 넣지 않는다.

`monitoring_source_fingerprint(root)`는 정렬된 Python 상대 파일명/바이트 tree SHA와
파일 수, LICENSE·THIRD_PARTY_NOTICES·존재하는 추가 license/JSON 파일의 해시를
반환한다. 실제 import한 package·Monitor·registry factory·SignalBatch·feature와
생성한 detector class가 선택 소스에 속하는지 확인한다. 실행 전후 소스와 고지가
변하면 결과를 거부한다. 같은 프로세스의 소스 선택은 lock으로 직렬화한다.

이번 원 소스는 28 Python 파일, tree SHA-256
`a8856f3e30f7bddf7539dbe0b74ea7d47ada4127f52f765bf61e4a134300fe0a`다.
MIT 원 라이선스와 고지는 Process Monitoring Toolkit contributors의 소유다.
원 코드·라이선스를 수정하지 않았다. 환경의 라이브러리도 별도 배포의 고지를
유지하며 이 저장소에 vendor하지 않는다. pin과 record digest는 기록 일치 검사이며
전자서명·공급자/실행 진위 인증이나 현장 성능 증명이 아니다.

원 registry에는 생성자 전체의 정형 JSON Schema가 없다. 따라서 spec digest는
이름·family·training·extra·temporal의 원 메타데이터를 고정하며 생성자 계약은
원 버전/source pin과 실제 `create_detector` 검증에 따른다. 무시된 kwargs나
새 원 버전을 자동 승인하지 않는다. custom client도 같은 source/spec/context/
split/결과 envelope를 제공해야 하며 실제 검증 책임은 그 공급자에게 있다.

`verify_monitoring_record`는 바깥 기록과 중첩 native receipt의 digest, source/
고지 pin, 원 recipe·spec·context·split, window/scaler 적용, 원 시각/채널/valid,
보정 threshold와 strict `score > threshold` 경보를 확인한다. 검출 점수는
방법별 abnormality index이며 서로 같은 척도·물리량·확률이라고 가정하지 않는다.
경보는 HEP·기간 고장확률·관측 사고·실제 조치가 아니다.

## 설치·사용·재현

주 개발 환경을 변경하지 않고 Python 3.12의 별도 환경에서 다음 직접 의존성으로
확인했다. NumPy 2.3.5·SciPy 1.18.1·scikit-learn 1.9.1·pandas 2.3.3,
statsmodels 0.15.0·PyOD 3.6.7·combo 0.1.3·Numba 0.68.0·llvmlite 0.50.0이다.
Numba의 확인한 NumPy 제약은 <2.6이다. 이 목록은 전체 전이/플랫폼 잠금 파일이
아니며 별도 설치 시 원 패키지의 호환 제약도 확인한다. 신경망 추가 실행에는
공식 PyPI의 torch 2.14.1을 별도 환경에 설치했다. 실제 런타임은 2.14.1+cpu,
CUDA build=None이다. 확인한 Windows CPython3.12 wheel은 124,113,906바이트이며
공급자 SHA-256을 취득 기록과 대조했다. 다른 OS wheel의 크기를 이 값으로 추정하지 않는다.
출처는 [공식 설치 안내](https://pytorch.org/get-started/locally/)와
[공식 배포 메타데이터](https://pypi.org/pypi/torch/2.14.1/json)다.

설치된 torch 메타데이터의 License-Expression은 Apache-2.0, LLVM 예외가 붙은
Apache-2.0, BSD-2-Clause, BSD-3-Clause, BSL-1.0, MIT의 결합이다. PMT의 MIT로
이 전이 의존성 전체를 다시 표시하지 않는다. 원 배포의 라이선스와 제3자 고지를
보존하며 외부 환경·바이너리·학습 가중치는 이 패키지에 넣지 않는다.

```powershell
python -m venv .venv-monitoring
.venv-monitoring\Scripts\python.exe -m pip install "numpy==2.3.5" "scipy==1.18.1" "scikit-learn==1.9.1" "pandas==2.3.3" "statsmodels==0.15.0" "pyod==3.6.7" "combo==0.1.3" "pydantic==2.14.1" "pint==0.26.2" "cloudpickle==3.1.2"
.venv-monitoring\Scripts\python.exe -m pip install "torch==2.14.1"
.venv-monitoring\Scripts\python.exe scripts/check_monitoring_exchange.py --monitoring PATH_TO_REVIEWED_PMT --worker-python .venv-monitoring\Scripts\python.exe
python -m pytest tests/test_monitoring_exchange.py -q
```

아래 예는 원 버전과 pin을 아직 선택하지 않은 상태의 보류를 실행한다.
원 모니터링 패키지나 optional 모델을 import하지 않는다.

```python
import runpy
from pathlib import Path
from pipeline_integrity.monitoring_exchange import (
    NativeMonitoringClient,
    PythonMonitoringClient,
    evaluate_monitoring,
    verify_monitoring_record,
)

request, splits, context = runpy.run_path("examples/monitoring_exchange.py")["make_case"]()
source_root = Path("path-to-reviewed-pmt")
direct = NativeMonitoringClient(source_root)
isolated = PythonMonitoringClient(Path(".venv-monitoring/Scripts/python.exe"), source_root)
record = evaluate_monitoring(request, splits, context, client=isolated)
verify_monitoring_record(record)
assert record["status"] == "needs_data" and record["native_receipt"] is None
```

실제 계산은 선택 root의 fingerprint와 `client.describe`에서 얻은 버전/spec digest로
요청을 구성한다. source/메서드·kwargs·window·정규화·지원 모집단을 바꾸면 새 근거와
독립 평가가 필요하다. 예제의 계수나 작성 intervention을 현장 고장 라벨로 옮기지 않는다.

## 실행 증거와 공개 자료 적용

원 실행 수와 분야별 기준 수는 공개 `monitoring_exchange_results.json`을 따른다.
59개 smoke에 더해 기존 4작성 사례, dynamic PCA의 SPE/T² 2작성 사례,
ECOD 미래 관측 불변, 신경망 20종의 같은 seed 재실행과 미래 관측 변경,
15개 추가 sequence의 결측/gap/mode 경계, 원자료 7분야와 JSON/direct 왕복을 실행한다.
독립 작성 수치·미상 기준, 미래 관측/평가 변경 불변, 창 가용성 계약, seed 재현은
서로 다른 증거 종류로 따로 센다. 신경망 출력값 자체를 독립적인 물리 정답으로 세지 않는다.
이번 실행은 native 137사례이며, 독립 작성 수치/미상 기준29·미래 관측 불변88·
창 가용성17·seed 재현20·CPU 상태 복구2, 합계156개 기준이다. 신경망 원 receipt의
가중치·seed·epoch·loss·device·thread·버전·미학습 주장 변조10개도 바깥/안쪽 해시를
다시 계산한 뒤 거부했다. 집중 pytest64개와 문서 Python 예제1개를 별도로 실행했다.
거부 8개에는 관측 재사용·버전/spec/pin/예산/NaN 위반과 아래 원 지원 실패가 있다.
일곱 family smoke의 실제 실패/미확인 ID도 같은 집계에서 확인한다. 물리 성능의
미확인 입력·기법까지 검증했다는 의미는 아니다.

[BattLeDIM 원 자료](https://doi.org/10.5281/zenodo.4017659)의 2018 pressure/flow/level
CSV 세 파일은 기존 공급자 MD5와 로컬 SHA를 확인했다. 첫 3일 864행을 하루
288행씩 training/calibration/evaluation으로 분리했다. 선택 단위는 pressure head m,
volume flow m³/s, water level m이며 압력 수두를 절대압으로 바꾸지 않았다.
동일 network simulation 한 run이므로 세 독립 실험이나 현장 검증으로 세지 않는다.
첫째/둘째 날은 caller가 선언한 reference라는 별도 가정 근거를 두었고 검증된
정상 라벨로 표시하지 않았다. 실제 source origin은 simulation이다.

zscore·PCA·HBOS에 더해 dynamic PCA·diffusion·GraphInfomax도 같은 세 일자와
세 원 채널에서 비교한다. lag/dynamic/graph window4의 원 초기 3행은 미계산으로
남는다. 실제 유효 개수는 공개 집계를 따른다. 이 유효 개수는 탐지 정확도·오경보율·
누설 진단 성능이 아니다. 원 행·단위·시계·결측/품질을 유지했고 평가 관측을
fit/calibration에 넣지 않았다. source citation/license·해시·집계와 실행 버전만
`research/monitoring_exchange_results.json`에 남기며 원관측·상세 score/receipt는
`artifacts/monitoring_exchange`에 둔다. CC BY 4.0 원 저자/버전은 그 집계의
원 catalog citation을 따르고 원 CSV와 변환 행을 공개 배포에 넣지 않는다.

## 확인한 한계와 PI-X05 후속

선택한 PMT 소스와 scikit-learn 1.9.1에서 단일 채널 `lag_ridge`는 fit의 2D target과
1D prediction이 broadcast되어 calibration 때 shape 오류를 낸다. 해당 요청은
점수를 내지 않았고 거부 사례로 보존했다. 원 소스를 수정하거나 가짜 채널을
추가해 우회하지 않았다. 독립 수치 기준은 원래부터 선언한 2출력 작성 회귀다.
CBLOF도 선언한 군 분할 조건을 만족하지 못하면 원 ValueError를 유지한다.
성공한 smoke의 n_clusters/alpha 설정은 추천값이나 자료에 적합한 현장 모수가 아니다.

원 PMT 소유자에게 단일 출력 shape·버전별 kwargs schema·normal-reference provenance,
원 행/clock/run·창 가용성/latency·상태 reset·source/spec/환경 pin을 수신 계약에
포함하도록 요청한다. 실제 정상/고장 truth·기간/조건·검사 단위·독립 source-group
holdout와 미상률을 제공받기 전에는 성능·확률 보정을 주장하지 않는다.
modern의 선택 방법들은 모두 작은 smoke를 실행했지만 별도 미래 관측 불변성
대조는 기존 library 경로의 ECOD와 새 신경망 20종의 명시 조건에서 수행했다.
모든 구현/파라미터에 대한 인과성 증명은 아니다. Dynamic PCA의 독립 작성 기준은
고유방향이 구분되는 2열 lag 공분산의 SPE/T²와 미상 창을 확인한다. 신경망의
같은 seed 재현은 같은 설치·CPU·thread·작은 설정에서의 비교이며 다른 버전/장치의
bit 단위 일치나 실제 공정에서의 우수성을 보장하지 않는다.
작은 입력의 성공은 큰 행렬의 계산량·수렴 보장이 아니며, wall-clock 제한이
필요하면 별도 Python client를 선택한다. 기존 HRA/기간 위험의 사건 정의·기회·
생존·방호 중복 검토 없이 점수나 경보를 확률 입력으로 만들지 않는다.
