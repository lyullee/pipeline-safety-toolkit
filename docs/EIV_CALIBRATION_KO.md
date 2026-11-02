# 두 축의 오차와 전체 공분산을 보존하는 교정

`pipeline_integrity.calibration`은 입력 x와 응답 y 모두에 불확실도가 있는
관측쌍을 적합한다. 점 사이의 공유 오차와 x·y 사이의 상관을 포함한 전체
표준 공분산을 사용한다. 물리 파라미터와 각 관측의 잠재적인 참 x를 함께
구하며, 수치 최적화는 기존 `inverse.solve_bounded_least_squares`를 통해
SciPy `least_squares`에 위임한다. 독자적인 최적화기나 샘플러는 추가하지 않는다.

기존 물리 모델의 시간 이력 보정은 [CALIBRATION_KO.md](CALIBRATION_KO.md)의
`inverse` 서비스를 사용한다. 여기서는 관측 번호나 Reynolds 수를 물리 시간으로
취급하지 않는다. 두 서비스는 같은 네이티브 최적화 진입점을 공유한다.

## 빠른 실행

프로젝트 루트에서 다음 코드를 실행한다. 기본 예제는 검토되지 않은 조건이므로
`needs_data`이며, 서비스의 SciPy 최적화·선형대수 백엔드를 호출하지 않는다.
공통 단위 라이브러리 Pint는 자체 선택 의존성 탐색을 할 수 있다.

```python
import runpy

from pipeline_integrity.calibration import fit_calibration, predict_calibration

make_case = runpy.run_path("examples/calibration.py")["make_case"]
request, context, provider = make_case(reviewed=True)
record = fit_calibration(request, context, provider)
assert record["status"] == "computed"
result = predict_calibration(record, [2.0, 4.0], context, provider)
```

`reviewed=True`는 원래 작성한 수치 예제의 검토 선언이다. 실제 설비에 사용할 때는
이를 복사해 적합성을 승인한 것으로 취급하지 않는다. 측정량 정의, 공분산 산출,
모델 범위, 물리적인 시험 분리와 국소 선형화의 근거를 다시 연결한다.

## 요청과 자료 계약

`CalibrationObservation`은 `id`, `run_id`, `population_id`,
`role`(`fit`, `validation`, `excluded`), `x`, `y`, `valid`, `evidence_id`를 갖는다.
`observation_time_s`는 선택적인 실제 관측 시각이며 적합 좌표로 바뀌지 않는다.
시각이 없는 출처에는 시각을 만들지 않는다. `run_id`는 같은 시험의 행을
서로 다른 시험으로 세는 용도로 사용하지 않는다.

`CalibrationRequest`의 주요 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `asset_id`, `clock_id`, `event_id` | 적용 대상, 관측 시간 체계와 분석 사건 |
| `x_basis`, `y_basis` | 공통 `MeasurementBasis`; SI 단위, 정의, 압력 기준과 필요한 기준 상태 |
| `observations` | 최대 32개 관측쌍; 누락과 무효를 구분 |
| `covariance` | 공통 `StandardCovariance`; 표준 공분산과 검토 근거 |
| `parameters` | 공통 `FitParameter`; 물리 단위, 유한 경계와 초기값 |
| `x_domain` | 모델이 허용하는 물리 입력 영역과 잠재 x의 경계 |
| `model_id`, `model_version` | 호출할 모델의 식별자 |
| `provider_source_sha256`, `configuration_sha256` | 실제 콜백 소스 및 명시적 설정의 핀 |
| `exact_x_ids`, `exact_x_evidence_id` | 명시적으로 참값이 정확한 입력을 고정할 경우의 선언 |
| `split_policy` | `separate_runs`, `separate_populations`, `fit_only` |
| `require_independent_validation` | 별도 검증 자료와 오차의 독립성을 요구하는지 여부; 기본 참 |
| `max_nfev`, `max_model_calls` | 최적화와 실제 모델 호출의 별도 예산 |
| `evidence_ids`, 검토 사전 | 실제 `RunContext.evidence`의 선택과 검토 근거 |

관측 ID가 `a`, `b`이면 공분산 순서는 `x:a`, `x:b`, `y:a`, `y:b`다.
`covariance_order(ids)`로 생성한다. 대각 항은 분산이며 표준편차가 아니다.
대칭 항은 정확히 같아야 하고, 상관을 모를 때 `None`을 사용한다. 누락된 상관을
0으로 채우거나 전체 행렬을 대각화하지 않는다. 최대 32점은 공통 공분산 계약의
64개 좌표 한계다. 더 큰 자료를 32점마다 나누어 공유 오차를 없애면 안 된다.

