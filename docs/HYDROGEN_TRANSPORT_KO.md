# 수소 원자 몰 기준의 확산·국부 평형·유한 슬랩 수송

`pipeline_integrity.hydrogen_transport`는 기존 Mat의 세 수송 모델을 호출한다. 확산식·포획 평형의 근찾기·보존 유한체적·비선형 해법·적응 시간 제어는 원 엔진에 남는다. 새 수송 해법이나 포획 물성 표를 이 저장소에 복제하지 않았다.

| method 상수 | 원 모델 | 물리량 |
| --- | --- | --- |
| SEMI | transport.semi_infinite_diffusion | 일정 확산계수·고정 표면 농도의 반무한 고체, 지정 깊이의 점 농도 |
| EQUILIBRIUM | transport.hydrogen_trap_equilibrium | 고정 독립 단일 점유 사이트의 국부 평형 분배 |
| KINETIC | transport.hydrogen_slab_kinetic | 유한 평판의 셀 평균 격자 농도·독립 포획 속도·표면 교환 |

모든 농도와 재고는 **수소 원자의 몰수**다. H₂ 분자의 몰농도나 ppm을 바로 넣지 않는다. 국부 평형의 농도 의존 미분 확산계수를 전체 투과 이력의 상수 확산계수로 바꾸지 않는다. 수송 계산은 취성·파괴·수명·고장확률 또는 인적오류 확률이 아니다.

## API·공통 맥락

`HydrogenTransportRequest`와 `evaluate_hydrogen_transport(request, context, *, client=None, pinned_job=None, owner_source_root=None)`를 사용한다. verify_hydrogen_transport_record는 기록 digest·원 receipt·요청/문맥·값/단위/확률 의미를 확인한다. digest는 전자서명이 아니다.

공통 입력은 method, asset_id/location_id/clock_id, material_condition_id, reference_lattice_id, population_id, parameter_support_id, parameters, input_units, evidence_ids다. geometry_id와 elapsed_time_origin_id는 수송 모델에 필요하다. concentration_basis는 hydrogen_atoms_mol_m3, concentration_measure는 방법별 point_lattice/local_total_and_partition/cell_average_lattice_and_traps다. binding_energy_reference는 SEMI에서 not_used, 포획 모델에서는 positive_relative_to_lattice_site다.

flux_sign_convention은 SEMI에서 positive_inward_at_declared_surface, EQUILIBRIUM에서 not_used, KINETIC에서 positive_inward_left_and_right다. 양쪽 kinetic 표면의 양의 방향은 모두 고체 안쪽이다. 오른쪽 표면이 좌표 양의 방향이라는 이유로 부호를 뒤집지 않는다. 방출은 음의 inward flux로 남는다.

declared_temperature_K와 temperature_support_K는 검토한 재료/모수의 온도 지원이다. SEMI·EQUILIBRIUM에서는 명시 온도가 필요하고 KINETIC에서는 원 온도 이력의 모든 점을 지원 구간과 비교한다. 하나의 declared_temperature_K도 지정한다면 이력과 같아야 한다. 지원 밖 온도는 native 계산 없이 out_of_scope로 남긴다. ID와 리뷰는 사용자의 검토 선언이며 스스로 실험 적합을 수행하거나 계수를 승인하지 않는다.

리뷰 키와 단위 사전은 `REVIEWS[method]`, `INPUT_UNITS[method]`에 있다. 모든 리뷰는 기본 미검토이며 선택한 review_evidence_ids 근거가 필요하다. None은0이 아니다. NaN/Inf는 JSON null로 바꾸기 전에 거부한다. 깊이·시간·셀의 원 행 ID는 depth_point_ids/time_knot_ids/cell_ids로 대응시키며 원 순서와 반복 깊이를 보존한다.

