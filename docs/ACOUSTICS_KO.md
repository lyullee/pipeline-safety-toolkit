# 두 센서 시간차와 조건부 배관 위치

`acoustics`는 같은 구간을 측정한 두 신호의 지연을 추정한다. 정규화 상관과
GCC-PHAT를 선택할 수 있으며 SciPy의 상관·필터·코히어런스와 FFT를 사용한다.
누설 존재를 분류하는 검출기는 기존 모니터링 프로젝트에 둔다. 상관이 크거나
두 신호가 비슷하다는 사실만으로 누설이라고 판단하지 않는다.
지연 계산에는 engineering, 공개 CSV 판독에는 data 추가 의존성을 설치한다.

## 실행과 데이터 연결

```powershell
python examples/acoustic_delay.py --write-job artifacts/acoustic_job.json
pipeline-integrity acoustic-delay artifacts/acoustic_job.json --output artifacts/acoustic_result.json
python scripts/acquire_data.py --only sheffield_acoustic --workers 1 --maximum-seconds 180
python scripts/check_acoustics.py --monitoring PATH_TO_MONITOR
```

첫 예제는 독립적으로 만든 25표본 지연과 100m 구간이다. 시계 편향 0·전파속도
1000m/s의 합성 조건에서 왼쪽 기준 위치 43.75m를 얻는다. 입력 신호는 예제
실행 시 생성하며 공개 실험 파형을 복제하거나 배경으로 사용하지 않는다.

`SampledTrace`에 자산·센서·시계·시작 초·표본 간격·수량·단위·생성 방식·
출처 해시·값·valid를 넣는다. 두 신호의 자산·시계·수량·단위·생성 방식과
표본 수가 같아야 한다. 작은 표본 간격 차이는 요청의 상대 한도 안에서만
허용하고 누적 드리프트를 따로 기록한다. 속도 변환·재표본화는 하지 않는다.
invalid 표본이 있으면 보류하며 표본을 지워 시간을 압축하지 않는다.

`slice_trace(trace, start_index, sample_count)`로 분석 구간을 명시적으로 선택한다.
시작 초·값·valid가 같이 이동한다. 이 함수는 두 기록의 동기화를 만들어 주지
않는다. 같은 clock_id 문자열도 하드웨어 동기화 오차가 0임을 증명하지 않는다.

## Python API

`estimate_delay(left, right, request, context)`는 해시가 있는 지연 결과를 반환한다.
`DelayRequest`에서 다음을 설정한다.

- 방법, 최대 지연 탐색 범위, 분석 주파수 대역, 필터 차수와 detrend
- 양의 상관 또는 절댓값 극성, 최소 겹침 비율, 최소 상관 크기
- 경쟁 봉우리 제외 폭과 상대 점수 차이, 최소 RMS, 코히어런스 진단 조건
- 표본 수 예산, 표본율 차이 한도, 탐색 경계 봉우리의 허용 여부

대역을 지정하면 전후 방향 SOS 필터를 사용한다. 이는 비인과적인 오프라인
처리이며 온라인 검출 지연으로 해석하지 않는다. 신호 크기를 먼저 정규화해
큰 센서 이득에서도 제곱·FFT의 넘침을 줄이고 원 단위의 처리 RMS를 기록한다.
단위가 같은 신호의 상수 이득 변화는 지연을 바꾸지 않아야 한다.

정규화 상관은 각 lag에서 실제 겹친 구간의 평균·분산을 사용한다. GCC-PHAT는
교차 스펙트럼을 위상 중심으로 정규화하고 역 FFT를 적용한다. 매우 작은
스펙트럼은 상대 바닥 1e-12에서 제외한다. 두 방법 모두 선택한 lag에서 실제
정규화 상관 크기를 확인한다. PHAT의 원시 봉우리 점수는 상관계수나 확률이 아니다.

경쟁 봉우리가 충분히 떨어지지 않거나, 신호가 약하거나, 탐색 끝에 봉우리가
있으면 기본 정책에서 보류한다. 제외 폭보다 탐색 범위가 너무 좁아 경쟁점을
검사할 수 없는 경우도 표시한다. 코히어런스의 한 구간 추정은 정보가 부족할
수 있어 구간 수와 informative를 함께 보존한다. 기본 품질 임계값은 사용자가
바꿀 수 있는 분석 정책이며 검증된 오경보율·정확도·검출확률이 아니다.

