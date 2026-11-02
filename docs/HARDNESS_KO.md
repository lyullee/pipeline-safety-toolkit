# 압흔 기하와 Brinell·Vickers 경도 연결

`hardness_exchange`는 기존 Mat의 `testing.brinell`, `testing.vickers`를
`MaterialJob`과 원 receipt로 호출한다. 원 엔진이 힘·압흔 기하의 계산을
담당하고 이 패키지는 시험·시편·재료 상태·위치·시계·유지 시간과 계측 근거를
보존한다. 압흔 영상 판독, 시험기 제어, 경도 척도 간 경험 환산이나 합격
판정 엔진을 새로 만들지 않는다.

출력은 압흔 기하에 대한 환산값이다. 경도로 인장강도·취성·조직·수명·균열
확률을 자동 추정하지 않는다. 계산이 `computed`여도 시험 절차 자격이나 규격
적합성을 뜻하지 않는다. 시험 하중/직경 비·유지 시간·표면·두께·간격·가장자리·
기기 교정의 허용치 표를 내장하지 않았다.

## 재현과 Python API

저장소 루트에서 실행한다. 실제 계산에는 기존 Mat를 별도 설치하거나
`--materials PATH_TO_MAT`로 해당 소스 루트를 제공한다.

```powershell
python -m pytest -q tests/test_hardness_exchange.py
python scripts/check_hardness.py --materials PATH_TO_MAT
```

검증 스크립트는 소유 소스에 import 캐시를 쓰지 않고 실행 전후 Python 소스
트리 해시를 대조한다. 공개 집계는
[hardness_results.json](../research/hardness_results.json)이다. 이 프로브의 힘·
치수·재료·조건은 독립 작성한 가상 기준이며 실측 시험 결과가 아니다.

두 공개 함수는 다음과 같다.

| API | 입력 | 반환·동작 |
|---|---|---|
| `evaluate_hardness(request, context, client=None)` | `HardnessRequest`, `RunContext`, 선택적 기존 `MaterialClient` | 상태·원 입력/조건·단위·원 receipt·해시를 보존한 JSON 직렬화 가능 dict |
| `verify_hardness_record(record)` | 위 계산 기록 전체 | 정상 기록은 반환값 없이 종료하고, 변조·원 receipt 불일치·잘못된 자격 의미는 오류 |

`HardnessRequest.model_validate_json(document)`로 JSON을 복원할 수 있다.
Python의 근거 tuple은 JSON에서 배열이 된다. 원 모델은 기존 strict JSON
경로로 호출하며 스키마·버전·단위·문맥을 바꾼 응답은 기존 MaterialJob 계약이
거부한다. `client`를 제공해도 이 보존 계약을 충족해야 한다.

## 요청의 자산·시험·조건

`HardnessRequest`에는 다음 필드를 입력한다.

| 필드 | 의미 |
|---|---|
| `method` | `testing.brinell` 또는 `testing.vickers` |
| `asset_id`, `location_id`, `clock_id` | 대상 자산·시험 위치·시계. 자산과 시계는 context와 같아야 한다. |
| `material_condition_id` | 실제 적용 재료 상태의 식별자. `None`이면 보류한다. 명목 강종에서 시험 물성을 추정하지 않는다. |
| `parameters` | 아래 원 모델 입력명을 그대로 사용하는 dict. 힘은 N, 모든 길이는 m다. |
| `conditions` | 아래 `HardnessConditions` 객체 |
| `applicability_reviews` | 방법별 검토 키와 bool 값 |
| `evidence_ids` | 중복 없는 한 개 이상의 원 입력 근거 ID |

`conditions`는 중첩 계약으로 시험 조건을 별도로 보존한다.

| `HardnessConditions` 필드 | 의미·단위 |
|---|---|
| `test_id`, `specimen_id` | 시험과 물리 시편의 식별자. 동일 시편의 여러 압흔을 독립 시편으로 세지 않는다. |
| `dwell_s` | 양의 유지 시간 s 또는 미상 `None` |
| `surface_condition_id` | 표면 상태의 식별자 또는 `None` |
| `procedure_id` | 적용 시험 절차의 식별자 또는 `None` |
| `force_evidence_id` | 실제 시험 힘·힘 계측의 근거 ID 또는 `None` |
| `length_evidence_id` | 압흔/압자 치수·길이 계측의 근거 ID 또는 `None` |
| `conditions_evidence_id` | 유지 시간·표면·절차 등 조건 근거 ID 또는 `None` |
| `geometry_applicable` | 검토한 기하 적용성. `True`, `False`, 미상 `None`을 구분한다. |

