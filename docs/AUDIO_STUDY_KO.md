# 현장 PCM 오디오의 단일 클립 진단과 monitoring 준비 API

구현·시험 시나리오 작성 완료, 실제 수치 분석과 회귀는 누리온 실행 대기. 이번 구현에서는 로컬 driver, native signal 함수, pytest, 벤치마크를 실행하지 않았다. RMS나 PSD가 실제 확보 WAV에서 계산되었다는 주장을 하지 않는다. source reader로 자료를 확보·검토한 이력과 이번 signal 분석 구현 상태를 구분한다.

## 자료와 분석 범위

`water_acoustic_training_base`의 현장 leak/no-leak 두 RAR만 기존 `audio_data`를 통해 읽는다. 출판자가 설명한 수량은 leak 500개, no-leak 386개의 1초 음원이다. 이는 886개 독립 실험이나 서로 다른 설비/사건을 뜻하지 않는다. 원 이름과 폴더에는 선언된 조건이 들어 있지만 이를 실제 원인, 교정값, 누출 존재의 검증 정답 또는 POD로 변환하지 않는다. 제3자 웹 음원이 섞인 environmental-noise archive는 현재 catalog와 reader의 allowlist 밖이며 취득·사용하지 않는다.

출처는 [Zenodo 원 자료](https://zenodo.org/records/18631450)와 DOI 10.5281/zenodo.18631450이다. 기존 reader가 검토한 CC-BY-4.0, 인용, publisher checksum과 크기·파일 경계를 유지한다. 이 API는 원문이나 외부 수치표를 복제하지 않고 새로 작성했다. 원 데이터/개인별 source-root와 결과 raw 자료는 코드 배포물에 포함하지 않는다.

## 실제 재사용 엔진과 단위

`audio_data.read_training_audio`는 catalog에 고정된 archive·publisher hash·허용 member·archive tool을 확인하고 mono 정수 PCM을 읽는다. archive 자체도 일반 파일이어야 하며 symbolic link alias를 거부한다. 파일을 해시한 다음 archive tool이 목록/멤버를 읽기 전후 다시 같은 파일 객체와 경로 identity를 확인한다. 이 검사는 로컬 교체/alias 혼동을 줄이며 공급자 서명이나 적대적 파일시스템에 대한 원자적 snapshot 보증은 아니다. `audio_study`는 이 reader를 실제 호출하며 WAV 파서를 중복하지 않는다. 스펙트럼은 SciPy `signal.get_window` 및 `signal.welch`를 실제 호출한다. FFT·누출 탐지기·신경망·분류기를 별도로 구현하지 않았다.

PCM은 기존 reader의 중심화된 정수 코드를 `2^(8*sample_width_bytes-1)`로 나눈다. 8-bit WAV의 unsigned offset 처리는 기존 reader가 담당한다. 이 값은 디지털 full-scale 상대 진폭이며 Pa, strain, 음압, 센서 전압으로 교정된 값이 아니다. 서로 다른 녹음 장치의 gain, AGC, 필터, 압축 여부와 물리적 거리 차이를 정규화했다고 보지 않는다.

출력은 digital mean/RMS/peak/crest factor, RMS dBFS, endpoint code 점유율과 Welch PSD다. zero RMS의 dBFS/crest factor와 zero PSD의 centroid는 None으로 보존하며 -inf나 임의 0으로 바꾸지 않는다. endpoint code 점유율은 -full_scale 또는 full_scale-1 코드 비율이며 analog clipping 증명이 아니다.

Welch PSD 단위는 full-scale²/Hz다. one-sided density, 실제 source fs, 명시한 periodic Hann 또는 boxcar window, nperseg, noverlap, nfft=nperseg, mean average, 명시 detrend 정책을 사용한다. 라이브러리의 자동 nperseg 축소나 overlap 추정은 허용하지 않는다. 전체 spectral bin power는 `sum(PSD)*df`이며 raw clip RMS²와 일반적으로 같지 않다. window 가중, segment averaging, detrend와 무시된 끝부분이 다르기 때문이다.

band power는 사용자가 지정한 `[lower, upper)` 안의 DFT bin 중심을 선택해 같은 bin 폭을 곱한 값이다. upper가 Nyquist일 때만 그 endpoint bin을 포함한다. 서로 겹치는 band는 거부하고 연속 band 공유 edge를 중복 합산하지 않는다. band 내 bin이 없으면 None/needs_data로 보존하며, 정확한 연속 주파수 경계의 적분이나 보간 결과라고 부르지 않는다.

## 단일 클립 API와 추적성

1. 실제 선택한 source archive basename과 member를 기존 reader로 읽어 source receipt를 만든다. arbitrary filesystem path나 environmental-noise member를 우회 입력하지 않는다.
2. `AudioStudyRequest`의 expected_source_record_digest에 실제 receipt digest를 지정한다. start_frame/frame_count, source frame budget와 timeout, window/nperseg/noverlap/detrend, band를 명시한다.
3. `runtime_metadata()`를 검토 시점에 runtime_pins로 고정한다. PCM reader, Welch/window source, FFT binary, NumPy/SciPy/Python, adapter 소스 해시와 라이선스 식별자를 기록한다.
4. 디지털 정규화와 source/window/resolution의 두 `REVIEWS`에 실제 검토 근거를 연결한다. `boundary_metadata(request)`를 context.metadata['audio_study_boundary']에 넣는다. clock은 source sample index/fs의 파일 상대 시간이며 물리적으로 동기화된 clock을 추정하지 않는다.
5. `evaluate_audio_clip(request, context, source_root=..., tar_executable=...)`를 호출한다. 미검토/누락 입력은 source나 signal owner를 호출하지 않는다. 실제 archive/member/PCM receipt, context origin과 source origin, source count/Nyquist를 확인한 뒤 분석한다.
6. `verify_audio_study_record`는 실제 source-root에서 원 member를 다시 읽고 현재 signal owner로 재계산한다. seal만 검사하는 함수가 아니다. source/runtime pin이나 출력이 바뀌면 거부한다.

진단 결과는 원 PCM 배열을 다시 담지 않고 source receipt와 상대 sample support, source citation/license, spectrum/diagnostics를 기록한다. source 원본이 없으면 실제 replay를 완료할 수 없다. 다른 CPU/runtime에서는 동일 코드라도 마지막 비트 차이가 생길 수 있으므로 현재 exact replay는 고정된 실제 실행 환경의 재현성 계약이다.

source의 디지털 진폭과 배포 metadata 이외의 calibration, 물리 clock, event/device/site group은 결과에서 자동 생성하지 않는다. `declared_condition`은 명시적으로 고른 publisher 폴더 annotation일 뿐이며 해당 실제 archive와 다른 label을 지정하면 거부한다. event/cause/HEP/POD, 독립 실험 수와 학습 성능은 None/False다.

## monitoring 적용 준비

`AudioMonitoringBinding`에 각 study record digest와 분석 clock, 원 파일 frame 0의 물리 clip_start_time_s, 실제 physical_event_id/device_id/site_id/source_group_id, calibration_basis_id 및 검토 근거를 직접 공급한다. 여기서 calibration은 물리 Pa 교정을 만들어 내는 기능이 아니라, 같은 digitizer/증폭 설정의 비교 가능성을 검토한 근거일 수 있다. 어떤 basis인지 명시해야 한다. 누락되면 준비 결과는 needs_data이며 split이나 훈련을 생성하지 않는다.

`prepare_audio_monitoring_split`은 role, split_id, reference_kind를 사용자가 명시한 **하나의** 물리 group만 기존 `MonitoringSplit`으로 준비한다. 같은 event/device/site/source/calibration group, 같은 Welch/digitizer/샘플율/clip 길이, 실제 asset/origin, 정렬되고 겹치지 않는 물리 clip support를 요구한다. 파일 창을 다른 독립 사건으로 나누거나 train/calibration/evaluation 역할을 자동 선택하지 않는다. 'no-leak' 폴더만 보고 verified_normal로 지정하지 않는다.

최종 `monitoring_boundary_metadata(records, bindings, role=..., split_id=..., reference_kind=...)`를 실제 context.metadata['audio_monitoring_boundary']에 넣어 정확한 clip 목록·physical group·명시 role을 바인딩한다. Feature frame 시각은 clip의 exclusive 종료시각으로, 해당 clip 전체 요약이 완료된 시각이다. 준비되는 특징은 RMS/peak/endpoint-code fraction 및 명시 band powers이고 단위는 디지털 무차원 1이다. 기존 monitoring owner가 향후 모든 통계·훈련·calibration·검증을 담당한다. 이 API는 detector를 fit하거나 성공률을 반환하지 않는다.

## HPC driver와 작업 예산

`scripts/review_training_audio_source.py`는 한 archive의 한 member만 명시적으로 선택한다. 기본 실행은 archive를 읽거나 signal 분석하지 않고 `prepared_not_executed` manifest를 저장한다. 향후 누리온에서 --execute-numerics를 추가해야 실제 source reader·Welch·actual replay를 수행한다. 예시의 대괄호 값은 현지 경로와 실제 catalog member로 바꾼다.

```text
python scripts/review_training_audio_source.py --source-root [RAW_ROOT] --archive "leak acoustic data.rar" --member "leak acoustic data/[ACTUAL_FILE].wav" --tar [BSDTAR] --frame-count [REVIEWED_COUNT] --nperseg [REVIEWED_WINDOW] --noverlap [REVIEWED_OVERLAP] --window hann --detrend constant --output [PRIVATE_OUTPUT_JSON]
```

누리온의 실제 실행에는 위 명령에 --execute-numerics를 추가한다. 파일 수를 돌면서 자동 학습하지 않는다. 원 archive당 reader 크기/hash allowlist, 최대 2,000,000 PCM frames, 한 분석 window 최대 65,536, 최대 100,000 Welch segments 및 64개 band로 작업량을 제한한다. 실제 frame count와 nperseg/overlap 조합에서 segment 수와 무시된 tail frames를 기록한다. FFT source pin을 유지하고 source 파일/header 진단과 수치 분석 로그를 구분한다.

## 검증 계획과 남은 작업

작성한 미실행 시험은 미검토 source/native 차단, Nyquist alternating PCM의 해석적 RMS/PSD/band power, zero-signal unknown ratio, source/context pin, endpoint 코드 점유율의 의미, unknown group의 split 보류, 실제 MonitoringSplit 준비, 재봉인한 feature 변조의 actual replay 거부다. fixture는 새로 작성한 합성 PCM이며 현장 WAV가 아니다. 아직 실제 archive selected member로 signal 함수를 실행하지 않았다.

누리온에서 실제 source member를 명시 선택하여 read/replay, Welch parameter/segment 변화, 8/16/24/32-bit PCM, rate·Nyquist·tail·band boundary, 장치별 gain/교정·group 근거를 확인한다. publisher 886 clips를 독립 886 실험으로 나눠 검증하지 않는다. 독립 physical event/site/device mapping과 calibration 근거가 확보되기 전 field 성능이나 POD를 산출하지 않는다. third-party environmental-noise는 원 저작권/재사용 조건이 별도로 해결되기 전 계속 미취득 상태다.

공식 함수 계약: [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html). 공개 설명·주석은 독립 작성했고 원 데이터·의존성의 고지 조건을 유지한다.

origin 계약: catalog의 `measured_process`는 공유 RunContext/SignalFrame 어휘의 `measured`로 명시적으로 대응시킨다. source receipt에는 catalog의 원래 origin을 유지한다. 다른 origin을 임의로 measured로 바꾸지 않는다.
