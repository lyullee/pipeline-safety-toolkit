# 재료 조성·충격 천이의 원 모델 연결

`material_quality.py`는 다음 세 Mat 모델을 실제 호출한다. 구간 정합, 조성 지수,
천이곡선 적합 알고리즘은 원 소유 프로젝트에 있다. 이쪽은 단위·근거·물리 범위와
원 결과의 동일성을 확인하고, 미상 조건을 보류한다.

| 원 모델 | 계산 의미 | 입력 기준 |
|---|---|---|
| `materials.chemistry_reconciliation` | 명시된 증명서·검증 분석·요구 구간의 제한된 비교 | 질량 분율, 완전한 명칭과 물리 범위 |
| `materials.pitting_composition_index` | 사용자가 정한 조성 계수의 스칼라 지수 | Cr·Mo·N 질량 백분율, 같은 assay |
| `materials.impact_transition` | 한 시리즈의 증가형 기술적 에너지 천이 적합 | K·J, 동일 재료·시편 형상·방향·조건 |

원 `0.1.0.dev0` 소스의 고정 스냅샷은 76 Python 파일이며, 트리 SHA-256은
`0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다.
실행한 세 모델 버전은 각각 `1.0.0`이다. MIT 원 라이선스·고지·JSON 자원과
Python 트리의 실행 전후 해시가 같다. 원 코드는 수정하지 않았다.

## API와 공통 계약

`evaluate_material_quality(request, context, *, client=None, pinned_job=None,
owner_source_root=None)`는 `MaterialQualityRequest`와 `RunContext`를 받는다.
`verify_material_quality_record(record)`는 봉인·원 영수증·입력·단위·결과·소스
핀과 의미 플래그를 다시 확인한다. 결과는 엄격한 JSON 값이다.

| 요청 필드 | 의미·조건 |
|---|---|
| `method` | 위 세 원 모델 ID 중 하나 |
| `asset_id`, `clock_id` | context와 정확히 같은 설비/자료 범위와 시계 |
| `location_id` | 원 영수증에 전달하는 위치/검사 범위 |
| `material_condition_id` | 선언한 재료 상태. impact 내부의 같은 필드와 정확히 대응 |
| `population_id` | 전달하려는 물리 모집단 ID. 관측 수와 모집단 수는 구분 |
| `physical_scope_id` | chemistry의 certificate와 verification 내부 범위와 정확히 대응 |
| `series_or_assay_id` | impact 내부 series ID 또는 pitting의 공통 분석 ID |
| `composition_basis` | chemistry는 `mass_fraction`, pitting은 `mass_percent`, impact는 `not_applicable` |
| `parameters` | 아래 원 입력 객체 또는 `None`. `None`이면 원 엔진을 불러오기 전에 보류 |
| `input_units` | `INPUT_UNITS[method]`와 정확히 같은 사전. 자동 배율 추정 없음 |
| `applicability_reviews` | `REVIEWS[method]`의 검토 키에 대한 실제 bool |
| `review_evidence_ids` | 각 검토 키의 근거 ID. 선택한 evidence에 포함되어야 함 |
| `evidence_ids` | context에 실제 존재하는 중복 없는 근거 ID tuple |
| `element_assay_ids` | pitting의 `Cr`, `Mo`, `N` 세 키 모두 동일한 `series_or_assay_id`를 가리킴 |
| `impact_observations_complete` | impact에서 검토한 완전·비검열 관측이면 `True`. 미상/검열은 보류 |
| `declared_temperature_support_K` | impact의 선언한 온도 지원 구간. 입력 관측의 이탈은 `out_of_scope` |
| `maximum_observations` | 기본 2,000, 허용 8~10,000. 시리즈를 자르지 않고 초과 요청 거부 |
| `maximum_elements` | 기본 118, 허용 1~118. 각 분석/요구 원소 사전의 최대 길이 |
| `maximum_payload_nodes` | 기본 100,000, 허용 100~500,000. 깊이 20 제한과 함께 직렬화 전에 검사 |
| `owner_source_tree_sha256` | 선택한 원 Python 트리 핀. 사용 시 `owner_source_root`도 제공 |

`RunContext`의 asset·scenario·clock·origin·evidence·metadata는 원 계산의
문맥 해시로 전달한다. `EvidenceRef`의 id·citation·origin·sha256·license_id·locator·
limitations를 보존한다. chemistry 내부 Evidence는 원 스키마의 `source_id`,
`origin`, `citation`, `license_id`, `sha256`를 사용한다. 내부 source ID는 선택한
context evidence에 존재해야 하며 citation·license·해시도 같아야 한다.

검토 키는 다음과 같다. 키를 만들거나 생략하면 검토 완료로 간주하지 않는다.

| 모델 | `REVIEWS` 키 |
|---|---|
| chemistry | `physical_scope_identity_and_assay_intervals`, `requirements_rights_basis_and_comparison_policy` |
| pitting | `same_assay_element_measurements_and_mass_percent`, `coefficient_support_and_index_meaning` |
| impact | `one_material_geometry_orientation_and_uncensored_series`, `temperature_energy_support_and_loss_policy` |

## 원 입력 전체

chemistry의 최상위 입력은 `certificate`, `verification`, `requirements`,
`require_matching_heat_ids`, `require_verification_designation`,
`require_certificate_verification_interval_overlap`다. 뒤의 세 정책은 실제 bool이며
`False`는 의도한 정책으로 보존한다. 0이나 미상값으로 바꾸지 않는다.

certificate와 verification은 각각 `record_id`, `physical_scope_id`, 선택적 `heat_id`,
선택적 `designation`, `method_id`, `element_intervals`, `interval_interpretation`,
`evidence`를 가진다. 명칭은 namespace·specification·edition·grade·product_form·
condition 여섯 필드를 모두 명시한다. 같은 이름에서 등급·규격판·제품·열처리의
동등성을 추정하지 않는다. requirements는 `requirements_id`, `designation`,
`element_limits`, `evidence`를 갖는다. 요구값과 그 이용 권한도 caller 근거다.

원소 키는 원 스키마의 대문자 시작 1~2글자 원소 표기를 사용한다. 구간은
`lower`, `upper`의 0~1 질량 분율이며 순서가 맞아야 한다. 원 스키마는 이 표기의
화학적 진위를 주기율표와 대조하지 않으므로 assay 원소 식별은 검토 대상이다.
분석의 미측정 원소나 `None` 구간은 원 엔진에서 `unknown`을 만든다. 측정 하한에
걸린 값은 검토한 구간으로 전달한다. 상한을 점값으로 만들거나 누락값을 0으로
채우지 않는다. 범위를 넘는 원소 하한 합은 거부한다. 구간 겹침은 caller의
일관성 정책이며 재료의 화학적 균질성을 증명하지 않는다.

pitting은 `chromium_mass_percent`, `molybdenum_mass_percent`, `nitrogen_mass_percent`,
`molybdenum_coefficient`, `nitrogen_coefficient`를 모두 받는다. 각 원소는 0~100의
질량 백분율이고 세 원소의 합도 100 이하여야 한다. 계수는 양수이며 검토한
적용 근거를 caller가 제공한다. 지수는 단위 없는 상대 조성 지표다. 실측 공식률,
임계 공식 온도, 승인 강종, 잔여 수명이나 고장 확률로 변환하지 않는다.

impact는 `series_id`, `specimen_geometry_id`, `material_condition_id`, `orientation_id`,
`observational_unit_description`, `observation_ids`, `temperature_K`, `absorbed_energy_J`,
`target_energy_J`, `minimum_relative_shelf_coverage`, `loss`, `residual_scale_J`를 받는다.
관측 ID는 중복 없이 온도·에너지 벡터와 행별로 대응해야 한다. 원 모델은 8개 이상 관측과
5개 이상 서로 다른 양의 K 온도를 요구한다. 에너지는 0 이상의 J다.
`minimum_relative_shelf_coverage`는 0.5 초과 1 미만, `loss`는 `linear` 또는 `soft_l1`,
`residual_scale_J`는 양수다. 강건 손실의 척도는 사용자가 선언하며 시험기 불확실성,
우도·신뢰구간·가중치를 자동으로 대신하지 않는다. 미파단/상한 검열 자료는 이
완전 관측 적합에 넣지 않는다. 형상·방향·열처리·조사 조건의 다른 자료를 묶지 않는다.

## 결과·교체·실패

chemistry의 `computed`는 비교를 수행했다는 뜻이다. 내부 outcome의 `satisfied`,
`not_satisfied`, `unknown`은 그대로 구분한다. 조건·명칭·heat가 부족하면 비교
자체를 했어도 outcome은 `unknown`일 수 있다. 이는 전체 규격이나 증명서의
진위·대표성·기계적 성능의 승인 판정이 아니다.

impact의 원 결과는 lower/upper shelf 에너지, mid-shelf 온도, logistic width,
관측 온도 구간·상대 shelf coverage, 목표 에너지 crossing, fitted energy·잔차·RMSE,
정보행렬 조건수와 수치 경계 도달 여부를 갖는다. shelf/수치 식별 조건이 부족하면
원 `needs_data`와 그 진단값을 보존한다. 목표 crossing이 관측 온도 밖이거나
shelf 사이에 없으면 `None`이다. 이 값은 파괴인성·천이 원인·허용 사용 온도가 아니다.

`client`는 기존 `MaterialClient` 계약을 만족하는 원 평가자·context/evidence
factory·registry factory로 교체할 수 있다. `pinned_job`은 `prepare_material_job`이
만든 `MaterialJob`이며 원 버전·입력 schema digest·단위·확률 의미를 고정한다.
다른 입력이나 위치·시계·근거의 job을 전용하지 않는다. 소스 핀은 선택한 checkout의
실제 로드된 package/evaluator/registry 소유 경로와 실행 전후 트리를 검사한다.
별도 checkout을 쓰면 Python 경로도 그 위치로 명시하고 검증을 재실행해야 한다.

미검토·미상 입력은 원 import 전 `needs_data`와 빈 결과로 보류한다. 단위/기준·
문맥·증거·physical scope 불일치, NaN/Inf, 숫자 대신 bool, 예산 초과·원 schema
변경·손상된 원 영수증·잘못된 소스 핀은 예외다. 선언 온도 지원 밖의 관측은
`out_of_scope`다. 원 엔진에서 나온 보류는 원 진단·값·영수증과 함께 보존한다.

## 실행 예

아래 코드는 원 재료 모듈이 없는 환경에서도 미상 입력의 보류 경계를 실행한다.

```python
import runpy
from pipeline_integrity.material_quality import (
    PITTING,
    evaluate_material_quality,
    verify_material_quality_record,
)

