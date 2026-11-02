# 검사 통계와 두께 격자 교환

`pipeline_integrity.inspection_statistics`는 Mat 소유의
`inspection.binomial_evidence`, `inspection.hit_miss_pod`,
`inspection.tofd_depth`, `geometry.thickness_grid` 네 모델을 실제 API로 호출한다.
통계 추정, 신뢰한계, TOFD 깊이식과 격자 군집 알고리즘은 이쪽에서 구현하지 않는다.
이 모듈은 실제 관측 정답, 시편 독립성, 절차·조건, 단위, 좌표·시계와 계산 영수증의
경계를 엄격히 연결한다. 계산 성공과 검사 합격·절차 자격·현장 성능은 별도다.

## 공개 Python API

```python
import runpy

from pipeline_integrity.inspection_statistics import (
    InspectionStatisticsRequest,
    InspectionTrial,
    TOFDReference,
    ThicknessGridLayout,
    evaluate_inspection_statistics,
    check_inspection_statistics_record,
)

cases, context = runpy.run_path("examples/inspection_statistics.py")["make_cases"]()
request = cases["inspection.tofd_depth"]
record = evaluate_inspection_statistics(
    request, context, client=None, pinned_job=None, source_root=None, expected_source_sha256=None
)
check_inspection_statistics_record(record)
```

`request`는 `InspectionStatisticsRequest`, `context`는 기존 `RunContext`다.
이미 생성한 객체도 평가 전에 Python 모드로 엄격하게 재검증한다. 중첩 dict/list는
수정할 수 있으므로 `frozen` 선언만 신뢰하지 않는다. 직렬화 전에 원 Python 값에
`json_value`를 적용해 NaN/Infinity가 JSON null로 바뀌는 일을 막는다. bool은 정수
예산이나 이항 횟수로 받아들이지 않는다. native 호출은 공통 `MaterialJob`과
`MaterialReceipt`의 strict JSON 경로를 사용한다.

`client`를 생략하면 선택적 원 Mat 패키지를 사용한다. 검토나 필수 정보가 부족한
요청은 그 패키지를 import하거나 live schema를 조회하기 전에 `needs_data`로
반환한다. `pinned_job`은 `prepare_material_job`으로 미리 검토한 작업이다.
모델·버전·입력 schema digest·단위·확률 의미, asset/location/clock, 파라미터와
근거가 다르면 계산을 거부한다. 현재 schema에 없는 파라미터도 거부하며 native
기본값으로 미상 입력을 채우지 않는다.

`source_root`와 `expected_source_sha256`는 함께 지정한다. 공통
`material_source_guard`가 선택 root의 `src` 아래 Python 파일을 상대 이름과
원 바이트 순으로 해시하고, 실제 import된 Mat 패키지와 evaluator가 그 `src`
안에 있는지 확인한다. 별도 `client.registry_factory`가 있으면 strict JSON을 실제
실행하는 registry의 출처도 같은 범위인지 확인한다. 계산이 예외를 내도 종료 시
소스 불변을 확인한다.
`source_pin`에는 before/after treeSHA와 파일 수·불변 여부만 남으며 개인 경로는
넣지 않는다. pin을 생략한 계산은 소스가 고정됐다고 주장하지 않는다. guard에서
반환하는 dict는 `with` 종료 때 after가 완성되므로 직렬화는 블록 뒤에서 한다.
동시에 바뀌는 개발 checkout 대신 별도 검토한 고정 소스를 지정할 수 있다.

## 공통 요청 필드와 근거

