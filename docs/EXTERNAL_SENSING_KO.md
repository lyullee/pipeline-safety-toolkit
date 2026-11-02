# 외부 누설 관측 어댑터

현재 상태는 **partial / 수치·원 엔진 검증 대기**다. 어댑터·예제·HPC 시험을 작성했다.
로컬 검사는 Ruff·컴파일로 제한하며 pytest, native 모니터링, 수치 예제는 실행하지 않았다.
여기서 `computed`는 입력 관측을 계약에 맞게 변환했다는 뜻이다. 센서 검출기나 현장 성능이
검증됐다는 뜻이 아니다. broad external sensing 항목을 완료로 계산하지 않는다.

`pipeline_integrity.external_sensing`은 이미 처리된 외부 계측값과 품질 상태를 받는다.
광학 위상으로부터 DAS strain을 추정하거나 원격 영상에서 가스 plume를 추출하지 않는다.
원 알고리즘·설정의 SHA와 원자료 SHA를 요구하고 의미를 검토한 출력만 받는다.

## 관측별 물리적 구분

| modality | footprint | 허용된 수량과 SI 단위 | 보존하는 의미 |
|---|---|---|---|
| DAS | fiber_gauge | strain `1`, strain_rate `1/s` | fiber ID와 along-fiber 시작/끝 m, 공간 path, 평균 게이지 basis |
| gas_detector | point | mole_fraction `1`, mass_concentration `kg/m^3` | 샘플링 지점의 종·혼합물/습윤 기준 |
| gas_detector | line_of_sight | path_molar_column `mol/m^2`, path_mass_column `kg/m^2` | 경로 적분 column과 실제 관측선 |
| remote_column | pixel_polygon | molar_column `mol/m^2`, mass_column `kg/m^2` | 종별 pixel column과 등록된 영역 |

모든 `MeasurementBasis`는 정의와 evidence를 포함한다. ppm 등 scaled unit은 호출자가 명시적으로
SI로 변환하고 변환 근거를 남겨야 한다. 경로 column을 임의 길이로 나눠 점 농도로 바꾸지 않는다.
strain을 압력으로 변환하지 않으며 원격 column을 누설 질량유량으로 바꾸지 않는다. dry/wet·기준
물성·배경 제거·게이지 평균 등의 의미는 basis의 definition/reference state와 원 처리 구성에 남긴다.

`SensorFootprint`는 명시된 Cartesian frame의 m 좌표를 가진다. 지리 위·경도를 m 좌표처럼 넣으면
안 된다. point/line/polygon의 형태와 중복 vertex를 확인하되 좌표 변환·polygon 교차·영상 투영·
배관과의 자동 매칭은 계산하지 않는다. 검토한 등록 근거와 spatial error bound가 필요하다.
polygon은 마지막 첫 점을 반복하지 않으며 closure는 implicit이다. curved fiber의 along-fiber
거리를 직선 끝점 거리로 추정하지 않는다. asset_port_ids는 검토한 매핑만 담는다.

## 시계와 품질

`ExternalAcquisition`의 time/exposure는 원 시계의 seconds다. frame 시각은
`exposure_end_s + source_to_analysis_offset_s`이고 보고 시각은 별도 sidecar에 유지한다. source_clock_id, mapping evidence, clock error bound를
함께 보존한다. 현재는 상수 offset만 지원하며 drift·불명확한 시간대를 자동 추정하지 않는다.
보고 시간은 exposure 안에 있어야 한다. 순서를 자동 정렬하지 않으며, 중복·역행·음수 변환·
부동소수점으로 붕괴한 서로 다른 보고 시각이나 nonzero exposure는 거부한다.

| state | 입력 값 | SignalFrame 변환 | sidecar |
|---|---|---|---|
| quantified | 유한 정량값 | 원 값, valid=True | 출처 row·품질 |
| non_detect | None | None, valid=False | LOD와 one-sided 또는 absolute-magnitude censoring support |
| saturated_lower/upper | None 또는 제한과 일치하는 raw clipping 값 | None, valid=False | 포화 방향과 한계, reported raw 값 |
| missing | None | None, valid=False | 원 누락 상태 |
| invalid | None 또는 유한 raw 값 | None, valid=False | 원 무효 상태와 reported raw 값 |

