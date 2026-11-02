# 조건을 보존하는 부식·마모와 면적 극값 연동

`pipeline_integrity.corrosion_wear`는 기존 재료 엔진의 세 모델을 호출하는 인터페이스다. 원 엔진이 침투량, 접촉 마모량, Gumbel 적합·진단·재표집을 계산한다. 이 저장소는 해당 알고리즘을 다시 구현하지 않는다. 호출자는 재료·환경·접촉·표면 모집단, 입력의 물리량 의미, 검토 증거, 적용 범위를 먼저 지정한다.

| method | 입력의 의미 | 결과의 의미 |
| --- | --- | --- |
| `corrosion.power_law` | 검토한 기준 시간과 침투 계수, 물리 노출 시간 | 선언한 평균 침투 또는 최대 침투량 |
| `degradation.archard_wear` | 같은 접촉 조건의 무차원 계수·경도, 법선 하중, 누적 미끄럼 거리, 분포 면적 | 제거 부피와 면적에 분포시킨 평균 깊이 |
| `corrosion.gumbel_area_maximum` | 한 노출 조건의 동일 면적 블록별 완전 관측 최대 깊이 | 같은 표면 모집단에서 지정한 면적의 최대 깊이 분위수와 깊이 초과 확률 |

마모 평균 깊이를 국부 최대 깊이로 바꾸지 않는다. 면적 극값의 초과 확률은 한 노출 조건의 공간 확률이며 미래 운전 기간의 고장 확률이 아니다. 이를 HRA·FTA·ETA의 사건 확률로 자동 연결하지 않는다. 확률 의미는 `spatial_size_exceedance_at_one_exposure`로 유지한다.

## Python API와 조건 선언

`CorrosionWearRequest`에는 method, asset/location/clock, `material_condition_id`, `environment_or_contact_id`, `population_id`, `quantity_meaning`, parameters, input_units, evidence_ids를 지정한다. `parameters=None` 또는 내부 결측값은 미상으로 남는다. 리뷰 이름과 입력 단위 사전은 각각 `REVIEWS[method]`, `INPUT_UNITS[method]`에서 얻을 수 있다. 이 상수를 사용하는 것만으로 검토가 완료되는 것은 아니다. `applicability_reviews`의 각 참값에는 `review_evidence_ids`의 근거가 있어야 한다.

`LossDomain`은 검토한 재료·조건·모집단·물리량, `parameter_support_id`, `evidence_id`, `calibration_reviewed`를 묶는다. 침투 모델에는 물리 노출 원점과 `exposure_range_s`, 마모 모델에는 `normal_force_range_N`, `sliding_distance_range_m`, `hardness_range_Pa`가 필요하다. 다른 조건 또는 범위 밖 요청은 `out_of_scope`, 미검토·결측은 `needs_data`로 반환하며 수치 결과를 만들지 않는다.

`parameter_support_id`는 검토 자료의 식별자이며 계수 적합을 수행하거나 새로운 계수값을 승인하지 않는다. 검토한 계수·기준 시간 자체를 고정하려면 해당 입력으로 `prepare_material_job`을 만들고 `pinned_job`으로 전달한다. `evaluate_corrosion_wear(request, context, *, client=None, pinned_job=None, owner_source_root=None)`는 입력 스키마·버전·조건·증거를 보존한 `MaterialReceipt`를 포함한다.

다음 코드는 원 엔진을 가져오지 않고 미검토 상태를 확인한다. 예제 계수는 독립적으로 작성한 수치 확인용이며 특정 재료에 권장하는 값이 아니다.

```python
import runpy
from pipeline_integrity.corrosion_wear import evaluate_corrosion_wear, verify_corrosion_wear_record

requests, context = runpy.run_path("examples/corrosion_wear.py")["make_cases"]()
request = requests["corrosion.power_law"].model_copy(update={"applicability_reviews": {}})
record = evaluate_corrosion_wear(request, context)
assert record["status"] == "needs_data"
assert record["values"] == {} and record["native_receipt"] is None
verify_corrosion_wear_record(record)
```

## 입력 단위와 해석

침투의 `coefficient_m`는 m, `exponent`는 무차원, `exposure_s`와 `reference_time_s`는 s다. 계수는 선언한 기준 시간과 함께 검토해야 한다. 로그 기록 첫 시점은 부식 시작 시점으로 자동 해석하지 않는다. `exposure_origin_id`와 domain 원점이 다르면 보류한다. 평균 손실에 적합한 계수를 최대 침투에 재사용하려면 새로운 근거가 필요하다.

마모의 `dimensionless_wear_coefficient`는 무차원, `normal_force_N`는 N, `sliding_distance_m`는 m, `hardness_Pa`는 Pa, `distributed_area_m2`는 m²다. 문헌의 부피/(힘·거리) 계수는 경도를 포함한 차원 계수일 수 있으므로 무차원 계수 자리에 넣지 않는다. 다른 하중·표면·윤활·재료 상태의 경도나 계수를 자동 매칭하지 않는다. 명시적인 영 하중·영 거리·영 계수는 영으로 유지하지만 미상 경도나 면적을 영으로 채우지 않는다. 이 모델이 fretting·부식과 마모의 상호작용을 입증하지는 않는다.