`RunContext.metadata["calibration_boundary"] = boundary_metadata(request)`는
사건, 모델, 측정량 정의, 행 순서, 분리 정책과 입력 영역을 묶는다. 자산·시계와
근거의 실제 소속도 검사한다. 이름만 같은 근거 문자열은 승인으로 취급하지 않는다.

필수 검토 키는 모듈의 `REVIEWS`에서 확인한다. 필요한 값이나 검토가 없으면
`needs_data`다. 무효 관측을 활성 행에 둔 경우, 불가능한 상관, 지원하지 않는
특이 오차, 영역 위반, 실패한 최적화는 `out_of_scope`다. 무효 값을 자동으로
빠뜨리지 않는다. 제외할 행은 요청에서 `excluded`로 명시한다.

## 모델 공급자와 단위

`affine_provider(x_unit=..., y_unit=..., slope_unit=...)`는
`y = offset + slope*x`를 제공한다. 계수 이름도 바꿀 수 있다. 기울기 단위가
입력 단위를 출력 단위로 연결하는지 검사한다. `model_identity(provider)`의
두 핀을 요청에 넣는다.

비선형 공급자는 `CalibrationProvider(model_id, version, x_unit, y_unit,
parameter_units, predict, configuration, derivatives=None)`로 만든다.
`predict(x, parameters, configuration)`는 물리 x 배열과 물리 단위의 계수
사전을 받아 같은 길이의 유한 y 배열을 반환한다. 콜백은 실제 파일에 있는
최상위 함수여야 한다. 숨은 클로저 대신 설정을 명시한다. 외부 상태를 변경하거나
호출할 때마다 난수를 만드는 모델은 이 결정론적 재생 계약에 적합하지 않다.

선택적 `derivatives(x, parameters, configuration)`는 계수별 y 미분 사전과
각 점의 x에 대한 y 미분 벡터를 반환한다. 이 경로는 점별 모델용이다. 전체 x에
서로 의존하는 모델은 미분을 생략해 SciPy가 전체 수치 Jacobian을 계산하도록
한다. 제공된 미분은 적합 후 실제 모델의 중앙 차분과 비교한다. 미분이 없으면
네이티브 3점 차분을 사용한다. 전역 식별 가능성은 국소 Jacobian 검사만으로
보장되지 않는다. 여러 초기값이나 다른 모델의 비교는 연구자가 별도로 수행한다.

## 계산과 불확실도의 의미

적합 행의 공분산 주부분행렬만 최적화에 사용한다. 잔차는
`[latent_x - observed_x, model(latent_x, beta) - observed_y]`다.
표준편차로 무차원화하고 전체 상관행렬의 Cholesky 인자로 백색화한다.
정규화한 계수와 잠재 x에는 명시된 유한 경계를 적용한다. 최적화 손실은
선형 제곱합이며, 알려진 측정 불확도를 임의의 강건 손실로 바꾸지 않는다.

활성 관측의 전체 확률적 공분산은 양의 정부호여야 한다. 수치 안정화를 이유로
jitter를 더하거나 고유값을 자르거나 의사역행렬을 사용하지 않는다. x의 영 분산은
해당 입력이 `exact_x_ids`에 있고 근거가 있는 경우만 허용한다. 그 행/열의 공유
공분산은 공통 계약에 따라 0이어야 한다. 이때 잠재 변수를 제거하고 관측 x를
고정한다. 정확한 y, 일반적인 특이 공분산의 선형 제약은 아직 지원하지 않는다.

반환 계수 공분산은 전체 가중 Jacobian 정보행렬에서 잠재 x를 주변화한 계수
블록이다. 측정 공분산이 이미 주어졌으므로 잔차 분산이나 축소 카이제곱으로
재조정하지 않는다. 이는 국소 Gauss–Newton 근사이며 자동으로 신뢰구간,
사후분포나 보장 확률이 되지 않는다. 활성 경계나 랭크 부족, 수치적으로 표현할 수
없는 분산에서는 이 무제약 공분산을 발표하지 않는다. 원래 네이티브 진단은 남긴다.

`audits`에는 국소 랭크, 특잇값, 자유도, 활성 경계와 네이티브 성공 여부를 둔다.
자유도는 확률적 잔차 수에서 계수와 잠재 x의 수를 뺀 값이다. 단일 출력의
확대불확실도나 허용 판정이 필요하면 별도 기준과 감도를 공통 `metrology`에
연결한다. 이 서비스는 적용 입력의 불확도를 0으로 가정하지 않는다.