지연은 정수 표본 단위이며 자동으로 소수 표본 정밀도를 주장하지 않는다.
lag k의 부호는 오른쪽 도달시각에서 왼쪽 도달시각을 뺀 방향이다. 두 파일의
시작 시각이 다르면 apparent_delay에 그 차이를 더한다. 시간차만 얻을 때
RelativeTiming이 없어도 분석할 수 있다.

`RelativeTiming(right_minus_left_latency_s, absolute_error_bound_s, source_id)`를
주면 센서·시계 상대 지연을 보정한다. 이 경우
`corrected_delay = k*dt + start_right - start_left - relative_latency`다.
정정 시간차의 기본 오차 경계는 반 표본·누적 표본율 차이·선언한 상대 시계
경계의 합이다. 잡음과 봉우리 추정 오차까지 포함한 통계 구간은 아니다.

## 조건부 위치 계산

`locate_on_span(delay_result, span, context)`는 원 지연 결과의 상태·문맥·해시를
확인한다. `PipeSpan`에는 좌우 센서 위치와 경계, 전파속도와 경계, 유속과
경계, 추가 시간차 오차, 근거 ID를 넣는다. source_bracketed와
single_path_nondispersive가 참이어야 한다. 그렇지 않거나 상대 시간 근거가
없으면 needs_data다. 상관 결과에서 이 조건들을 자동 승인하지 않는다.

왼쪽에서 오른쪽으로의 평균 유속을 u, 파속을 c, 센서 간 거리를 D,
정정 시간차를 τ라고 할 때 왼쪽으로부터의 거리는

`x = [D*(c-u) - τ*(c*c-u*u)] / (2*c)`

이며 좌표에는 왼쪽 센서의 기준 위치를 더한다. u=0이면 `(D-c*τ)/2`다.
전파속도 경계 전체에서 c>|u|를 요구한다. 센서 간 길이·방향 전파 시간·
시간차·위치를 구간 연산으로 계산해 보수적인 위치 경계를 반환한다. 같은
변수가 여러 번 등장해 경계가 넓어질 수 있으며 확률적 신뢰구간이 아니다.
명목 위치가 구간 밖이면 보류하고 결과를 센서 경계로 잘라 맞추지 않는다.

전파속도는 해당 파동 모드와 주파수 대역의 근거를 사용한다. 기존
`transient.wave_speed` 또는 물성 공급자의 계산을 사용할 때도 관벽·재질·
모드·주파수 가정이 이 자료와 맞는지 검토한다. 점탄성 플라스틱관의 분산,
반사·분기·다중 소스는 이 단일 경로 계산의 범위를 넘는다. 두 센서만으로
구간 밖 소스와 끝점 소스를 구분하기 어려운 상황도 있다.

## 모니터링 연결

`delay_feature_frames(results, stream_id=...)`는 시간차·부호 있는 상관·상대
봉우리 차이·평균 코히어런스를 기존 SignalFrame으로 만든다. 같은 자산·시계·
원자료 생성 방식과 증가하는 분석 시각을 요구한다. 보류된 창은 valid=False로
유지한다. 특징의 생성 방식은 simulation이며 원자료 laboratory 등은 별도
metadata로 전달한다. `monitoring_batch`에 자산·원 결과 해시·입력 생성 방식을
주어 실제 기존 SignalBatch로 연결할 수 있다. 특징을 HEP·사고 빈도로 바꾸지 않는다.

## Sheffield 자료의 판독 범위

취득 파일은 492,926,144바이트 ZIP이며 공급자 MD5와 로컬 SHA-256을 확인했다.
40개의 동시 기록은 두 누설 형식·거리 1~10m·각 반복 기록으로 구성된다.
표본율은 8192Hz다. CSV가 이미 가속도 m/s²를 제공하므로 센서 감도를 다시
곱하거나 나누지 않는다. hydrophone Pa 열은 이 두 가속도 분석에 섞지 않는다.

`SheffieldArchive`는 원본을 한 번 해시한 읽기 수명 동안 크기/수정 시각 변화를
검사한다. 파일을 수정하면서 같은 reader를 사용하지 않는다. 선택한 member·
시작 표본·개수·판독 코드·단위·시계를 추적한다. CSV의 실제 시간 열이 정해진
격자에 맞는지도 확인한다. 부분 읽기를 전체 파일 읽기라고 표시하지 않는다.