| 필드 | 의미와 제약 |
|---|---|
| `method` | 네 native 모델 ID 중 하나 |
| `asset_id`, `location_id`, `clock_id` | 물리 자산·위치·원 관측 시계. asset/clock은 context와 같아야 함 |
| `specimen_set_id`, `material_condition_id` | 실제 시편군/검사 단면과 재료 상태 식별자 |
| `procedure_id`, `procedure_revision`, `test_conditions_id` | 절차 개정과 비교 가능한 시험 조건 식별자 |
| `parameters` | 아래 표의 native 입력 전체. 미상이면 null로 유지 |
| `input_units` | 모듈의 `PARAMETER_UNITS[method]`와 정확히 같은 매핑 |
| `applicability_reviews` | 아래 모델별 key의 명시적 bool 검토. 생략은 false |
| `review_evidence_ids` | 각 true review를 뒷받침하는 근거 ID |
| `evidence_ids` | 비어 있지 않은 고유 ID 목록. 모두 context의 근거에 존재해야 함 |
| `maximum_observations` | 기본 100000, 범위 1~1000000. trial 및 POD 벡터 초과 시 오류 |
| `maximum_grid_cells` | 기본 100000, 범위 1~1000000. cell 합계 초과 시 오류 |

근거는 단순히 문자열 검토를 통과시키기 위한 자리가 아니다. 절차·교정·시편 정답·
실험 설계의 출처를 `EvidenceRef`의 citation, origin, hash, license와 limitations로
연결한다. 서로 다른 검사자의 반복 판정, 같은 시편의 여러 영상과 같은 결함의
재측정은 자동으로 독립 시편이 되지 않는다. 시계 이름은 수치 시간축이나 설비
나이를 만들지 않는다. 관측을 정렬·절단하거나 결측을 제거해 예산에 맞추지 않는다.

## 모델별 native 입력

### 이항 근거

| 파라미터 | 단위/제약 |
|---|---|
| `opportunities` | `count`, 양의 정수 |
| `positive_responses` | `count`, 0 이상이며 opportunities 이하 |
| `confidence_level` | `1`, 0.5 초과 1 미만 |
| `response_kind` | `category`, `detection_at_declared_size` 또는 `false_call_on_blank` |
| `independent_trials_declared` | `bool`, 명시한 독립·동질 기회 설계 |

`trials`에 `InspectionTrial`을 원 순서로 제공한다. 각 행은
`observation_id`, `specimen_id`, `clock_id`, `truth_flawed`, `detected`,
`flaw_size_m`을 가진다. `cluster_id`와 `acquired_time_s`는 선택 정보다.
실제 양성 횟수와 행 수가 native 횟수와 정확히 맞아야 한다. unknown truth/detected는
false나 미검출이 아니므로 보류한다. 같은 시편 또는 공유 cluster가 있으면 별도
의존 모델이 필요하다. observation ID 중복과 clock 불일치는 오류다.

검출 근거는 `declared_detection_size_m`가 필요하며 모든 시편의 truth가 true이고
그 크기와 일치해야 한다. blank 근거는 truth=false, size=null이어야 하며 단일 결함
크기를 선언하지 않는다. 0 false call은 허용하지만 미상 횟수나 검출 정답이 없는
행을 false call 0으로 채우지 않는다.

출력 `probability_estimate`는 명시한 응답의 기회당 비율이다.
`one_sided_lower_probability`와 `one_sided_upper_probability`는 각각 독립적인
한쪽 이항 한계이며 함께 하나의 동일 신뢰수준 양쪽 구간을 뜻하지 않는다.
검출 결과는 선언한 크기·조건에서만 해석한다. blank false-call 비율은 POD가 아니다.
원 모델의 빈 `probability_semantics` 매핑도 바꾸지 않고 `response_kind`를 보존한다.

필수 review key는 `independent_comparable_trials`,
`binary_truth_and_response_definition`, `declared_size_or_verified_blank`,
`procedure_and_test_conditions`다.

### hit/miss POD

| 파라미터 | 단위/제약 |
|---|---|
| `specimen_ids` | `identifier`, 원 순서 고유 시편 ID, 최소 6행 |
| `flaw_size_m` | `m`, 양의 실제 결함 크기. 시편 벡터와 정렬 |
| `detected` | `bool`, 실제 검출 여부. 숫자 0/1을 대신 받지 않음 |
| `query_sizes_m` | `m`, 최소 1·최대 100개. 관측 크기 지지구간 내부 |
| `target_probability` | `1`, 0 초과 1 미만 |
| `confidence_level` | `1`, 0.5 초과 1 미만 |
| `maximum_threshold_size_m` | `m`, 양의 상한 탐색 영역 |
| `independent_trials_declared` | `bool` |