비검출을 zero로, 포화값을 정확한 농도로, 누락을 정상으로 채우지 않는다. censoring support는
instrument rule의 범위이며 confidence interval이 아니다. upper_one_sided 비검출은 lower=None으로
남겨 음수 retrieval noise를 임의 금지하지 않는다. absolute_magnitude는 양·음 strain에 적합하다.
검출한계 미상은 None으로 보존하고 비검출 상태 자체에는 알려진 limit를 요구한다. saturation
한계가 미상이면 해당 포화 상태를 선언할 수 없다. 원 raw 파일은 별도 source SHA로 보존한다.
모든 acquisition은 모든 채널을 선언 순서대로 명시해야 한다. 미기록 채널은 explicit missing으로
표현하고 원 row identity를 부여해야 하며 어댑터가 새 관측을 생성하지 않는다.

`maximum_readings`와 최대 channel/row/vertex 수로 frame/sidecar 생성 전에 작업량을 제한한다.
input과 model_copy 객체를 다시 검증하고 NaN/Inf는 거부한다. unknown 등록 오차·시계 오차 또는
네 가지 applicability review/evidence 중 하나가 빠지면 frames를 내보내지 않고 needs_data다.

## API와 기존 모듈 연결

`adapt_external_sensing(request, context)`가 원 `SignalFrame`과 `quality_sidecar`를 같은 receipt에
담는다. `RunContext.metadata['external_sensing_boundary'] = boundary_metadata(request)`로 exact
요청·asset·clock·origin을 묶는다. 모든 basis/quality/registration/review 근거는 context에 있어야 한다.
source origin을 measured로 승격하지 않는다. `examples/external_sensing.py`의 `make_case()`는
직접 작성한 synthetic methane 상태 예제이며 기본 reviews=False로 계산을 보류한다.

`to_monitoring_split(record, role=..., split_id=..., physical_run_id=..., source_group_id=...,
reference_kind=...)`는 기존 `MonitoringSplit`을 만든다. 평가용 reference를 정상으로 자동 선언하지
않는다. channel/frame 계약과 원 acquisition ID를 유지하며 통계 특징이나 score를 여기서 만들지 않는다.

`evaluate_external_monitoring(records, monitoring_request, context, split_ids=..., physical_run_ids=...,
source_group_ids=..., reference_kinds=..., client=..., owner_source_root=...)`는 정확히 세 개의
training/calibration/evaluation receipt를 받는다. 채널의 footprint·종·물리량·instrument configuration이
세 split에서 같아야 한다. 분석 context의 `metadata['external_sensing_records']`는 순서대로 세 receipt
SHA를 포함해야 한다. 그 다음 기존 `evaluate_monitoring`에 위임하며 원 PMT의 source/version/native
spec pin, split 분리, training-only fitting, calibration, causal window와 품질 기권을 그대로 사용한다.
원 exchange가 지원하는 `NativeMonitoringClient` 또는 worker client를 그대로 전달한다.

PMT 원 소스의 `SignalBatch`는 valid mask를 보존하며 `WindowFeatures`는 사용 창의 모든 채널이
유효해야 특징을 계산한다. 따라서 비검출·포화가 많은 경우 scoring 기권이 많아질 수 있다.
이 기권을 정상 운영이나 누설 없음으로 세지 않는다. 고밀도 DAS의 모든 gauge를 하나의 광폭
시계열로 넘기면 shared feature/row 예산을 넘을 수 있으므로 검토한 subset 또는 원 owner의
공간 축 지원을 사용한다. 어댑터가 gauge 평균/다운샘플링/차원 축소를 새로 발명하지 않는다.

`to_sampled_trace(record, channel_id, sample_period_s=..., maximum_grid_error_s=0.0)`는 고정 DAS
gauge 하나를 기존 `SampledTrace`로 전달한다. 최소 8개·균일 표본 등 기존 waveform 계약을 유지한다.
명시 grid tolerance를 벗어나면 거부하고 재표본화하지 않는다. source_digest는 전체 receipt를 가리켜
exposure·footprint·clock bounds를 추적할 수 있다. sampling period/허용 오차를 이 함수가 추정하지
않는다. 후속 acoustics에는 별도의 검토된 `RelativeTiming`과 적용성 근거가 필요하며 gauge averaging
영향은 사라지지 않는다. pressure_wave는 Pa 압력 trace를 요구하므로 DAS strain을 넣으면 안 된다.

inspection의 axial/circumferential wall feature와 외부 관측 footprint는 서로 다른 계약이다. 둘을
같은 누설/결함이라고 자동 결합하지 않는다. 실제 좌표 등록·동일 사건·물리적 원인 근거가 필요하다.
결과는 원인·HEP·누설 존재확률이 아니며 `quantity_is_cause_or_HEP=False`,
`leak_detection_probability=None`, `field_qualified=False`를 유지한다.

