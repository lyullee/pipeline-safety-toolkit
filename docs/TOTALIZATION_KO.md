# 관측 구간 적산과 공동 시간 오차

`totalization.totalize_measurements`는 정해진 관측 구간의 순 수량을 계산한다.
공통 `MeasurementInput`·`MeasurementBasis`·`StandardCovariance`와 기존 계량
불확실도 전파를 사용한다. 질량/체적의 단위를 이름만 바꾸어 에너지 계산에 넣는
방식은 사용하지 않는다. 선형 구간의 양/음 적분은 새 공통 `quadrature`에 두었고
기존 전력 적분도 같은 함수를 호출하도록 통합했다.

## 구간의 의미

| `sample_meaning` | 자료와 시간 | 모델 |
|---|---|---|
| `point_linear` | 시간마다 점 값, 값/시각의 개수가 같음 | 점 사이를 직선으로 연결하고 영점 교차에서 양/음을 분리 |
| `interval_mean` | 경계 시간 n+1개와 구간 평균 n개 | 각 구간 평균×실제 구간 길이 |
| `cumulative_counter` | 시간마다 누적 계수기 값 | 인접 계수기 차이와 전체 끝점 차이 |

순간값·구간 평균·누적값을 자동으로 서로 바꾸지 않는다. 계수기 증가/감소의 합은
주어진 끝점 사이의 증가/감소다. 관측하지 않은 구간 내부의 왕복 흐름이나 계수기
reset/wrap을 알아냈다는 의미가 아니다. reset 부재와 부호 의미를 근거로 검토한다.

`TotalizationRequest`에는 자산/계기·시계·사건·명목 양의 포트 쌍, 시계 offset,
`readings`·`times`, 출력 수량의 기반, 최대 허용 간격과 검토 근거를 넣는다.
각 reading/time은 `MeasurementInput`이며 time 단위는 `s`, 정의 ID는 clock ID다.
출력 단위는 rate×초 또는 계수기 단위와 일치해야 한다. 같은 조성·습분·기준 상태를
유지한다. 지역 액체 체적을 건조/표준 기체 체적으로 변환하지 않는다.

`boundary_metadata(request)`를 `context.metadata["totalization_boundary"]`에 결속한다.
명목 부호는 포트 쌍 기준이며 실제 설비 포트의 대응 근거도 문맥에 보존한다.
`nonnegative` 또는 `signed_nominal_ports`를 명시한다. 음수가 금지된 흐름·감소하는
계수기, 명시적 invalid 자료는 지원 밖이다.

미상/품질 미정·긴 공백은 구간을 채우지 않는다. 알려진 구간의 기여는
`known_interval_net_amount`에 남기지만 `complete_net_amount=None`이다. 전 구간이
알려져 있을 때만 완전 총량을 표시한다. 마지막 관측 뒤의 tail·연간 운전시간·다른
회차 사이의 흐름을 만들지 않는다. 기본 미검토 예제는 아무 적산도 실행하지 않는다.

## 시간과 수치 범위

입력 시간은 정렬하지 않고 알려진 값의 엄격한 증가를 요구한다. offset을 더했을 때
시간이 같아지거나 역전되면 거부한다. 표현된 구간 길이의 변형은
`clock_representation_tolerance_s` 이내여야 하며 기본값은 1ns다. 큰 원점을 가진
시계는 호출자가 근거 있는 허용값을 지정할 수 있다. 실제 최대 표현 오차를 결과에
남기며 이 값을 무작위 측정 불확실도라고 자동 가정하지 않는다.

양/음 적분은 mantissa/exponent로 곱과 비를 평가해 표현 가능한 결과를 중간
overflow/underflow 때문에 잃지 않도록 한다. 비영 수량 자체가 표현 범위를 벗어나면
0이나 무한대로 내보내지 않고 거부한다. 두 최소 subnormal 값을 각각 반으로 나눠
부적합으로 판단하던 반례는 endpoint 합을 먼저 스케일하는 방식으로 수정했다.

## 공동 공분산

전체 오차 벡터의 순서는 readings 뒤 times다. 입력별 표준 분산뿐 아니라 reading과
시간, 여러 시점 사이의 모든 공분산을 보존한다. 같은 시계 offset의 오차가 모든
시각에 공통이면 기간과 순 총량에서 상쇄될 수 있고, 개별 시간 오차는 남을 수 있다.
유량이 계수기 값/시간으로 계산됐다면 유량과 시간의 상관관계를 없애지 않는다.

출력은 `net_amount`와 `window_duration`의 2차원 국소 공분산이다. 민감도는 선택한
적산 모델에서 계산하고 기존 `metrology.propagate_measurement_uncertainty`에 전달한다.
확대 계수·명시 판정도 같은 계약을 사용한다. 양/음 구간 수량 각각의 불확실도는
별도로 계산하지 않으며 순 수량의 불확실도와 혼동하지 않는다.

`uncertainty_required=True`가 기본값이다. 공분산·국소 근사 검토가 없으면 명목 총량을
남겨도 최종 상태는 `needs_data`다. 전체 공분산의 처리 한계는64좌표이며, 그보다 큰
창의 명목 총량은 남기고 불확실도는 지원 밖으로 표시한다. 공통 오차를 잃는 창 분할이나
독립성은 자동 적용하지 않는다. 명목 계산은 최대65,536 readings를 받는다.
명목 적산만 원하면 `uncertainty_required=False`를 명시하며 0불확실도를 뜻하지 않는다.

## 실행과 실제 자료

```powershell
python examples/totalization.py
python scripts/check_totalization.py --raw-root data/raw
```

[예제](../examples/totalization.py)의 검토된 point/평균 모델은10kg, 계수기 모델은5kg을
준다. 시간 오차를 포함한 순 분산은 각각0.0374/0.0524/0.02kg²다. 이는 직접 작성한
수치 기준이며 실측 정확도를 뜻하지 않는다. 기록은 요청/문맥·구간·공분산·공통 코드
해시를 포함하고 `verify_totalization_record`가 현재 수학을 다시 계산한다.
재해시한 총량·시각·공분산·소스 변경도 검사한다.

`totalization_data.vsl_counter_period_cases(record, source_root=...)`는 기존 VSL reader의
검증된 원자료를 재사용한다. 실제 계수기 시간이 있는224구간 중160개는 질량,
64개는 지역 액체 체적이다. 각 계수기 기간을 독립된 상대 시계0..τ로 정의하고
해당 MUT의 구간 평균×τ를 계산한다. 원 wall 시작/종료나 UTC를 계수기 시간으로
대체하지 않고 기준기의 평균 창도 MUT와 같다고 가정하지 않는다.

유량/시간 공분산은 미상이므로224개 명목 수량이 있어도 불확실도는 보류한다.
계수기 시간이 없는 터빈50구간에는 시간을 채우지 않는다. 서로 다른 시험을
연속 운전·누적 재고로 합산하지 않는다. 출처/권리와 실제 온도·압력·밀도 기반은
[계량 자료 안내](METER_DATA_KO.md)와 각 상세 문맥에 남긴다. 원자료·274개 상세
기록은 배포 밖에 저장하며 [공개 집계](../research/totalization_results.json)는 숫자와
검증 상태만 포함한다. EMUE의 MC 파생30점은 시간 이력이 아니므로 적산하지 않는다.

이는 연구용 측정 모델이다. 센서 drift·복합 reset·일반 singular 교정·현장 승인·
거래 계량 인증은 별도 범위다. 계산 결과에는 `custody_transfer_certified=False`를 보존한다.
