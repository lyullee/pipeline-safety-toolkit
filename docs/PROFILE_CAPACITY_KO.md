# 축 방향 감육 형상과 선언된 압력차 용량

`pipeline_integrity.profile_capacity`는 기존 Mat의 `structural.profile_capacity`를 호출한다. 원 엔진이 축 방향 두께 점 사이의 손실 면적과 사용자 공급 팽창계수를 사용해 모든 절점 끝점 창을 비교한다. 이 저장소는 면적 스캔 알고리즘, 규격 계수, 재료 유동응력 또는 허용압력 표를 복제하지 않는다.

기준 압력과 계산 용량은 모두 **내압에서 외압을 뺀 압력차**다. 공정 절대압력과 직접 비교하지 않는다. 결과는 선언한 모델의 정적 감육 계산이며 MAWP, 설계 승인, 누설/파열 분류, 잔존수명 또는 고장확률이 아니다.

## API와 필드

`ProfileCapacityRequest`는 asset_id/location_id/clock_id, geometry_id/material_condition_id, profile_id, coordinate_basis_id, source_observation_id, source_point_ids, parameters, input_units, evidence_ids를 받는다. 원 점 ID는 축 방향 좌표와 일대일 대응한다. 결측 점을 삭제하거나 두께를 기준 벽으로 잘라 맞추지 않는다. `applicability_reviews`와 `review_evidence_ids`의 키는 `REVIEWS`에 정의되어 있으며 기본은 미검토다.

`evaluate_profile_capacity(request, context, *, client=None, pinned_job=None, owner_source_root=None)`가 원 작업·스키마·단위·조건·증거를 담은 receipt와 결과를 반환한다. `verify_profile_capacity_record(record)`는 digest, 원 receipt, 요청/문맥, 값과 압력 비교의 일치를 확인한다. 이 검사는 전자서명이나 현장 진위를 입증하지 않는다.

네이티브 parameters의 전체 필드는 다음과 같다.

| 필드 | 의미·단위 |
| --- | --- |
| geometry_id, material_condition_id | 요청과 같은 형상·재료 상태 |
| reference_wall_thickness_m | 기준 벽두께, m |
| axial_position_m | 엄격히 증가하는 원 축 좌표, m |
| measured_remaining_thickness_m | 각 위치의 잔여 두께, m |
| reference_capacity | 동일 형상/재료의 기준 압력차·기준 설명·근거 |
| bulging_table | 동일 형상/재료의 창 길이(m)·무차원 계수·적용 설명·근거 |
| maximum_supported_depth_fraction | 사용자 근거가 지원하는 최대 상대 손실 |
| full_axial_extent_confirmed | 실제 전체 축 방향 범위를 확보했는지 |
| blunt_metal_loss_confirmed | 균열형 결함과 구분된 감육인지 |
| internal_pressure_dominant_confirmed | 내압 우세 조건인지 |
| provider_applicability_reviewed | 기준 압력과 팽창계수의 적용을 검토했는지 |

reference_capacity에는 geometry_id, material_condition_id, reference_pressure_difference_Pa, capacity_basis, evidence를 둔다. bulging_table에는 geometry_id, material_condition_id, window_length_m, factor, validity_description, evidence를 둔다. 길이 절점은 증가하고 계수는 1 이상으로 감소하지 않아야 한다. 사용자는 자신이 이용할 수 있는 다른 공급자 값으로 바꿀 수 있다. 원 규격의 표나 보정식을 이 패키지에 넣지 않는다.

공급자 evidence의 source_id는 선택한 context 근거에 있어야 하며 citation/license_id/sha256가 일치해야 한다. 해당 공급자는 명시적인 라이선스 또는 사용 권한이 필요하다. optional sha256=None은 미상인 출처 해시로 남으며 물리 입력을 채우지 않는다. 공급자와 프로파일의 형상·재료가 다르면 원 엔진의 보류를 유지한다.

## 입력 보류와 수치 범위

리뷰·원 점 ID·계산 입력이 없으면 `needs_data`로 반환한다. 원 엔진이 없는 환경에서도 이 보류를 확인할 수 있다. 실제 계산에 필요한 엔진이 없으면 의존성 오류를 명확히 반환한다.