## 적합·검증 분리와 적용

`separate_runs`는 적합과 검증의 시험 ID가 겹치지 않게 한다.
`separate_populations`는 모집단 ID가 겹치지 않게 한다. 선언한 물리 분리의
타당성은 근거로 검토해야 한다. 검증 y는 계수 선택이나 초기값에 사용하지 않는다.
검증 단계는 적합 후 한 번의 전방 예측으로 잔차를 보고한다. 불확실한 관측 x에서의
이 잔차를 독립적인 참값 오차나 합격 판정으로 바꾸지 않는다.

적합과 검증 사이의 공분산이 하나라도 0이 아니면 통계적으로 독립된 오차라고
표시할 수 없다. 기본 설정은 이를 거절한다. 명시적으로
`require_independent_validation=False`를 선택하면 상관된 검증의 설명적 잔차를
반환하고 그 한계를 기록한다. `fit_only`는 검증 행이 없고 독립 검증 요구가 거짓일
때만 허용한다. 같은 캠페인의 30점을 임의로 두 집단으로 나눠 독립 검증으로
세지 않는다.

`predict_calibration(record, x_values, context, provider)`는 재생 검사를 통과한
결과에서 전방 모델을 적용한다. 관측된 적합 x의 최솟값·최댓값을 벗어나면
외삽을 거절한다. 넓은 물리 `x_domain`만으로 실제 교정 범위를 넓히지 않는다.
계수의 역함수나 입력의 자동 반전은 제공하지 않는다. x를 계기 지시값, y를
기준량으로 정의하면 이 전방 함수가 보정값을 반환한다. 다른 모델은 같은 의미를
가정하지 않는다. 시간열 `SignalFrame`은 관측 시각이 없는 자료에서 생성하지 않는다.

## 공개 자료 적용과 수치 검증