All_Data의 여러 거리 열은 여러 실험을 결합한 행렬이다. 이 판독기는 해당
행렬을 두 센서 동시 기록으로 받아들이지 않는다. Coherence (simultaneous
recording) 폴더의 명시된 CSV 스키마만 지원한다. 참고 센서는 누설에 매우
가까우나 정확한 오프셋 오차가 없고, 전파속도와 상대 채널 지연 오차도
미확정이다. 압력 이름의 bar를 절대압력으로 바꾸지 않는다.

## 현재 실행 결과와 한계

40기록의 첫 4초씩을 100~2000Hz·절댓값 극성·최대 지연 0.1초라는 고정 설정으로
비교했다. 이 설정을 결과별로 최적화하지 않았다.

| 방법 | 신호 정책 통과 | 보류 |
|---|---:|---:|
| 정규화 상관 | 39 | 1 |
| GCC-PHAT | 28 | 12 |

보류 사유는 선택된 lag에서 상관이 약한 경우였다. 이 통과 수를 정확도나
기법 우열로 해석하지 않는다. 실제 도달시각 정답과 전파 모델이 없으므로
위치 오차를 산출하지 않았다. 누설 없는 동시 기록을 이용한 검출 성능이나
오경보 평가도 수행하지 않았다.

한 실제 기록을 7개 비중첩 4초 창으로 나눠 4개 특징을 기존 모니터링 객체로
전달했다. 나머지 2초는 분석에서 제외했다고 기록했다. 같은 기록의 이 창들은
독립 실험 검증 자료가 아니다. 실제 예측·학습 검출기·HEP를 실행하지 않았다.

기계 판독 집계는 `research/acoustic_results.json`, 전체 판독/창별 결과는 로컬
`artifacts/acoustics`에 있다. 공개 패키지에는 원 신호·변환 표본·지원 문서와
그림을 포함하지 않는다. 독립 합성 시험은 부호·이득·시계 지연·유속·구간 경계,
주기적 모호성·잡음·결측·변조·단위·판독기·CLI를 확인한다.

`leak.acoustic_localization`은 partial이다. 측정 기반 파속·동기화 경계와 독립
위치 검증, 반사·분기·분산 모델이 후속 과제로 남는다. 음압파 이벤트의 발생
검출·도달 판정은 아직 별도 계획 항목이다.

## 합성 호출 API

`localize_acoustic_leak(left, right, request, span, context)`는 위 두 기존
계산을 순서대로 호출하고 입력 파형 해시, 하위 결과 해시, 센서 순서와 상태를
한 결과에 묶는다. 계산식이나 신호 처리 엔진을 새로 복제하지 않는다. 입력 두
파형의 자산·시계가 context와 다르면 즉시 거부한다. 위치 계산의 기존 근거 게이트는
그대로 적용되며 검증되지 않은 시계·파속·경로에는 `needs_data`가 반환된다.

외부 시스템은 이 함수를 라이브러리로 직접 호출하거나 다음 JSON 형식의 job을
예제 CLI에 줄 수 있다:

```powershell
python examples/acoustic_localization.py acoustic_job.json --output acoustic_result.json
```

Job은 `context`, `left`, `right`, `specification`, `span` 다섯 객체를 가진다.
span은 delay-only 요청에서는 null로 둘 수 있다. 위치를 요청하면 양끝 센서 ID를
파형의 좌·우 순서와 맞춰야 한다. 상세 필드와 단위는 각 `RunContext`, `SampledTrace`,
`DelayRequest`, `PipeSpan` 계약을 따른다. `verify_acoustic_localization_record`는
결합 영수증과 delay/location 하위 영수증의 digest 연결을 확인한다. 합성 결과는
`result_digest`로 무결성을 확인할 수 있지만 센서 교정, 이벤트 분류,
현장 정확도, 누설 확률을 의미하지 않는다. 누리온 검증 큐에 호출 계약·상태 전파·
재현 해시 검사를 추가했으며 로컬 시험은 실행하지 않았다.

## 기술·자료 출처

- [SciPy 상관 정의와 lag](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.correlate.html)
- [SciPy 코히어런스](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.coherence.html)
- [자료·작성자·이용 조건](https://doi.org/10.15131/shef.data.32229270.v1)

자료는 Mohammad Reza Shekofteh, University of Sheffield, 2026, CC BY 4.0으로
고지한다. 문장·코드·예제는 독립적으로 작성하고 원 분석 스크립트를 복사하거나
실행하지 않았다. 외부 자료의 수치·설명·라이선스는 원출처 조건을 유지한다.