모든 `InspectionTrial`의 truth=true와 알려진 flaw size를 요구하고 세 native
관측 벡터를 witness 행과 정확히 대조한다. query 순서는 임의로 정렬하지 않는다.
miss는 검출=false인 알려진 결함 시편이다. blank는 이 증가형 POD 적합의 결함
행으로 넣지 않는다. 실제 자료에 정답·독립 시편·검출 여부가 없다면 추정을 보류한다.

출력 `probability_of_detection`은 결함 크기와 검사 절차가 주어진 검출확률이다.
원 선언 `detection_given_flaw_and_procedure`를 그대로 유지한다. 점별 lower confidence,
target size 추정과 upper confidence size·외삽 flag·탐색 범위 안에서 유계인지 여부를
보존한다. confidence는 점별·점근적이다. native가 분리, 양/음 결과 부족 또는
안정적인 증가 기울기 부족으로 반환한 `needs_data`와 notes를 바꾸지 않는다.
유계가 아닌 threshold를 탐색 상한이나 0으로 대체하지 않는다.

필수 review key는 `independent_physical_specimens`,
`binary_truth_and_known_flaw_sizes`, `procedure_and_test_conditions`,
`increasing_log_size_model_and_query_support`다.

### TOFD 깊이

| 파라미터 | 단위/제약 |
|---|---|
| `probe_index_separation_m` | `m`, 양의 측정 probe index 간격 |
| `longitudinal_sound_speed_m_s` | `m/s`, 양의 시편 종파속도 |
| `delay_from_lateral_wave_s` | `s`, lateral 기준 대비 0 이상 지연 |
| `wall_thickness_m` | `m`, 양의 알려진 wall thickness |

`tofd_reference`는 원 `clock_id`, lateral/diffraction frame ID와 같은 frame의
SHA-256, 구분된 transmit/receive probe ID, 양쪽 wedge ID,
`separation_basis_id`, `velocity_basis_id`, `wall_thickness_basis_id`,
`lateral_arrival_s`, `diffraction_arrival_s`를 보존한다.
`backwall_arrival_s`도 있으면 그대로 보존한다. 두 pick은 같은 불변 frame이어야
하고 원 pick 차이가 native delay와 맞아야 한다. 미상·비인과 pick·다른 frame은
계산 보류다. 파형에서 pick을 만들어 내거나 common delay 외의 교정을 추정하지 않는다.

출력 `tip_depth_m`, `remaining_backwall_ligament_m`는 대칭 직선 경로·probe 중간
아래 tip이라는 검토에 한정된다. wall을 넘으면 native `out_of_scope`와 음의
ligament를 보존한다. zero delay는 알려진 0이며 미상 arrival과 다르다. dead zone,
flaw identity, POD, acceptance나 수명은 이 깊이 변환으로 확정하지 않는다.

필수 review key는 `symmetric_straight_path_and_midpoint_tip`,
`same_frame_lateral_reference_and_calibrated_delays`,
`probe_index_separation_velocity_and_wall_basis`, `procedure_and_test_conditions`다.

### 두께 격자

| 파라미터 | 단위/제약 |
|---|---|
| `cell_thickness_m` | `m`, 원 row-major 직사각 격자. 양의 관측 또는 null |
| `cell_size_x_m`, `cell_size_y_m` | `m`, 양의 동일 면적 cell 치수 |
| `reference_thickness_m` | `m`, 양의 비교 기준 두께 |
| `reporting_threshold_m` | `m`, 양의 reporting threshold |

`grid_layout`은 `coordinate_frame_id`, 원 `row_ids`/`column_ids`,
`row_axis='y'`, `column_axis='x'`, `positive_x_direction`,
`positive_y_direction`, 첫 cell 중심의 `first_cell_center_x_m`와
`first_cell_center_y_m`를 가진다. ID는 각 축에서 고유해야 하며 실제 배열 치수와
일치해야 한다. 첫 중심은 SI m이고 이후 배치는 선언한 동일 cell 크기와 양의 축을
따른다. 실제 좌표가 비등간격이거나 불규칙하면 이 모델을 적용하지 않는다.