[자료 안내](CALIBRATION_DATA_KO.md)의 PTB·LNE EMUE sonic-nozzle 자료를 실제로
읽어 사용한다. 출처는 [Zenodo 4016916](https://doi.org/10.5281/zenodo.4016916),
재사용 조건은 CC BY 4.0이다. 원자료와 출처 처리 기록은 배포에서 제외한
`data/raw`와 `artifacts`에 보관한다. 코드의 MIT 조건과 자료의 표시 의무를 구분한다.

공개된 x=`Re**(-0.5)`, y=`Cd` 30점과 전체 60×60 행렬을 그대로 전달했다.
비대각 원소 3,540개를 모두 유지한다. 2018년 7월 15일의 단일 캠페인이며 관측
시각은 없다. 원시 유량의 Type A 성분만으로 총불확실도를 재구성하지 않고,
출처가 제공한 중간량의 표준 공분산을 사용했다. 추가 난수 표본은 만들지 않았다.
이 예제의 `RunContext.origin="laboratory"`는 실험에서 정의한 측정량의 추정값이라는
뜻이다. `metadata.source_origin`에는 공급자의 Monte Carlo 전파 결과라는 실제
기원을 남기고, 원본 멤버 해시와 표준 공분산 척도도 함께 보존한다. 원시 센서
관측이나 독립적인 신규 실험으로 표시하지 않는다.

| 항목 | 실행 결과 | 출처의 반올림 기준값 |
|---|---:|---:|
| 절편 | 0.9966305635 | 0.99663 |
| 기울기 | −3.5267395836 | −3.5267 |
| 절편 표준불확실도 | 0.0009607332 | 0.000961 |
| 기울기 표준불확실도 | 0.0482652337 | 0.04827 |
| 계수 간 공분산 | −0.00002534725 | −0.0000253 |

이는 공개 수치 기준의 재현이다. 새로운 독립 실험 검증이나 거래 계량 자격을
얻은 것으로 표시하지 않는다. 별도 물리 검증 캠페인이 확보되지 않아 이 출처의
계산은 명시적인 `fit_only`다.

```powershell
python -m pytest tests/test_calibration.py -q
python -m ruff check src/pipeline_integrity/calibration.py tests/test_calibration.py examples/calibration.py scripts/check_eiv_calibration.py
python scripts/check_eiv_calibration.py
```

해석적인 Deming 해, 명시적인 정확 x의 가중 선형대수 해, 비선형 지수 모델의
알려진 계수·정보행렬, 공분산 배율 불변성, 공유 오차, 검증 자료 변경 불변성과
재해시한 저장 결과 변조를 검사한다. 상세 네이티브 입력·출력은
`artifacts/calibration`에, 공개 집계와 현재 파일 해시는
`research/eiv_calibration_results.json`에 기록한다. 집계에는 전체 관측 자료를 넣지 않는다.

## 백엔드 선택과 실패 기록

기본 백엔드는 현재 환경의 SciPy 1.18.1·NumPy 2.5.4이며 기존 설치를
업그레이드하지 않았다. SciPy의 [`least_squares`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)
공개 API를 기존 공통 진입점으로 호출한다. SciPy·NumPy의 배포 조건은
[SciPy](https://github.com/scipy/scipy/blob/main/LICENSE.txt)와
[NumPy](https://github.com/numpy/numpy/blob/main/LICENSE.txt)를 확인한다.

별도 네이티브 비교에는 [`odrpack` 0.7.0](https://hugomvale.github.io/odrpack-python/reference/)을
실제로 실행했다. SciPy의 기존 `odr`는 공식 문서에서 폐기 예정으로 안내되므로
새 생산 의존성으로 추가하지 않았다. odrpack도 이 서비스의 생산 경로에는 필요 없다.

Windows CPython 3.12 wheel은 2,610,981바이트, 공급자 SHA-256은
`e1a8bed77c6f828b53a2959da0a8e9fbcec4b3c2514956f92576eaca85cd3007`이다.
정확한 wheel을 `--no-deps`로 추가했고 다른 설치 버전은 바꾸지 않았다.
Python/ODRPACK95의 MIT 허가와 wheel에 들어 있는 컴파일러 런타임의 허가는
같지 않다. `libgcc`, `libgfortran`, `libstdc++`, `libquadmath`, `libwinpthread`는
각 공급자의 조건과 예외를 확인해야 한다. 특히
[GCC 런타임 허가](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html)와
[Fortran 링크 안내](https://gcc.gnu.org/onlinedocs/gfortran/Link-Options.html)를 참조한다.
이 저장소는 해당 바이너리나 wheel을 재배포하지 않는다. 사설 비교 기록에는
설치한 바이너리의 실제 해시를 남긴다.

일반적인 독립 점의 odrpack 비교는 성공했다. 실제 `irank=0`은 랭크 결손이
없다는 네이티브 진단으로 보존했다. `sd_beta`는 잔차 분산으로 배율 조정돼 있어
주어진 절대 측정 오차의 불확실도로 그대로 쓰지 않는다. 반면 모든 측정량을 한
벡터 관측으로 묶은 전상관 ODR 실험은 실제로 `info=10010`, 관측 수보다 계수 수가
많다는 실패를 반환했다. 관측을 가짜로 반복해 이를 우회하지 않았다. 전체 공분산
생산 경로를 공유 최소제곱으로 선택한 이유와 이 실패 출력을 함께 보존한다.

이 선택적 비교를 다시 실행하려면 설치한 odrpack 0.7.0 환경에서
`python scripts/check_eiv_calibration.py --odr-reference`를 사용한다.

## 저장 결과 검사와 확장 요청

`verify_calibration_record(record, provider, request=..., context=...)`는 요청,
공통 경계, 콜백·설정 핀, 현재 공유 엔진과 네이티브 수학을 다시 실행해 비교한다.
결과에 붙인 해시만 다시 계산해서 계수·공분산·상태를 바꿔도 통과하지 않는다.
현재 소스의 수치 일관성 검사이며 과거 실행의 전자서명이나 승인 증명은 아니다.

남은 확장은 다음과 같이 구체적으로 요청한다.

1. 공통 공분산 계약에 64좌표를 넘는 자료용 희소/블록 연산, 실제 좌표 순서와
   공유 성분 식별을 추가한다. 자료를 임의로 독립 블록으로 나누지 않는다.
2. 정확한 y와 일반적인 특이 공분산은 측정 제약식과 영공간의 근거를 받는
   명시적인 제약 최적화가 필요하다. 현재의 양의 정부호 경로에 작은 분산을
   넣어 흉내 내지 않는다.
3. 비선형 계수의 국소 근사를 넘어서는 불확실도는 기존 `uncertainty` 표본 생성기와
   실제 전체 측정 법칙을 연결한다. 독립 정규 오차나 coverage factor를 만들지 않는다.
4. 별도 시험/모집단 검증, 환경 드리프트, 동특성, 계기 교체 이력과 거래 규정의
   결정 절차는 실제 자료와 근거를 추가해야 한다. 현재 적합 결과만으로 인증하지 않는다.