requests, context = runpy.run_path("examples/material_quality.py")["make_cases"]()
request = requests[PITTING].model_copy(update={"parameters": None})
result = evaluate_material_quality(request, context)
verify_material_quality_record(result)
assert result["status"] == "needs_data"
assert result["native_receipt"] is None
```

실제 원 엔진 실행과 독립 작성 기준의 재현 명령은 다음과 같다.

```powershell
python scripts/check_material_quality.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/review_material_process_data.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python -m pytest tests/test_material_quality.py tests/test_material_process_data.py -q
python -m ruff check src/pipeline_integrity/material_quality.py src/pipeline_integrity/material_process_data.py examples/material_quality.py scripts/check_material_quality.py scripts/review_material_process_data.py tests/test_material_quality.py tests/test_material_process_data.py
```

실제 원 모델 13응답, 독립 작성 기준 18개, 잘못된 요청 9개 거부를 확인했다.
정상 구간·검열/미상 분석·다른 heat·0지수·False 정책, 알고 있는 합성 천이의
shelf·중앙 온도·폭·목표 crossing과 강건 손실, shelf 부족과 외삽 보류를 확인했다.
이는 소프트웨어와 인터페이스의 수치 기준이며 현장 재료 검증이 아니다.
자세한 원 입력·영수증·기준값은 `artifacts/material_quality`에만 남긴다.
공개 집계는 `research/material_quality_results.json`이며 본인 코드 해시·원 버전·
schema digest·소스/라이선스/고지/자원 불변 증거를 포함한다.

[실험 자료 가이드](MATERIAL_PROCESS_DATA_KO.md)의 BAM 6시편은 한 온도·미파단이며
조성 선언은 다른 비교 lot일 수 있다. 원조건의 세 모델 요청은 모두 보류하고
합성 기준과 구분한다. 방법별 잔차 통계·모수 불확실성·검열 likelihood·미세조직과
실사용 환경 전이·시편 크기 보정·전체 규격 절차는 후속 PI-X04 근거가 필요하다.