null cell은 native가 지원하는 미상 관측으로 그대로 전달한다. 전부 null이면
native 호출 전에 보류한다. `minimum_measured_thickness_m`,
`mean_measured_thickness_m`, observed/missing area와 observed fraction은 측정한
범위만 설명한다. `net_measured_loss_volume_m3`는 기준보다 두꺼운 cell의 음의
기여도 포함하는 signed 순량이다. 이를 gross corrosion volume으로 바꾸지 않는다.
threshold와 같은 cell은 below에 포함하지 않으며 군집은 원 모델의 4-neighbour
연결이다. 미상 cell을 통과해 군집을 연결하거나 sound cell로 채우지 않는다.
이 군집 면적은 검사 coverage·포착률·내압 capacity·FFS 합격 판정이 아니다.

필수 review key는 `equal_area_cell_placement_and_original_order`,
`measured_thickness_and_missing_cells`, `reference_and_reporting_threshold_basis`,
`procedure_and_test_conditions`다.

## 영수증과 의미의 보존

반환 dict는 `status`, `values`, `output_units`, `probability_semantics`, `issues`,
원 요청/context, `native_receipt`, 선택 `source_pin`, 구현 SHA와 `record_digest`를
포함한다. native가 계산했다면 원 payload 전체와 파라미터·context·모델·schema
영수증을 보존한다. 원 결과에 요구 판정 등 추가 필드가 있으면 payload에 남으며
calculation status와 합격으로 합치지 않는다. 보류한 경우 receipt=null과 빈 values를
반환한다. 잘못된 단위·식별자·입력 불일치·예산 초과·tamper는 예외다.

`check_inspection_statistics_record`는 top-level digest, 중첩 MaterialReceipt,
native 값과 공개 projection의 일치를 재확인한다. 해시는 전자서명이나 신뢰기관
승인이 아니며, 공격자가 모든 기록을 새로 작성할 권한까지 막는 기능은 아니다.
`period_failure_probability`와 `human_error_probability`는 항상 null이고,
`inspection_or_material_acceptance`와 `independent_field_validation`은 false다.
POD, pointwise 크기초과, 측정 영역 비율을 기간 첫고장확률·HEP·포착률로 바꾸지 않는다.

## 재현과 검증 범위

기존 환경에서 다음을 프로젝트 루트 기준으로 실행한다. `MAT_SOURCE`는 사용자가
별도로 관리하는 검토한 원 Mat checkout을 뜻한다.

```powershell
python -m pytest tests/test_material_source.py tests/test_inspection_statistics.py -q
python -m ruff check src/pipeline_integrity/material_source.py src/pipeline_integrity/inspection_statistics.py tests/test_material_source.py tests/test_inspection_statistics.py examples/inspection_statistics.py scripts/check_inspection_statistics.py
python -m ruff format --check src/pipeline_integrity/material_source.py src/pipeline_integrity/inspection_statistics.py tests/test_material_source.py tests/test_inspection_statistics.py examples/inspection_statistics.py scripts/check_inspection_statistics.py
python scripts/check_inspection_statistics.py --materials MAT_SOURCE
```

동시 개발 중인 원 checkout 대신 검토한 비공개 snapshot을 사용했다면 마지막 명령에
`--source-kind private_pinned_snapshot`을 추가한다. 이번 공개 실행 증거는 이 방식의
76개 Python 파일 source pin을 기록한다. LICENSE·외부 notice와 세 catalog JSON도
별도로 전후 해시를 비교했다. snapshot 위치와 복제한 코드는 공개 배포에 넣지 않는다.

예제 단독 실행 시 원 Mat를 설치하거나 `MAT_SOURCE/src`를 Python 경로에 지정한 뒤
`python examples/inspection_statistics.py`를 실행한다. `make_cases()`의 네 요청을
다른 연구 코드에서 가져와 쓸 수 있다. 예제는 자체 작성 가상 값이다. 실제 자료에
예제의 독립성·정답·크기·교정을 옮겨 붙이지 않는다.

