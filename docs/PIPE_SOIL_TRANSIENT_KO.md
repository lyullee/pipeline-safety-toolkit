# 관벽·단열·토양의 원통형 과도 열 경계

`pipeline_integrity.pipe_soil_transient`는 기존 재료 엔진의
`thermal.transient_conduction_1d`를 재사용한다. 관벽에서 토양까지의 동심 원통
층과 각 셀의 축열, 시간에 따른 경계 온도·열유속·대류/복사를 연결한다.
기존 재료 엔진의 유한체적·비선형 해석·물성 적분 알고리즘을 복제하지 않는다.
공통 SI·자산·위치·시계·사건·근거·원 버전/스키마/소스 고정을 덧붙인다.

이 경로의 바깥 면은 **관 축과 동심인 원통**이다. 실제 수평 지표면·매설 깊이의
반공간을 같은 체적이나 같은 축열로 간주하지 않는다. 정상 shape factor를 맞춘
가상 반경을 시간 변화 해석의 실제 토양 체적으로 사용하지 않는다.
지표면/복수관의 실제 단면은 `soil_cross_section`의 별도 2차원 경로를 검토한다.
토양의 수분 이동·접촉 저항·유체 에너지 피드백을 포함한 전체 연성은 partial이다.

## API와 안전한 기본 실행

```python
import runpy
from pipeline_integrity.pipe_soil_transient import (
    evaluate_pipe_soil_transient,
    verify_pipe_soil_transient_record,
)

request, context = runpy.run_path("examples/pipe_soil_transient.py")["make_case"]()
record = evaluate_pipe_soil_transient(request, context)
verify_pipe_soil_transient_record(record)
assert record["status"] == "needs_data"
assert record["native_receipt"] is None
```

`evaluate_pipe_soil_transient(request,context,*,client=None,owner_source_root=None)`는
`PipeSoilTransientRequest`와 `RunContext`를 받는다. 사용자가 검토한 원 소스의
SHA-256, 모델 버전, 입력 스키마 digest가 없으면 원 모델 import 전에 보류한다.
실제 호출은 기존 `MaterialClient`, `prepare_material_job`, `evaluate_material_job`과
`material_source_guard`를 사용한다. 다른 저장소의 함수·모델을 임의로 교체하는
client도 같은 원 입력·출력·근거·소스 계약을 만족해야 한다.

