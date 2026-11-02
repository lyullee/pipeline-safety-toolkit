# 음압파 도착 특징과 조건부 방향

`pressure_wave.evaluate_pressure_wave(request, context)`는 두 Pa 압력 신호에서
음의1차 차분 압력 변화율 특징을 찾고 입력한 시계/모델 오차로 도착시각 차이의
구간을 구성한다. 기존 `acoustics.SampledTrace`·`RelativeTiming`·공통 문맥을
재사용하고, SciPy `find_peaks`로 검토한 크기/돌출도 정책을 실행한다.
구현한 것은 제한된 특징/시각 어댑터다. 일반 음압파 원인/도달 검증·다중 경로와
실측 성능은 남아 해당 전체 기술을 부분 구현으로 유지한다.

## 입력과 재생

`PressureWaveRequest`에 좌/우 trace, absolute/gauge/dynamic_perturbation 압력 기준, Pa/s의 최소
음의 변화율과 돌출도, 사건 ID·근거, 특징 정책 검토를 넣는다. trace는 실제 자산/
시계·센서/원 관측 hash·시작 시각/주기·원 값/valid를 갖는다. 양쪽 pressure/Pa와
생성 origin이 문맥과 일치해야 한다. 단위 변환·정렬·결측 보간·재표집을 하지 않는다.

도착 구간에는 상대 채널 latency의 bias/절대 오차, 각 특징→실제 도착의 모델
오차와 동일 사건의 검토가 필요하다. 오차는 전체 분석창의 동기/clock drift를
포함해 검토해야 한다. 없는 보정을0으로 넣지 않는다. 각 신호의 명시한 주기로
처리하며 서로 다른 sample rate를 같다고 가정하지 않는다.

실측 또는 실험실 origin의 두 trace에는 `source_pair_qualification`이 필요하다. 영수증이
없거나 `needs_pairing_evidence`이면 SciPy 특징 탐색 전에 `needs_data`로 반환한다. ready
영수증도 seal/schema, context evidence ID, P1/P2 sensor ID, 각 원자료 SHA와 압력 기준이
현재 trace와 일치해야 한다. 따라서 파일명이나 같은 시간 좌표만으로 실제 분석이 시작되지
않는다. 합성·시뮬레이션의 독립 연구 기준은 이 자료 자격 경계와 구분해 유지한다.

`boundary_metadata(request)`를 `context.metadata["pressure_wave_boundary"]`에
넣어 원 trace·사건·압력 기준·위상과 전체 요청 SHA를 연결한다. threshold·돌출도·상대 latency/
오차·검토 플래그를 바꾸면 새 boundary 검토가 필요하다. `verify_pressure_wave_record`
는 요청/문맥·현재 SciPy 특징/시각·자격을 재생해 재해시한 변조도 거부한다.
이 검사는 전자서명이나 입력 사건/실행 이력의 진위 증명이 아니다.

자료 자격 영수증은 `pressure_wave_data.qualify_pressure_pair`에서 만들고
`verify_pressure_pair_qualification`로 원 bytes까지 재생한다. NPW 요청에는 원자료를
다시 복사하지 않고 그 source-bound 영수증을 결합한다. 영수증이 ready여도 누출 원인,
발생시각, 도착 정답, 위치 또는 확률은 생기지 않는다.

CSV record에서 실제 trace를 만들 때에는
`pressure_wave_data.build_pressure_pair_trace_record`를 사용한다. 이 브리지는 원자료
SHA, clock, 명시 시간원점 이동과 고정격자 오차를 보존하며 재표집하지 않는다. 반환한
P1/P2 trace와 같은 qualification을 요청에 넣어야 이 모듈의 자료 경계가 이어진다.

`bind_pressure_wave_pair_request(...)`는 trace record와 qualification의 caller-pinned
digest를 확인한 뒤 두 센서를 좌/우 역할에 배치한다. P1을 왼쪽으로 추정하지 않으며
호출자가 `left_sensor_id`, `right_sensor_id`와 `sensor_role_evidence_id`를 명시한다.
역할 근거와 분석 근거는 실제 `RunContext.evidence`의 고유 항목이어야 한다. trace의
asset/clock/origin과 qualification의 센서별 source SHA도 문맥과 다시 대조한다.

반환 문맥에는 전체 request boundary, 좌/우 역할과 근거 ID, trace-record digest가
결합된다. 이후 threshold·역할·근거·qualification·trace가 바뀌면 기존 문맥으로
`evaluate_pressure_wave`를 호출할 수 없다. 상태는
`research/pressure_wave_request_binding_status.json`을 따른다.