모든 알려진 근거 ID는 `RunContext.evidence`에 존재해야 한다. 요청 근거와
조건 근거의 합집합을 중복 없이 원 MaterialJob에도 전달한다. 같은 문서가
힘·길이·조건을 함께 뒷받침할 수 있으나 그 사실이 측정 오차의 독립성을
뜻하지 않는다. 재료·절차 식별자 문자열과 검토 플래그는 입력 선언을 추적하는
장치이며 시험 조건이나 자격을 자동 확인하는 모델이 아니다.

유지 시간·표면·절차·시편·재료 상태는 이쪽 기록에 남는다. 현재 원 두 모델의
기하 입력에는 이 값들이 없으므로 native `parameters`에 임의의 추가 필드로
밀어 넣지 않는다. 원 단순 계산에 조건이 사용되지 않았다는 사실을 보존한다.

## 원 입력명과 출력 단위

| method | 필수 원 입력 | 기하 조건 |
|---|---|---|
| `testing.brinell` | `test_force_N`, `ball_diameter_m`, `mean_indentation_diameter_m` | 모두 양수이고 평균 압흔 직경은 구 직경보다 작아야 한다. |
| `testing.vickers` | `test_force_N`, `first_diagonal_m`, `second_diagonal_m` | 모두 양수다. 두 대각선의 실측값을 각각 입력한다. |

Brinell은 구면 압흔의 표면 기하를 사용한다. 원 모델은 평균 압흔 직경을
받으므로 사진의 직경 후보를 임의로 선택하거나 여러 원시 직경을 숨겨 평균해
주지 않는다. 평균을 만든 원 관측과 선택·계측 근거가 필요하다. 압흔 직경이
구 직경 이상인 입력은 원 스키마 오류다.

Vickers는 대향 면 사이 각 136°인 압자 기하와 두 대각선의 산술평균을 사용한다.
한 대각선을 다른 대각선으로 복사하거나 평균값 하나로 두 원 관측을 대체하지
않는다. 대각선 차이·형상 왜곡·분해능의 적용성은 사용자가 계측 근거로 검토한다.
이 어댑터에는 그러한 품질의 규격 허용 비율이 없다.

| 출력 | 원 단위 문자열 | 해석 |
|---|---|---|
| `indentation_hardness_Pa` | `Pa` | 힘과 압흔 표면 기하로 환산한 경도 |
| `conventional_HBW` | `kgf/mm^2` | Brinell 모델이 내보내는 관용 HBW 수치 |
| `conventional_HV` | `kgf/mm^2` | Vickers 모델이 내보내는 관용 HV 수치 |

관용 수치를 Pa 필드에 그대로 넣지 않는다. 기존 단위 서비스의
`convert_quantity`로 입력 힘과 길이를 N·m로 변환할 수 있다. 예를 들어
10 kgf는 98.0665 N, 0.2 mm는 0.0002 m다. Pa와 관용 수치의 보고 기준에는
표준 중력가속도 9.80665 m/s²와 mm²↔m² 변환이 사용된다. 이 단위 환산은
경도에서 강도를 추정하는 경험식과 다르다.

출력명을 `HB`, `HV` 또는 일반 응력으로 임의 통합하지 않는다. 특히 원
Brinell 출력명은 `conventional_HBW`다. Brinell·Vickers 결과가 모두 Pa로
표현된다는 사실만으로 시험 방법·하중·재료 상태가 다른 관측을 동일 척도의
교환 가능한 측정으로 취급할 수 없다.

## 방법별 검토·미상·범위 이탈

각 방법에는 다음 세 검토가 모두 필요하다.

| 방법 | 압흔 기하 검토 키 |
|---|---|
| `testing.brinell` | `spherical_ball_and_mean_indentation_geometry` |
| `testing.vickers` | `vickers_geometry_and_two_measured_diagonals` |