```python
import runpy
from pipeline_integrity.hydrogen_transport import (
    KINETIC,
    evaluate_hydrogen_transport,
    verify_hydrogen_transport_record,
)

requests, context = runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()
request = requests[KINETIC].model_copy(update={"parameters": None})
record = evaluate_hydrogen_transport(request, context)
assert record["status"] == "needs_data" and record["native_receipt"] is None
verify_hydrogen_transport_record(record)
```

미검토·물리 입력 미상은 원 엔진 import 전에 needs_data다. 계산이 필요한데 원 엔진이 없으면 의존성 오류를 명확히 반환한다. 원파형 전류에서 경계 농도를 만들어 이 보류를 우회하지 않는다.

## 네이티브 파라미터와 단위

SEMI의 전체 입력은 diffusivity_m2_s(m²/s), exposure_s(s,양수), depth_m(m배열), initial_concentration_mol_m3, surface_concentration_mol_m3다. 농도 단위는 mol/m³이며 이 facade에서는 수소 원자 기준이다. 깊이 배열이 같은 순서로 전달되며 표면 유속과 초기 대비 누적 초과량은 음수가 될 수 있다. 포획·유한 뒷벽·응력 항을 반무한 해에 추가했다고 주장하지 않는다.

EQUILIBRIUM은 material_condition_id, reference_lattice_id, total_hydrogen_atoms_mol_m3, lattice_site_density_mol_m3, lattice_diffusivity_m2_s, temperature_K, maximum_supported_lattice_occupancy, traps와 세 선언 local_equilibrium_confirmed/independent_single_occupancy_sites_confirmed/fixed_trap_population_and_uniform_temperature_confirmed를 받는다.

각 평형 trap은 trap_id, 같은 재료/격자 ID, site_density_mol_m3, binding_energy_J_mol, maximum_supported_occupancy, evidence를 받는다. 양의 binding은 격자 사이트에 대한 결합 에너지다. 농도·사이트 밀도는 mol/m³, 에너지는 J/mol, 온도는 K, 점유는 무차원이다. trap 목록이 명시적으로 비어 있는 것과 미상인 것은 다르다. 입력이 사이트 용량 또는 지원 점유를 넘으면 원 out_of_scope를 유지한다. 영 수소의 log odds=None을 가짜 유한 수로 채우지 않는다.

KINETIC의 전체 입력은 다음과 같다.

- material_condition_id, reference_lattice_id, elapsed_time_origin_id, thickness_m, cell_count, lattice_site_density_mol_m3.
- initial_lattice_hydrogen_atoms_mol_m3(셀 평균 배열), elapsed_time_s, uniform_temperature_K, lattice_diffusivity_m2_s(시간 이력).
- left_boundary, right_boundary, 명시적인 traps 목록.
- maximum_supported_lattice_occupancy, maximum_supported_total_trap_to_lattice_site_ratio.
- planar_geometry_reviewed, fixed_populations_and_uniform_temperature_reviewed, independent_single_occupancy_and_dilute_trap_regime_reviewed, negligible_stress_gradient_reviewed, detailed_balance_rate_ratio_reviewed.
- relative_tolerance, absolute_occupancy_tolerance, maximum_step_s, maximum_step_attempts, maximum_relative_mass_balance_error, store_profiles.

슬랩 길이는 m, 시간은 s, 공간 균일 온도는 K다. 시간은 실제 경과 원점0에서 엄격히 증가해야 한다. 초기 격자/포획 배열은 셀 수와, 계수/온도/경계/속도 이력은 시간 절점과 같아야 한다. maximum_step_s=None은 명시적인 추가 최대 간격을 두지 않는 원 선택값이며 미상 물성을 뜻하지 않는다. 세부 입력은 원 schema/job pin으로 고정한다.

kinetic trap은 population(위 평형 trap 선언), rate_model, capture_frequency_per_s, release_frequency_per_s, initial_occupancy, independent_rate_histories_reviewed, rate_evidence를 받는다. 속도는1/s다. binding_energy_ratio 모드의 release_frequency_per_s=None은 상세평형 비율로 정해지는 모드의 정상 입력이다. independent_frequencies 모드는 별도 release 이력과 검토가 필요하며, 이를 온도나 binding 값으로 덮어쓰지 않는다. 공급한 유효 빈도를 원자적 전이 prefactor나 비가역 포획의 증거로 재표시하지 않는다.