## 재현성과 검증 인계

`verify_external_sensing_record(record, request=..., context=...)`는 seal 확인 후 원 입력에서 frame과
sidecar를 다시 만들고 결과 전체를 비교한다. rehashed quality/geometry/frame 변경도 현재 소스의
재변환과 다르면 거부한다. 원자료·처리 source/config SHA와 로컬 모듈/공통 소스 SHA,
Python/Pydantic/Pint 버전을 기록한다. 해시는 source bytes와 결합을 보존하며 자료가 실제 계측됐거나
원 처리 알고리즘이 역사적으로 실행됐음을 독립 인증하지 않는다. 통계 native receipt는 기존 PMT
exchange가 보존하며 본 adapter receipt와 함께 저장해야 공간·censoring 근거를 잃지 않는다.

준비한 HPC 시험은 비검출/포화/누락 분리, 근거·시계 보류, 단위와 modality 혼동, 균일 DAS
trace 변환, 원 source row·예산, rehashed 변조, origin/NaN 복사 우회, 미결합 native 호출 차단을
포함한다. 아직 실행하지 않았으며 실제 DAS/remote 원 알고리즘·센서 성능 검증도 대기 중이다.

## 원 owner에 대한 구체 확장 요청

수신: [process-monitoring-toolkit](https://github.com/lyullee/process-monitoring-toolkit).
현재 cached `SignalBatch`/`WindowFeatures`와 공식 repository 설명을 읽고 작성했다.
이 요청은 실제 전송된 issue가 아니며 타 세션에서 구현할 수 있는 인터페이스 요구사항이다.

1. `SignalBatch.metadata`/public contracts에 ordered channel basis, species, footprint ID/geometry hash,
   exposure interval, clock error와 source row identity를 손실 없이 왕복하는 공식 schema를 제공한다.
2. censoring state와 bound를 명시하는 observation contract를 추가한다. 일반 detector에는 현재처럼
   valid=False로 전달하고, censored-data 방법은 명시적으로 지원·검증한 알고리즘에만 허용한다.
   비검출을 0/LOD 절반으로 채우는 default는 두지 않는다.
3. DAS time×gauge 입력의 along-fiber coordinate/gauge length/channel order를 보존하는 bounded chunk
   API와 공간 adjacency 검증을 제공한다. 실제 demodulation/denoising은 담당 owner에 남긴다.
4. remote retrieval의 moving footprint, cloud/quality flag, exposure, species/basis를 가진 불규칙
   시공간 observation adapter와 검토된 aggregation/resampling 계약을 별도 제공한다.
5. causal feature output에 사용 원 row·spatial support·censoring 기권 이유를 기록하고 replay 시 검증한다.
   event probability나 cause를 score로부터 자동 생성하지 않는다.

수락 기준: 원 footprint·row IDs·origin·clock/quantity 의미가 왕복에서 같고, censored/unknown이
기권으로 유지되며, PMT native algorithms와 independent split 검증을 재사용한다. 실제 정상/고장
label이 있는 독립 field dataset이 확보되기 전에는 탐지확률이나 현장 qualification을 주장하지 않는다.

## 교차 정적검토: exposure 요약의 시각과 DAS 응답

SignalFrame의 time_s는 이제 exposure_end_s에 source-to-analysis offset을 더한 명목 완료 시각이다. 원 보고 시각은 quality sidecar의 source_time_s와 analysis_report_time_s에 보존한다. exposure 중간 보고 시각을 causal monitoring 시각으로 쓰면 아직 완료되지 않은 적분값이 미래 정보가 될 수 있던 문제를 수정했다. clock error bound는 계속 원 요청에 유지되며, 지연의 실제 상한이나 원시 신호의 순간 측정시각을 새로 추정하지 않는다. 완료 시각 순서가 증가하지 않는 자료는 자동 정렬하지 않고 거부한다.

nonzero exposure를 가진 DAS 기록을 SampledTrace로 바꿀 때는 ExternalChannel.temporal_response='uniform_exposure_mean'을 근거와 함께 명시해야 한다. 모든 acquisition의 양의 exposure 길이가 정확히 같아야 한다. 이 경우 quantity_id에 평균 응답·길이·end-time 기준을 함께 넣어 순간 strain과 동일한 waveform으로 취급하지 않는다. 응답이 미상인 exposure 값은 raw frame/sidecar/monitoring 경로에서 유지되며, point waveform으로 자동 재해석하지 않는다. zero-duration sample의 기존 quantity_id는 유지한다. 이 수정의 시간·응답 회귀를 작성했지만 실행하지 않았다.