공통 검토 키는 `force_dwell_surface_and_length_metrology`,
`material_condition_location_and_method`다. 현행 키는 `REVIEWS[method]`로 조회한다.
다른 방법의 키는 오류이고 필요한 키의 누락/거짓은 `needs_data`다. 예제의
`dict.fromkeys(..., True)`는 가상 기준에 대한 선언이며 실제 시험의 검토를
대신하지 않는다.

필수 native 필드가 없거나 값이 미상이면 소유 모델을 불러오기 전에 보류한다.
`parameters`의 dict/list 안쪽 `None`, 중첩 조건의 `None`과 미상 재료 상태도
그대로 보존하고 `unknown_input_paths`에 위치를 남긴다. 알 수 없는 값을
강종·정격 하중·대표 직경·표준 유지 시간으로 채우지 않는다. 추가 파라미터는
자동 제거하지 않으며 알려진 값으로 채워져도 원 스키마에 없으면 원 모델이 거부한다.

`geometry_applicable=None`은 미상이다. 필요한 검토와 입력이 갖춰진 상태의
`geometry_applicable=False`는 `out_of_scope`이고 native receipt 없이 반환한다.
이는 호출자가 검토한 기하 부적합의 보류이며 내장 규격 표에서 계산한 판정이
아니다. 반면 0 이하 힘·길이, bool 힘, Brinell 압흔 직경≥구 직경 등은 원
입력 오류다. 유효 기하에 대해 원 모델이 계산하는 값과 이 조건 판정을 구분한다.

다음 예는 Mat 없이 실행되며 중첩 유지 시간 미상을 보존한다.

```python
import runpy

from pipeline_integrity.hardness_exchange import HardnessRequest, evaluate_hardness, verify_hardness_record

cases, context = runpy.run_path("examples/hardness_methods.py")["make_cases"]()
document = cases["testing.vickers"].model_dump_json()
request = HardnessRequest.model_validate_json(document)
conditions = request.conditions.model_copy(update={"dwell_s": None})
held = evaluate_hardness(request.model_copy(update={"conditions": conditions}), context)
verify_hardness_record(held)
assert held["status"] == "needs_data" and held["native_receipt"] is None
print(held["unknown_input_paths"])
```

## 원 receipt와 실제 계산

기존 Mat가 import 가능한 환경에서 다음 독립 작성 예제를 실행한다. 두 결과의
native 입력명과 단위, 원 receipt를 확인하며 원 소스에는 쓰지 않는다.

```python
import runpy
import sys

from pipeline_integrity.hardness_exchange import evaluate_hardness, verify_hardness_record

sys.dont_write_bytecode = True
cases, context = runpy.run_path("examples/hardness_methods.py")["make_cases"]()
for method, request in cases.items():
    record = evaluate_hardness(request, context)
    verify_hardness_record(record)
    assert record["status"] == "computed"
    print(method, record["values"], record["output_units"])
```

기록 schema는 `pipeline_integrity.hardness_exchange.v1`이다. 주요 필드는
`request`, `context`, `status`, `issues`, `unknown_input_paths`, `values`,
`output_units`, `probability_semantics`, `native_receipt`, 구현 SHA-256과
`record_digest`다. 원 모델의 notes는 `issues`로 유지한다. 입력 context의
관측 origin과 기하 계산의 `output_origin="simulation"`도 구분한다.

`native_receipt`에는 실제 모델 ID·버전·입력 스키마 해시·단위·확률 의미,
원 입력과 자산·위치·시계·context 해시, 원 payload와 결과 무결성이 남는다.
두 모델의 `probability_semantics`는 빈 dict이며 경도를 확률로 해석하지 않는다.

`verify_hardness_record`는 바깥 기록뿐 아니라 원 `MaterialReceipt`의 해시와
원 입력·상태·값·단위·notes·근거·문맥을 다시 대조한다. 바깥 해시를 재계산해
달라진 원 값이나 receipt 제거를 숨길 수 없다. 허용치 적용·현장 자격·강도/
취성/확률 추정 플래그를 참으로 바꾸는 것도 거부한다. 해시가 근거의 사실성이나
실험 교정의 타당성을 증명하는 승인 서명은 아니다.

