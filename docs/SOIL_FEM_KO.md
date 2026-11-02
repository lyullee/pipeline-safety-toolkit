# 다각형 공동과 비직교 단면의 P1 토양 열

`soil_fem`은 scikit-fem 12.0.2의 삼각형 P1 요소·적분·행렬 조립·경계 자유도 제거와
희소 풀이를 사용한다. 기존 `SoilCrossSectionMesh`와 `validate_mesh`를 재사용한다.
일정한 토양 물성, 주어진 다각형 배관 공동과 관축 방향으로 동일한 단면을 다룬다.
지금의 공동은 입력한 다각형 그 자체이며 곡선이나 원관을 자동 추정하지 않는다.
관벽·유체 재고·수분·이류·동결·상변화와 현장 교정은 이 계산에 포함되지 않는다.

이 서비스는 기존 FiPy 기반 직교 단면과 같은 물리 분야의 다른 수치 공급자다.
공통 형상·근거·시계는 공유하지만 P1 노드장과 유한체적 셀 평균을 같은 자료라고
취급하지 않는다. 원 유한요소 구현이나 다른 저장소의 열전도 엔진을 복제하지 않는다.

## 설치와 재현

```powershell
python -m pip install -c requirements-tested.txt ".[soil-fem,dev]"
python examples/soil_fem.py
python scripts/check_soil_fem.py
python -m pytest tests/test_soil_fem.py -q
python -m ruff check src/pipeline_integrity/soil_fem.py examples/soil_fem.py scripts/check_soil_fem.py tests/test_soil_fem.py
```

기본 설치는 scikit-fem이나 SciPy를 요구하지 않는다. 미검토·미상 요청은 해당
라이브러리 import 전에 보류하며, 검토된 계산에 의존성이 없으면 실제 ImportError를
반환한다. 실행한 환경은 Python3.12.14·scikit-fem12.0.2·NumPy2.5.4·SciPy1.18.1이다.
178,478바이트의 선택 wheel만 추가했고 기존 NumPy/SciPy 버전은 바꾸지 않았다.