경계는 고유 boundary_id, kind, evidence를 받는다. no_flux에는 농도 이력이 없다. prescribed_lattice_concentration은 lattice_hydrogen_atoms_mol_m3, linear_surface_transfer는 equilibrium_lattice_hydrogen_atoms_mol_m3와 transfer_coefficient_m_s(m/s), dissociative_surface_reaction은 absorption_capacity_flux_mol_m2_s와 desorption_capacity_flux_mol_m2_s(mol/(m²·s))를 받는다. 원 엔진이 표면 전달과 반 셀 확산 저항을 함께 처리한다.

교환계수와 반응 용량은 검토한 외부 입력이다. 기체 압력·전류·진공·가열속도에서 자동으로 농도/교환계수/반응속도를 추정하지 않는다. CoolProp 상태만으로 표면 fugacity·흡착·용해도·검출기 응답을 보장할 수도 없다. 실제 표면 화학과 전류 관측 변환은 별도 소유자 계약이 필요하다.

## 예산·출력·검증

maximum_depth_points(최대100,000), maximum_cells(512), maximum_time_knots(4,096), maximum_trap_populations, maximum_state_values(1,000,000), maximum_step_attempts를 지정할 수 있다. 원 kinetic은 최대16포획군이다. 직렬화/호출 전에 예산을 검사하고 데이터를 잘라 맞추거나 solver 설정을 바꾸지 않는다.

원 out_of_scope·needs_data와 진단값을 보존한다. 시도 횟수가 소진된 부분 궤적을 완료 결과로 바꾸지 않는다. 완성된 재고/표면 유속/누적 교환/질량 잔차와 solver 진단을 그대로 읽는다. 시간의 국부 오차 제어나 작은 질량 잔차만으로 공간·전역 오차 또는 실험 정확성을 보장하지 않는다.

```powershell
python -m pytest tests/test_hydrogen_transport.py -q
python scripts/check_hydrogen_transport.py --materials PATH_TO_PINNED_MAT
```

원3모델16응답·독립 작성 기준21개·거부4개를 확인했다. 반무한 점 농도·signed 방출·영 수소·영 결합 사이트 분배, 폐쇄 균일 재고, 독립 capture/release 이력과 두 표면 법칙의 균형을 비교했다. 유한 양면 농도 경계에서는8/16셀 평균이68.79146/69.39099 mol/m³, 독립 연속 슬랩 급수는69.78819 mol/m³였다. 오차가 약0.99673→0.39720 mol/m³로 줄었으며 해당 질량 잔차 비는 약3.5e-16/2.0e-16이었다. 두 격자 비교는 일반 수렴 증명이나 현장 검증이 아니다.

공개 집계는 `research/hydrogen_transport_results.json`, 상세 배열·원 receipt·급수 기준은 `artifacts/hydrogen_transport`다. 고정 원76Python소스 treeSHA `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`, MIT 라이선스·고지·JSON 자원의 실행 전후 불변을 확인했다. owner_source_tree_sha256와 owner_source_root를 함께 주면 공통 guard가 실제 로드된 소스도 검사한다. 원 스냅샷은 배포에서 제외한다.

[새 공정 자료 검토](MATERIAL_PROCESS_DATA_KO.md)의 AHSS 전류 이력에는 실제 활성 면적·유효 경계 농도·D/포획/초기 상태의 근거가 부족하다. 현재 관측을 이 수송 모델에 보정했다는 주장은 하지 않는다. PI-X04에는 원 시계·표면·면적·검출기·전자수/효율·원자 몰·모수 지원과 source-group 분리를 유지하는 수신 계약을 요청한다. 이후 실험이 생겨도 검출기 관측을 경계 조건과 혼동하지 않아야 한다.