## 기존 용접 경도 traverse와 사용자 규칙

기존 `welding.hardness_traverse`는 위치·영역별 경도와 선언 상한, 확장불확실성을
다루는 별도 모델이다. 이 묶음은 그 모델이나 요구사항 판정을 재구현하지 않는다.
관련 API와 절차 한계는 [WELDING_KO](WELDING_KO.md)를 따른다.

사용자가 검토한 연결에서는 원 시험·시편·재료 상태·위치·방법·힘·유지 시간·
계측 근거와 원 receipt를 유지하면서 `indentation_hardness_Pa`를 측정 점의
`hardness_Pa`에 대응시킬 수 있다. 그 전에 동일 시험 방법과 영역/좌표 대응,
하중·조건의 적용성을 검토해야 한다. `method_id`, `test_force_N`, `dwell_s`,
위치/영역 ID와 조건별 상한의 근거는 호출자가 명시한다.

이 기하 계산은 `expanded_uncertainty_Pa`나 그 포괄 의미를 산출하지 않는다.
확장불확실성을 임의로 0으로 넣거나 소수점 자리 수를 측정 정확도로 취급하지
않는다. 실제 교정·해상도·힘/치수 오차와 상관·반복성의 근거가 별도로 필요하다.
미측정 영역은 미상으로 남기며 선언 상한을 용접 절차 승인이나 균열 확률로
승격하지 않는다. 자동 HBW↔HV 환산이나 규격 허용 경도의 기본값은 없다.

## 수치 근거와 실제 자료의 적용 범위

실제 원 두 모델 v1.0.0의 8계산 사례와 18개 독립 작성 기하·힘·단위 기준을
확인했다. 구면 3-4-5 단면의 cap 면적, 다이아몬드 면 기하, N·kgf와 m·mm,
힘 배율·길이 배율·두 대각선 순서 보존을 포함한다. 작은 구면 압흔의 별도
70자리 Decimal cap 계산도 원 안정화 계산과 대조했다. 이 극소 치수 사례는
수치 기하 검증이며 실제 계측·재료 적용성을 확인한 시험이 아니다.

38개 자동 검사는 방법별 검토·원 입력 누락·중첩 미상·범위 이탈·조건 근거·
원 필드/단위 보존·notes·receipt와 자격 의미 변조를 확인한다. native 프로브는
7보류·2명시 기하 범위 이탈·8개 원 입력 오류 거부도 확인했다. 힘/기하 산술의
비교는 상대 1e-12·절대 0 허용오차를 사용한다. 원 소스 트리의 실행 전후 해시가
같으며 원/이쪽 구현·스크립트·예제 해시는 공개 연구 집계에 있다.

이 실행 증거는 가상 SI 입력과 원 모델의 계산·계약 검증에 한정된다. 공개
자료를 취득했어도 실제 힘·두 대각선/평균 압흔·압자 치수·유지 시간·표면·
교정·재료 상태·시편 독립성의 해당 행 근거를 먼저 확인해야 한다. 완성된 경도
수치만 있는 표에서 원 압흔을 역산해 독립 원 관측처럼 사용하지 않는다.
새 실제 자료의 취득·판독·행 매핑과 실험 비교 증거는 그 작업이 검증될 때
별도 연구 기록으로 추가한다.

공개 구리 합금 23보고서/127압흔의 원조건 보류와 별도 명시 가정 기하 진단은
[구리 압흔 자료 안내](COPPER_HARDNESS_KO.md)에 설명한다. 원 표면 준비 NN을
보존하며 해당 자료의 재현 비교를 현장 검증으로 승격하지 않는다.

다음 확장은 원 계측의 힘·치수 공분산과 교정/반복성, 압흔 선택/영상 판독의
불확실성, 방법·재료·온도·하중/유지 조건의 지원과 사용자 검토 규칙의 명시적
연결이다. 소유 모델의 지원이 필요한 경우 PI-X04의
[확장 요청서](EXTENSION_REQUESTS_KO.md)에 입력·단위·근거·수용 기준을 추가한다.
새 계측이나 경험 환산을 넣을 때 원 출처·권리·모집단·독립 평가를 먼저 확인하며
미완료 시험/현장 자격을 계산 성공으로 바꾸지 않는다.