원 소스 배포 주소가 확인되지 않은 경우 주소를 추측하지 않는다. 이용 권한을
확인한 원 소스의 `src` 경로를 선택하고, `MaterialClient.describe`의 현재 버전과
입력 스키마를 검토한 후 pin을 선언한다. 연구에서 사용한 원 소스는 76 Python
파일 SHA-256 `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다.
원 LICENSE·고지·자원을 별도 보존하며 코드 배포에는 그 소스를 포함하지 않는다.

## 입력 전체와 물리 의미

| 입력 | 의미 |
| --- | --- |
| asset_id/location_id/clock_id/event_id | 공통 자산·단면 위치·분석 시계·열 사건 |
| geometry | `concentric_cylindrical_shells` 고정; 평면/지표면으로 재표시 불가 |
| cylinder_length_m | 모든 원통 셀과 면에 적용되는 축방향 길이 m |
| materials | 물성 조건별 `PipeSoilMaterial` tuple |
| shells | 안쪽 관벽에서 바깥 토양까지의 `PipeSoilShell` tuple |
| time_s | 0에서 시작해 증가하는 국소 시각 s; 원순서 유지 |
| clock_offset_s | 공통 시계로 옮길 때 더하는 명시 offset s |
| temporal_interpolation | `linear_between_declared_knots`; ZOH로 해석하지 않음 |
| inner_boundary/outer_boundary | 원통 안·밖의 서로 다른 `PipeSoilBoundary` |
| perfect_contacts | 명시 True만 원 실행 가능; None은 미상, False는 미지원 |
| maximum_step_s | 원 implicit solver의 최대 시간 간격 s |
| nonlinear_absolute_tolerance_K/nonlinear_relative_tolerance | 원 비선형 온도 수렴 기준 |
| maximum_nonlinear_iterations | 원 비선형 반복 상한; 기본 80 |
| maximum_relative_energy_balance_error | 원 모델의 이산 에너지 상대 판정 기준 |
| audit_absolute_tolerance_J/audit_relative_tolerance | 이쪽 독립 최종 에너지 감사의 허용오차 |
| owner_source_tree_sha256 | 원 Python 소스 고정; 기본 None은 보류 |
| expected_model_version/expected_input_schema_digest | 검토한 원 모델과 입력 스키마 고정 |
| applicability_reviews/review_evidence_ids | 다섯 검토 키와 각각의 선택 근거 ID |
| evidence_ids | context에 존재하는 중복 없는 선택 근거 |
| input_units | 모듈의 `UNITS`와 정확히 같은 SI/부호 기준 |

물성에는 `id`, 두 개 이상 증가하는 `temperature_K`, 같은 길이의
`conductivity_W_m_K`, `volumetric_heat_capacity_J_m3_K`, `evidence_id`를 준다.
물성은 온도 사이에서 원 모델의 선형 보간을 사용하고 지원 밖으로 외삽하지 않는다.
같은 온도의 밀도와 질량 비열을 새로 추정하지 않고 부피 열용량을 직접 받는다.
물성 값 None은 결측이며 0으로 바꾸지 않는다. 규격·공급자 물성표는 내장하지 않는다.

각 shell은 `id`, `role=pipe_wall|insulation|soil`, `material_id`, 증가하는
`radial_edges_m`, 셀마다의 `initial_cell_temperature_K`, `evidence_id`를 갖는다.
초기 온도는 셀 평균이다. 층의 마지막 반경과 다음 층의 첫 반경은 같은 값을
재사용해야 한다. 간격·겹침·순서·접촉 조건을 자동 수리하지 않는다.
첫 층은 관벽이고 마지막 층은 토양이며, 토양 뒤에 관벽/단열을 배치하지 않는다.
유효 재료와 형상 근거는 선택 근거에 포함되어야 한다.

경계 종류와 필요한 값은 다음과 같다. 모든 시간 배열은 `time_s`와 정확히 정렬한다.

| kind | 필요한 값 |
| --- | --- |
| unresolved | 미확정 경계; 원 실행 보류 |
| adiabatic | 추가 값 없음; 명시적인 단열이며 결측이 아님 |
| prescribed_temperature | temperature_K |
| prescribed_heat_flux | heat_flux_into_solid_W_m2 |
| convection_radiation | convection_W_m2_K, ambient_temperature_K, emissivity, radiative_environment_temperature_K |

각 경계에는 `id`와 `evidence_id`도 필요하다. 복사 없는 대류는 emissivity를
명시적으로 0으로 선언한다. 비활성 종류에 값을 넣으면 거부한다. 안·밖 모두
열의 양수 방향은 **고체 내부로 유입**이다. 원통 바깥에서 빠져나가는 열은
음수이므로 두 경계 열을 모두 양의 손실로 바꾸어 더하지 않는다.

context의 `metadata.pipe_soil_boundary`는 `pipe_soil_boundary(request)`로 만든
자산/위치/사건/시계/offset/보간/형상/부호와 같아야 한다. 다섯 검토 키는 다음과 같다.

- concentric_shell_geometry_and_axial_uniformity
- initial_cell_averages_and_material_support
- linear_boundary_history_and_clock_mapping
- perfect_contacts_and_absent_moisture_transport
- phase_change_absent_or_represented_in_supplied_capacity

마지막 검토는 상변화가 없는지 또는 주어진 부피 열용량에 잠열이 반영됐는지에
대한 별도 근거다. 다른 물성 검토만으로 원 모델의 상변화 검토를 자동 승인하지 않는다.

## 예산·미상·실패

셀은 2~500개, 물성 조건과 층은 각각 최대 100개다. 보관하는 셀×시각 값은
최대 200,000개다. 원 모델의 시간 상한 외에 이쪽은 `maximum_steps`(기본10,000),
`maximum_work_units`(기본20,000,000)를 검사한다. 후자는 예상 substep×셀×허용
비선형 반복의 보수적인 규모이며 실제 측정 CPU 시간이 아니다.
예산 초과 시 해상도·이력·solver 기준을 자동으로 낮추지 않는다.

검토·초기 온도·물성·활성 경계가 미상이면 needs_data다. 유한 접촉 저항,
초기 물성 지원 이탈은 원 실행 전 out_of_scope다. 잘못된 단위·식별자·시계·
겹침·배열·NaN/Inf/bool 오용·소스/스키마 불일치는 예외로 거부한다.
모델의 실행 중 지원 이탈/비선형 실패는 원 out_of_scope와 값을 그대로 보존한다.
완료하지 않은 온도 이력으로 성공 값을 만들지 않는다.

## 결과·에너지·신호

결과 schema는 `pipeline_integrity.pipe_soil_transient.v1`이다. 원 요청/context,
상태/한계, 원 `MaterialReceipt`, source_pin_report, audit, 구현/전체 digest를 보존한다.
미실행 때 native_receipt/source_pin_report/audit은 None이다.
최종 층별 에너지 변화는 정확한 원통 셀 체적과 제공된 부피 열용량의 온도 적분으로
독립 감사한다. 원 solver의 substep 적분 경계 J 합과 비교하며,
온도에 따라 달라지는 열용량을 마지막 값×온도차로 대체하지 않는다.
최종 수지 감사가 실패하면 out_of_scope이며 하류 신호 출력은 차단한다.

총 에너지가 맞아도 서로 반대인 잘못된 두 경계 열률은 상쇄될 수 있다.
따라서 각 출력 시점에서 원통 면적·반 셀 열저항·셀/표면 온도와 경계 W의
관계도 확인한다. 단열은 W=0, 지정 온도는 원 knot, 지정 열유속은 실제
원통 면적을 곱한 W와 일치해야 한다. 대류/복사도 선언한 주위 온도·계수·
방사율과 일치해야 한다. computed 상태가 보고한 원 상대 수지 기준을 위반하거나
반복/step 진단이 예산을 넘는 경우도 거부한다. 재해시만으로 이 모순을 승인하지 않는다.

`verify_pipe_soil_transient_record(record)`는 원본을 import하지 않고 저장된 요청,
source 보고·버전/스키마·근거/context·원 입력·단위·초기 상태와 audit의 투영을
다시 확인한다. 해시는 서명이나 실제 해석 실행의 독립 증명이 아니다.
`pipe_soil_signal_frames(record,context)`는 computed와 에너지 감사 통과 때만
각 셀 평균 온도 K, 안·밖 경계의 **출력 시점 열률 W**를 공통 시계로 만든다.
이 W를 시간 구간 평균 열률이나 원 적분 J로 재표시하지 않는다. 원 모델이 반환하지
않는 구간별 누적 에너지나 벽/토양 계면 flux를 임의 복원하지 않는다.

자격 flag는 지표면 평면·복수관·수분 이동·유체 에너지 피드백·현장/규격 자격을
모두 false로 유지한다. 유체 측 온도 이력을 공급할 수 있으나 유체 재고·엔탈피를
동시에 풀어 되먹임한 결과가 아니다. 필요한 유체 물성은 별도 CoolProp 경계에서
유체·상·절대압·기준을 검토하고 제공해야 한다.

## 실제 재현과 기준

```powershell
python scripts/check_pipe_soil_transient.py --materials PATH_TO_REVIEWED_MATERIAL_SOURCE
python examples/pipe_soil_transient.py
python -m pytest tests/test_pipe_soil_transient.py -q
python -m ruff check src/pipeline_integrity/pipe_soil_transient.py examples/pipe_soil_transient.py tests/test_pipe_soil_transient.py scripts/check_pipe_soil_transient.py
```

작성한 독립 기준은 두 재료의 정상 log 온도/열률, 안쪽 원통의 일정 ±열유속
적분, 원통 확산식의 분리 가능한 Bessel 모드와 셀 체적 평균이다. 같은 모드의
공간·시간 세분과 가변 열전도도/부피 열용량 에너지를 확인한다. 이 수치 기준과
시계·단위·저장 오염 거부를 따로 구분하며 모두를 실험 검증 수로 세지 않는다.
예제의 열물성·경계는 독립 작성한 가상 값이며 토양 물성의 권장값이 아니다.

| 층별 셀 수 | Bessel 모드 최종 최대 절대 오차 K |
| --- | ---: |
| 4 | 0.14762537 |
| 8 | 0.04001599 |
| 16 | 0.01054753 |

같은 시간 간격에서 공간 해상도를 높인 작성 기준의 오차다. 이는 실제 토양의
예측 정확도 표가 아니다. 최종 재현은 13사례·원 호출10개·작성 수치/계약 기준34개,
의미가 바뀐 재해시 기록12개 거부와 집중 pytest40개를 확인한다.
공개 `research/pipe_soil_transient_results.json`에는 집계·해시·기준 오차와 한계만
두고 전체 온도/요청/응답은 `artifacts/pipe_soil_transient`에 보존한다.

실제 토양 온도 자료의 수직 깊이·일별 극값·처리 시각을 관 축 기준 반경이나
순간 경계 온도로 바꾸지 않는다. [토양 자료](SOIL_DATA_KO.md)의 원 관측·처리
계보·근거/좌표 결합을 먼저 검토한다. 관 벽 경계·재료·반경·초기 상태가 없으면
관측량을 이 모델의 물리 성능 검증 자료로 표시하지 않는다.

## 외부 확장 요청

PI-X04에는 원 열 모델의 유한 접촉 저항, 내부 계면 열률과 시간 구간별 누적
에너지, 셀별 엔탈피/partial prefix, 단위·부호·원 좌표/시각과 물성/소스 pin을
유지하는 정식 계약을 요청한다. 영 순열량에서 상대 수지 판정이 민감해지는 기존
원 한계도 유지한다. 이쪽 독립 감사가 통과했다고 원 상태를 임의 승격하지 않는다.
실제 반공간·복수관·수분/지하수/상변화·유체 되먹임은 각각의 물리 영역과
에너지·자료 계약으로 검증한 후 연결한다.