## 특징·도착·방향·원인은 별개의 값

특징은 압력 차분의 내부 peak다. 단일 peak의 support는 해당 인접 두 샘플 시각이다.
평탄한 peak는 native SciPy의 plateau left/right edges를 받아 plateau 전체 차분 구간을
포함한다. 가운데 index의 한 차분 구간으로 축소하지 않는다. peak_rate_index는 native
대표 index이고 left/right_sample_index 및 feature_support_s가 실제 전체 지지구간이다.
가장 큰 peak를 골라 모호성을 숨기지 않는다. 조건에 맞는 특징이 여러 개이거나
없거나 창 끝에서 하강이 잘리면 needs_data다. 원 값이 없거나 invalid인 구간도
압축/채움으로 제거하지 않는다. 부동소수점 overflow는 지원 밖으로 거부한다.

특징 차이의 구간은 [오른쪽 하한−왼쪽 상한, 오른쪽 상한−왼쪽 하한]이다.
보정 bias를 빼고 두 모델 오차와 동기 절대 오차만큼 확장한다. 정규분포·독립성/
신뢰수준을 가정하지 않는다. 실제 압력 전선의 정확한 첫 도착을 임의의 peak
시각으로 단정하지 않으며, 모델 오차가 없으면 도착/방향을 보류한다.

구간 전체가 양수면 left, 음수면 right가 먼저 받는다.0을 포함하면 indeterminate다.
동일 파가 두 센서를 순서대로 지나는 `through_pair` 위상을 검토했을 때만
`conditional_travel_direction`을 제공한다. `source_between`이면 두 방향으로
갈라질 수 있어 receiver 순서만 남긴다. 위치·누설 원인·고장 확률은 계산하지 않는다.

## 검증과 기존 압력파 연동

```powershell
python -m pytest tests/test_pressure_wave.py -q
python scripts/check_pressure_wave.py
```

작성한 계단 신호의 부호·sample support·bias/오차·모호한 방향, 다중/창 끝 특징·
결측·기준압/단위·생성 origin·재해시 변조를 검사한다. 기본 미검토 예제는 native
검색을 실행하지 않는다. 정확한 스키마는 `examples/pressure_wave.py`를 따른다.

같은 예제의 `make_moc_case`는 기존 액체 특성선 엔진을 실제 호출한다.1000m 경로의
300/700m 두 센서에 대해1ms/2ms 격자를 적용했다. 오른쪽 경계의 명시 유출 증가로
생긴 하강 전선은 거리/파속 기준−0.4s를 포함하는 도착 구간을 만들고 오른쪽→왼쪽
순서를 재현했다. 원 체적 수지를 함께 확인했다. 모사·격자 기준이며 독립 현장 검증은 아니다.

이 사례 자체가 압력 하강의 원인이 누설로 유일하게 결정되지 않는 반례다.
`leak_detected`, `leak_probability`, `position_m`는 None이다. 기존 감시/HRA에
전달할 때도 특징을 HEP·누설 판정/발생 시점으로 바꾸지 않는다. 대응 근거·사건
분류·sensor/포트 역할과 실제 도달 오차를 별도로 검토해야 한다.

[실행 집계](../research/pressure_wave_results.json)에는 실제 소스/런타임·작성 기준을
남기고 전체 입력/파형·MOC 결과는 배포 밖 `artifacts/pressure_wave`에 둔다.
실제 음압파 자료·다중 경로/반사/분산·분류/오경보·원인 구분·시계/계측 응답의
실험적 오차 보정은 후속이다. 기존 음향 상관/위치 계산을 새 peak 모듈에 복제하지 않았다.

네이티브 특징 정의와 endpoint/plateau 동작은
[SciPy 공식 API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html)를 따른다.
설명·예제와 검증 조건은 독립적으로 작성했다.


## 교차 정적 검토 후 변경

전체 요청 boundary 결합과 flat-top peak의 전체 시간 support 보존을 보강했다. 설치된
SciPy `_peak_finding.py`의 공개 plateau_size/left_edges/right_edges 계약을 정적으로
대조했다. 새 plateau·검토 조건 변경 회귀는 작성했지만 실행하지 않았다. 기존 33개
통과 기록은 이전 소스에 대한 이력이며 이번 변경의 수치 통과를 의미하지 않는다.