검증 스크립트는 네 native 모델, 전체 검출과 blank 0응답, 30개 가상 독립 시편의
세 크기군, 분리·외삽/비유계 threshold, TOFD zero/out-of-wall, 미상 cell과 음의
순손실, 미검토 의존성·정답 부족과 pinned job을 실행한다. 24개 독립 수치 기준은
이항 끝점의 단일 꼬리 관계, 원 적합기를 재사용하지 않은 알려진 세 군의 비율·
중간점·공분산, 별도 고정밀 삼각형 길이와 직접 정한 격자 산술값이다.
profile confidence의 점별 숫자 자체에 대한 독립 구현 검증을 주장하지 않는다.
원 source revision/treeSHA는 `check_integrations.revision`으로 실행 전후 기록한다.

상세 request·관측행·영수증은 `artifacts/inspection_statistics/native_records.json`에만
저장한다. 공개 `research/inspection_statistics_results.json`은 모델 버전/schema digest,
24기준·사례 상태·관측 집계·상세 기록 해시·권리/제약·소스 불변만 포함한다.
자체 작성 예제의 권리는 MIT이며 외부 엔진의 소유권과 인용은 별도다.
원 Mat 코드와 원관측행은 공개 요약에 재배포하지 않는다.

TUBERS 파형·영상과 ILI 반복 관측은 기존 자료 검토 경로를 유지한다. 이들 자료의
현재 독립 hit/miss·검사자 판정·결함 크기 정답이 부족하므로 여기서 가짜 POD를
보정하거나 실험 검증 완료로 표시하지 않는다. 알려진 합성 비율 재현은 현장 검출
성능이나 관벽 geometry·probe·절차 전이의 독립 검증이 아니다.

## 다른 세션과 원 프로젝트에 요청할 확장 명세

1. **검사 설계와 실제 POD 검증**: 원 Mat에 별도 trial-series 계약을 요청한다.
   입력은 불변 source/행 hash, physical specimen/결함/검사자/절차 개정/조건/blank
   ID, 알려진 크기와 불확실성, binary truth와 독립 검토 detection, source-group과
   calibration/holdout 분할, cluster·원 순서·원 시계다. 미상·미검출·false-call을
   구분한 결과와 지원 범위·실패 사유를 반환해야 한다. 같은 시편/원자료의 여러
   영상은 독립 행으로 늘리지 않으며 공개 자료의 truth 부족을 명시한 보류 예제를
   완료 조건에 포함한다. 기존 독립 모델과 별도의 clustered 반복 검사 모델이 필요하다.
2. **TOFD 실물 획득 연결**: 원 획득 세션의 procedure/revision·probe/wedge·측정 index
   separation·시편 velocity/wall basis·encoder 방향·frame hash를 전달한다.
   동일 frame의 lateral/backwall/diffraction pick과 그 근거를 명시적으로 바인딩하고
   noncausal/unknown/다른 frame·뒤집힌 tip 순서를 보류해야 한다. 원 파형 처리,
   비대칭·굴절 경로와 dead-zone qualification은 각각 별도 모델과 독립 실험이
   필요하다. 이쪽은 좌표와 원 status/units를 검증할 adapter만 확장한다.
3. **비정규 두께 지도와 불확실성**: 원 위치/면적/원순서/좌표계·유효성 mask·측정
   불확실성·원 행 hash를 가진 비등간격 map 모델을 요청한다. unknown area를
   보존하고 공간 연결 정의를 모델 metadata에 선언해야 한다. 합성 irregular
   geometry·missing barrier·threshold equality·signed/gross loss를 구분한 기준과
   독립 두께 지도 holdout을 마련한 뒤 새 모델 ID/schema/version을 연결한다.
4. **검사 계획·위험 연결**: 감지 의미와 실제 결함 분포·위치/크기 지지구간·검사
   조건·기간 고장 모델·생존 조건을 구분한 명시적 변환 계약을 요청한다. 단순 POD
   또는 observed fraction에서 HEP·기간 고장·risk reduction을 생성하면 안 된다.
   교환 계약 검사와 독립 현장 성능 증거를 별도 산출물로 제출한다.

확장 작업은 해당 소유 프로젝트 세션에서 수행하고 여기에서는 새 live schema,
MaterialJob, source pin, native receipt와 independent reference를 재검증한다.