면적 극값은 깊이와 깊이 임계값이 m, 블록 면적이 m²다. `block_ids`와 `maximum_depth_m`는 같은 순서·길이를 유지한다. 최소 8개, 최대 5,000개 블록이며 시간 연속 관측이나 선택된 깊은 피트 목록을 독립 블록으로 바꾸지 않는다. 타깃 블록 수는 정수이며, `maximum_target_to_sampled_area_ratio`로 면적 확장 한계를 선언한다.

완전 관측, 동일 노출·표면 모집단, 동등 독립 블록의 세 선언이 모두 필요하다. 진단 재표집과 파라미터 재표집은 각각 99~1,999회이며 요청의 `maximum_total_resamples`로 합계를 제한한다. `seed`를 보존한다. 입력을 잘라 예산을 맞추지 않는다. 상수 최대값은 양의 척도를 식별하지 못하므로 `needs_data`다.

Anderson–Darling 기반 재적합 진단과 음의 물리 깊이에 배정된 확률을 원 엔진이 보고한다. 선언한 진단 조건이 충족되지 않으면 값이 남아 있어도 상태는 `needs_data`다. 음의 꼬리를 잘라서 합격시키지 않는다. 진단 통과만으로 독립성·꼬리 외삽·현장 적합성이 입증되지는 않는다. 파라미터 백분위 구간은 가정한 분포 안의 표본 변동을 나타내며, 실제 한 표면 최대 깊이의 예측 구간이 아니다.

## 재현과 원 소스 고정

```powershell
python -m pytest tests/test_corrosion_wear.py tests/test_material_source.py -q
python scripts/check_corrosion_wear.py --materials <검토한-Mat-체크아웃>
python scripts/review_corrosion_source.py --help
```

이번 실행은 원 저장소가 동시에 변경되는 상황을 피하기 위해 원본을 수정하지 않고 별도 비공개 스냅샷을 사용했다. Python 76개 소스 트리 SHA-256은 `0d9471bbe4e38b86b34610221e7adacd114e4e31d442fcdb9bb5afc7fec3efda`다. MIT 라이선스·고지·JSON 자원을 원 바이트로 보존했고 실행 전후를 비교했다. 스냅샷을 이 저장소의 배포물에 포함하지 않는다. 재현 시 같은 원 소스를 확보하거나 새 소스의 해시를 명시적으로 검토하고 결과를 다시 생성해야 한다. 파일 수만 같다는 것은 버전 일치의 증거가 아니다.

`owner_source_tree_sha256`과 `owner_source_root`는 함께 제공한다. 공통 `material_source_guard`가 실제 로드된 패키지·평가기·registry의 위치와 소스 해시를 검증한다. 미지정이면 기존 엔진 설치를 사용한다. 원 엔진이 없을 때도 미검토 요청은 `needs_data`로 반환하지만 계산이 필요한 요청은 의존성 오류를 숨기지 않는다.

공개 집계는 `research/corrosion_wear_results.json`, 상세 입력·영수증과 독립 기준은 `artifacts/corrosion_wear`에 남긴다. 공개 집계에는 원 관측 행이나 개인 로컬 경로를 넣지 않는다. `verify_corrosion_wear_record`는 기록 digest, 원 영수증·요청·문맥·결과의 일치와 보류 결과의 계약을 검사한다. 이 digest는 전자서명이나 현장 진위 증명이 아니다.

## 확인한 범위와 다음 연구

원 엔진의 세 모델을 실제 호출해 침투 0.0004 m, 마모 부피 1e-11 m³·분포 평균 깊이 1e-8 m, 영 입력, 물리 적용 범위 이탈을 확인했다. 면적 극값에서는 적합된 위치·척도로 분위수와 블록 CDF의 거듭제곱을 독립 스칼라 기준과 비교했다. 이 확인은 적합·재표집 알고리즘이나 실제 부식 꼬리에 대한 독립 현장 검증이 아니다. 수치 기준·거부 사례·실행 버전과 해시는 공개 집계의 실제 실행값을 따른다.

첫 검증 실행에서는 모의 표본의 적합도 p값이 1이어서 p값 하한을 높이는 것만으로 보류 상태가 만들어지지 않았다. 검증 입력을 양의 최대값 범위에서 이동해 원 모델의 음의 깊이 꼬리 진단을 실제로 위반하는 사례로 바꿨다. 계산을 바꾸어 테스트에 맞추지는 않았다. 독립 검토에서 영수증 없는 기록의 상태·빈 결과 검사를 보완했고 회귀 검사를 추가했다.

[공개 데이터 적용 가이드](CORROSION_SOURCE_KO.md)는 구리 센서와 AFM 실험의 원 관측·단위 근거·결측 조건을 구분한다. 현재 해당 자료를 위 세 모델에 물리적으로 보정했다는 주장은 하지 않는다. 이후 실제 물리 노출 원점·단위와 재료/환경별 적합·별도 검증 자료, 접촉 조건과 경도의 대응 근거, 독립 블록 검사 설계가 확보되면 같은 API에 추가할 수 있다. 검사 POD·기간 고장·인적 대응으로 전달할 때는 각각의 수신 모듈 계약과 별도의 근거를 충족해야 한다.