```python
import runpy
from pipeline_integrity.profile_capacity import evaluate_profile_capacity, verify_profile_capacity_record

request, context = runpy.run_path("examples/profile_capacity.py")["make_case"]()
record = evaluate_profile_capacity(request.model_copy(update={"parameters": None}), context)
assert record["status"] == "needs_data" and record["values"] == {}
verify_profile_capacity_record(record)
```

원 모델의 주요 경계는 다음과 같다.

- 영 잔여 두께는 천공/누설 평가가 필요한 `out_of_scope`다. 영 파열압력으로 바꾸지 않는다.
- 기준 벽보다 두꺼운 값은 원 기준·오차를 해결해야 하는 `needs_data`다. 음의 손실을 잘라서 계산하지 않는다.
- 지원 상대 깊이를 넘거나, 한 창이라도 공급자 길이 지원 밖이면 `out_of_scope`다. 불편한 창만 제외하거나 표를 외삽하지 않는다.
- 모든 공급자 선언이 참이어도 현장 적합성이나 규격 자격이 자동으로 생기지 않는다.

maximum_points는 2~500, maximum_provider_knots는 2~5,000, maximum_nodal_windows는 최대124,750이다. 직렬화 전에 계산량을 확인하며 점이나 표를 잘라 예산을 맞추지 않는다. native 상태와 진단을 그대로 유지한다.

## 선택적 압력차 비교

demand는 `Pressure(reference="differential")`로 명시한 0 이상의 내압−외압이다. demand_evidence_id가 선택 근거에 있어야 한다. 절대압·게이지압 또는 외압 우세의 음수 차이를 넣으면 거부한다. 두 절대압을 가지고 있다면 각 압력의 근거를 별도로 검토하고 공통 단위에서 압력차를 계산한 후 전달한다.

demand가 없으면 비교는 `not_requested`다. 원 계산이 완료되지 않으면 비교는 `withheld`다. 완료된 계산에서는 압력차 비와 선언한 모델 용량 이하 여부만 제공한다. 요구 압력이 용량을 넘더라도 계산 status는 computed이며 비교값은 false다. 계산 성공을 합격으로 바꾸지 않는다. standard_or_design_acceptance는 false다.

## 재현·검증·다음 작업

```powershell
python -m pytest tests/test_profile_capacity.py -q
python scripts/check_profile_capacity.py --materials PATH_TO_PINNED_MAT
```

원1모델의 실제12응답·독립 작성 기준10개·거부4개를 확인했다. 평탄 손실의 용량 감소8/9와 중앙 plateau의2/3, 선택 창과 투영 면적, 모든 절점 창 수, 영 손실과 요구 압력의 별도 판정을 비교했다. 상수 팽창계수2와 기준 압력10MPa는 독립 작성 가정이며 공학적 권장값이 아니다.

실행한 원76Python소스의 tree SHA-256은 `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다. MIT 라이선스·고지·JSON 자원과 소스를 실행 전후 비교했다. 공유 `material_source_guard`는 실제 로드된 원 package/evaluator/registry도 검사한다. owner_source_tree_sha256와 owner_source_root는 함께 지정한다. 원 엔진 스냅샷은 별도 보관하며 배포에 포함하지 않는다.

공개 집계는 `research/profile_capacity_results.json`, 상세 원 입력/receipt와 독립 기준은 `artifacts/profile_capacity`다. 실제 공개 burst 자료를 이 모델에 교정하거나 현장 검증했다는 주장은 하지 않는다. ILI/두께 격자에서 이 API로 보낼 때에는 축 방향 트랙·위치·기준 벽·물리 범위·시편/관측 정합을 별도 검토해야 한다. 원2D 격자를 자동으로 하나의 축 프로파일로 줄이지 않는다.

PI-X04 후속 요청은 원 점·검사/좌표·형상/재료·계측 오차·팽창 공급자 지원과 기준 압력차의 lineage를 수신 엔진에서 보존하는 것이다. 연속 끝점 최적화, 원주 범위, 검출·크기 오차, 국부 불연속·하중 결합은 각각 새 물리·자료 근거가 필요하다.