출처는 [공식 저장소](https://github.com/kinnala/scikit-fem),
[조립과 경계 적분 API](https://scikit-fem.readthedocs.io/en/stable/howto.html),
[공식 배포 메타데이터](https://pypi.org/pypi/scikit-fem/12.0.2/json)다.
`skfem/`과 배포 패키지는 BSD-3-Clause이며 원 개발자 고지를 유지한다.
문서 예제는 별도 조건을 가질 수 있어 복사하지 않았다. 이 패키지는 라이브러리
코드·바이너리·설치 환경을 배포에 포함하지 않는다. 실제 라이선스·소스·의존성
버전의 해시는 [실행 집계](../research/soil_fem_results.json)의 backend를 따른다.

## API와 데이터 의미

공개 호출은 `evaluate_soil_fem(SoilFEMRequest, RunContext) -> dict`,
`verify_soil_fem_record(record, request=None, context=None) -> dict`,
`to_signal_frames(record, context, *, allow_partial=False)`다.
검증 성공은 `{"valid": True}`이며 저장 무결성·현재 수치 조건 확인이다.
전자서명·실제 실행 사실의 독립 증명·설비 승인서는 아니다.

```python
import runpy
from pipeline_integrity.soil_fem import evaluate_soil_fem, verify_soil_fem_record

request, context = runpy.run_path("examples/soil_fem.py")["make_case"]()
record = evaluate_soil_fem(request, context)
assert record["status"] == "needs_data"
assert record["native_called"] is False
assert verify_soil_fem_record(record, request, context)["valid"]
```

`SoilFEMRequest`의 입력은 다음과 같다.

| 필드 | 계약 |
|---|---|
| asset_id, location_id, clock_id, event_id, clock_offset_s | 설비·위치·시계·사건·비음수 경과 시계 오프셋 |
| mode | steady 또는 transient. steady 시간은0 하나, transient는0부터 엄격 증가 |
| mesh | 기존 SoilCrossSectionMesh. vertex/face/cell ID와 경계 그룹 순서를 유지 |
| time_s, maximum_step_s | 공통 입력 매듭과 내부 최대 단계 |
| history_interpolation | linear 또는 previous_knot_hold |
| initial_node_temperature_K | 원 vertices_m 순서의 P1 노드 온도. transient에는 모두 필요 |
| conductivity_W_m_K | 양의 일정 열전도도; null이면 보류 |
| volumetric_heat_capacity_J_m3_K | 양의 일정 체적 열용량; transient에 필요 |
| temperature_support_K | 검토한 양의 Kelvin 물성 지원 범위 |
| boundaries | 아래 SoilFEMBoundary로 모든 공통 경계 그룹을 정확히 한 번 지정 |
| triangulation | local_delaunay 또는 convex_fan |
| mass_scheme | vertex_quadrature_lumped 또는 consistent |
| robin_quadrature | vertex_lumped 또는 consistent |
| geometry_evidence_id, property_evidence_id, nodal_field_evidence_id | 선택한 형상·물성·P1 자료 근거 |
| evidence_ids | 중복 없이 실제 context에 있는 근거 ID |
| applicability_reviews, review_evidence_ids | 6개 REVIEWS의 명시 True와 해당 선택 근거 |
| input_units | 모듈 INPUT_UNITS의 정확한 SI 선언 |
| expected_skfem_version | 선택한 native 버전; 검증한 기본12.0.2 |
| absolute_energy_tolerance_J | 기본1e-7J의 에너지 잔차 한도 |
| absolute_power_tolerance_W | 기본1e-8W의 식 잔차 한도 |
| relative_residual_tolerance | 기본1e-9; 실제 열·저장 변화 크기를 기준으로 적용 |
| temperature_comparison_tolerance_K | 기본1e-8K, 최대1e-5K의 수치 비교 여유 |

원 메시의 꼭짓점은 최대4,096개, 볼록 셀은 최대2,000개다. 내부 단계는2,000개
이하이고 노드·셀 평균·경계 이력을 합친 사전 필드 예산도 검사한다. 큰 입력을
조용히 줄이거나 시간·좌표를 정렬해 수리하지 않는다. NaN/Inf/bool을 물성이나
미상 null로 바꾸지 않으며 `model_copy`로 바꾼 객체도 실행 전에 재검증한다.

검토 항목은 다각형 공동과 동일 관축 단면, 명시 P1 노드장, 일정 물성과 온도
지원 범위, 경계 이력/시계/순서, 삼각화·적분·연산자 진단, 제외된 물리 범위다.
기본 검토 값은 미입력이다. 사용자의 검토가 양의 결합계수나 온도 범위 위반을
덮어 계산 성공으로 바꿀 수는 없다.

`RunContext`는 같은 asset_id/clock_id를 사용한다.
`metadata['soil_fem_boundary']`는 `boundary_metadata(request)`와 정확히 일치해야 한다.
이는 위치·사건·오프셋·관축 길이·좌표 평면·P1 노드장·다각형 공동의 의미를 묶는다.
계산 기원은 synthetic 또는 simulation이며 측정 온도로 다시 표시하지 않는다.

## 경계와 초기장

`SoilFEMBoundary`는 id, role(ground/pipe/far_field), evidence_id를 갖는다.
pipe에만 component_asset_id가 필요하다. 필요한 이력은 다음과 같다.

| kind | 입력과 공간 순서 |
|---|---|
| temperature | 명시 node_ids와 nodal_temperature_K; 그룹의 모든 경계 vertex를 정확히 포함 |
| heat_flux | 공통 그룹의 입력 face ID 순서대로 heat_flux_into_soil_W_m2 |
| convection | 같은 face 순서의 convection_W_m2_K와 ambient_temperature_K |
| adiabatic | 별도 이력 없음 |

각 이력은 time_s 행과 해당 노드 또는 면 순서의 열을 갖는다. null 경계값은
미상이다. 0열률과 h=0은 정상적인 명시 입력이며 임의 기본값으로 대체하지 않는다.
여러 Dirichlet 그룹이 한 노드를 공유하면 모든 시간의 온도가 같아야 한다.
서로 다른 값을 평균하거나 어느 그룹을 우선하지 않는다. transient의 초기
Dirichlet 노드도 첫 경계값과 같아야 한다. 초기 순간의 미정 열 충격을 만들지 않는다.

일반 셀 평균·센서 점 관측·면 평균으로부터 P1 노드장을 자동 생성하지 않는다.
별도의 검토된 투영·측정 연산자가 필요하면 입력을 구성하는 단계에서 명시해야 한다.
출력 `cell_mean_from_P1_K`는 이미 계산된 P1 장을 native 적분한 셀 평균이며
입력 셀 평균을 재현했다는 뜻이 아니다.

previous_knot_hold는 각 구간의 왼쪽 매듭을 그 구간 전체에 적용한다. 오른쪽
매듭에 놓인 결과 행도 방금 푼 구간의 경계값을 나타내므로 왼쪽 극한의 의미다.
다음 구간은 다음 단계부터 바뀐다. 이를 매듭 직후 경계 갱신의 순간값으로 해석하지 않는다.

## 삼각형·연산자·온도 비교 조건

공통 기하 검사는 실제 연결된 토양, 양의 면적 공동, 닫힌 경계, 겹침·교차·
매달린 꼭짓점·부분 중첩 면을 확인한다. local_delaunay는 각 볼록 셀 안에서
SciPy의 원 Delaunay를 호출한다. Gmsh나 외부 메시 도구는 필요하지 않다.
기존 노드를 옮기거나 추가하지 않으며 원 노드 ID·좌표·공동 면이 native 메시에도
그대로 남았는지 확인한다. 셀별 삼각형 면적 합도 원 셀과 같아야 한다.

꼭짓점 적분의 집중 질량과 Robin 적분은 native Basis/FacetBasis의 명시 수치 선택이다.
전도 강성은 native laplace 조립, 물성·관축 길이는 그 계수다. 이것만으로 어떤
삼각형에서도 단조성을 보장한다고 주장하지 않는다. 실제 시간 단계 연산자의
양의 비대각 항, 양의 대각, 대각 질량 여부를 확인한다. 수치 영점은 강성 크기에
대한1e-12 상대 수준이다. consistent 질량이나 부적절한 삼각형의 계산은 원 결과를
남기되 이 비교 원리가 확인되지 않으면 diagnostic_rejected이고 신호로 내보내지 않는다.

수동 경계에서는 이전 노드장과 선언 Dirichlet/Robin 온도로 상·하한을 잡는다.
Neumann 열원이 있으면 조립된 노드 열 부하와 노드 열용량의 비율로 단계당 허용
변화를 추가한다. 따라서 실제 양의 열 유입을 초기 최대 온도로 잘라내지 않는다.
정상 상태는 양의 연산자에서 부호별 열원과 경계 온도로 비교장을 native 풀이한다.
온도를 clipping하거나 실패 행을 삭제해 이 검사를 통과시키지 않는다.

## 열률·에너지·반력의 구분

모든 양의 열률은 토양으로 들어가는 방향이다. 2차원 면적/선 길이에
axial_extrusion_length_m를 한 번 곱한다. 강성·자연 경계·반력은 W,
용량은 J/K, 온도 변화에 따른 저장은 J다. native 질량 행렬의 저장 변화와
별도 P1 체적 적분의 저장 변화를 대조한다. 단계의 자연 경계 열과 Dirichlet
전체 반력은 저장 변화와 같아야 하며 자유 노드의 실제 조립 식 잔차도 검사한다.
허용오차의 상대 척도는 절대310K 같은 기준 온도가 아니라 실제 전도·자연 열·
저장 변화율이다.

`boundary_physical_gradient_power_into_soil_W`는 FacetBasis로 적분한 현재 P1
기울기·기하 법선의 열률이다. `boundary_weak_power_into_soil_W`는 자연 경계
부하 또는 Dirichlet 구속의 변분 반력이다. transient에서는 방금 푼 구간의
backward-Euler 열률이다. 둘은 일반 메시·해에서 동일하거나 같은 오차 차수의
보존량이라고 가정하지 않는다. 누적 에너지는 weak 열률을 사용한다.

서로 다른 Dirichlet 그룹이 모서리 노드를 공유하면 그 노드 반력을 한 면에
유일하게 배분할 수 없다. 해당 그룹의 weak 열률과 누적값은 None이다.
분할하지 않은 Dirichlet 노드 합과 전체 경계 열은 알려진 값으로 남는다.
공동처럼 다른 경계와 노드를 공유하지 않는 완전한 성분은 반력 합을 기록한다.
이는 특정 면의 국소 물리 열유속으로 배분했다는 뜻이 아니다. 선형 작성 예제의
ground 노드 반력 합24W는 측면 모서리 기여가 상쇄되는 그 조건의 검증이다.
일반 지표/외곽 경계에 그 값을 적용하지 않는다.

## 저장 검증과 신호

저장 기록은 요청/context·producer·삼각형·질량/강성·라이브러리 소스/버전을 묶는다.
scikit-fem Python 소스 전체, 원 라이선스, 공유 기하 코드, NumPy/SciPy 버전과
실제 Qhull/SuperLU 바이너리 해시를 기록한다. 검증기는 원 기록의 노드 온도를
현재 native 연산자에 다시 넣어 경계 열·반력·셀 평균·누적·비교 조건·식 잔차를
재구성한다. 바깥/안쪽 digest를 다시 계산한 의미 변조도 이 검사를 생략할 수 없다.

SignalFrame은 P1 노드 K, 별도 셀 평균 K, 기울기 끝점 W, 정상 weak W 또는
backward-Euler weak W, 누적 J와 알려진 전체 Dirichlet/경계 합을 구분한다.
transient 첫 행에는 앞 구간이 없으므로 backward-Euler 열률이 None/valid=False다.
모서리 분할이 미상인 열률도 None이며 0으로 만들지 않는다. 시간은 원 로컬 시계에
검토된 오프셋만 더한다. 정상 문제에는 시간 누적 에너지를 만들지 않는다.

원 풀이가 중단되면 완료한 행만 partial로 남긴다. 일반 내보내기는 이를 거부한다.
`allow_partial=True`를 명시해도 해당 접두 구간의 모든 물성·연산자·열/온도 조건을
통과해야 하며 operating_mode는 부분 접두 구간으로 표시한다. 실패한 연산자,
물성 지원 범위 또는 온도 비교 조건은 이 opt-in으로 우회할 수 없다.

## 검증 기록과 남은 연구

실제 두 공동의 선형 온도장300+2y를 전단0/.3/.75에서 실행해 기하 법선 열24W와
native 구속 반력을 비교했다. 기존12개 작은 탐색 사례와 세분화한 obtuse/fan
실패를 그대로 재생한다. consistent 질량의 수치 에너지 수지가 닫혀도 온도가
초기 최대를 초과할 수 있었고, 집중 질량만 바꾼 비직교 fan도 실패했다.
현재 경로는 Delaunay·적분 선택·실제 연산자/온도 검사를 함께 적용한 명시 사례다.

독립 연속 해는 모든 정수 좌표 공동 경계에서 사라지는 sin(πx)sin(πy)의 열 감쇠다.
메시2/4/8의 P1장 L2 오차를 비교한다. 별도 시간 기준은 native 일반화 고유값을
사용한 이산 고유모드의 스칼라 backward-Euler 감쇠이며 연속 공간 정답과 구분한다.
같은 자료나 모델이 만든 참조량을 독립 현장 검증으로 세지 않는다.
세부 사례·오차·실패·검사 수와 현재 파일 해시는 기계 판독 검증 기록을 따른다.

곡선 경계/고차 형상, 일반 외곽 모서리 반력의 배분 규칙, 셀·센서장 투영,
온도·수분 의존 물성, 관벽·유체와의 양방향 보존 연성, 독립 실험 교정은
후속 연구다. 이 조건을 해소하지 않고 다관 열 문제 전체나 현장 성능의 완료로
집계하지 않는다. 원곡선의 제어점·곡률·요소 Jacobian과 메시/시간 세분화 근거를
후속 계약에 추가해야 하며 임의 다각형을 원관으로 바꾸어 완성한 것으로 표시하지 않는다.
