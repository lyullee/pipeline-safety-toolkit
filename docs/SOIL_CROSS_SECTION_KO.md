# 시간에 따른 토양 단면 열과 여러 배관 경계

`soil_cross_section`은 실제 FiPy 4.0.3의 `Mesh2D`, `TransientTerm`,
`DiffusionTerm`, `ImplicitSourceTerm`과 SciPy `LinearLUSolver`를 호출한다.
토양 제어체적, 수평 지표, 실제 빈 공간으로 이루어진 여러 배관 경계를 한
단면에서 계산한다. 같은 영역의 온도장이 배관 경계 사이의 열 교환에 영향을 준다.
재료 엔진의 기존 1차원 원통 열전도기를 복제하지 않는다.

현재 자격 범위는 **직교 메시, 다각형 공동, 일정한 토양 열전도도·체적 열용량**이다.
입력한 다각형은 그 형상 자체다. 예제의 두 정사각형 공동은 원관으로 해석하지 않는다.
비직교·곡면 근사 메시, 실제 관벽과 유체의 동시 에너지 계산, 토양 수분 이동·
지하수 이류·동결·상변화·물성 역추정 및 현장 정확도 검증은 미완료다.
배관 기술 전체의 다관 연성 완료로 이 산출물을 집계하지 않는다.

## 설치와 재현

선택 의존성이므로 기본 설치는 FiPy를 요구하지 않는다. 미검토·미상 요청은
FiPy를 import하지 않고 `needs_data`로 반환한다. 검토한 계산에 선택 라이브러리가
없으면 실제 ImportError를 전달하며 성공한 계산처럼 표시하지 않는다.

```powershell
python -m pip install -c requirements-tested.txt ".[soil,dev]"
python examples/soil_cross_section.py
python scripts/check_soil_cross_section.py
python -m pytest tests/test_soil_cross_section.py -q
python -m ruff check src/pipeline_integrity/soil_cross_section.py examples/soil_cross_section.py scripts/check_soil_cross_section.py tests/test_soil_cross_section.py
```

확인한 환경은 Python3.12.14, FiPy4.0.3, NumPy2.5.4, SciPy1.18.1,
Matplotlib3.11.2다. 기존 계산 의존성을 바꾸지 않고 FiPy439,565바이트 wheel과
Typer0.27.3, Shellingham1.5.4, Rich15.0.0, markdown-it-py4.2.0, mdurl0.1.2의
작은 순수 Python wheel만 추가했다. 실행·패키지 버전과 원 소스 해시는
[기계 판독 검증 기록](../research/soil_cross_section_results.json)에 남긴다.
FiPy의 NumPy 내부 이름 사용에 따른 DeprecationWarning도 검증에서 숨기지 않는다.

## 입력과 검토

`SoilCrossSectionMesh`는 미터 좌표 `vertices_m`, 두 꼭짓점 ID의 `face_vertex_ids`,
각 셀을 둘러싼 면 ID의 `cell_face_ids`, 이름별 `boundary_face_groups`를 받는다.
좌표 평면은 x가 수평, y가 위쪽이다. `ground_surface_y_m`보다 위에 토양 셀을
둘 수 없다. `axial_extrusion_length_m`는 단면이 동일하게 이어지는 관축 길이다.
2차원 셀 면적과 선분 길이에 이 길이를 **한 번** 곱해 체적·열률·에너지를 구한다.

검사는 꼭짓점·면 ID, 중복·교차·겹침·매달린 꼭짓점, 양의 면적을 가진 볼록 셀,
토양 영역의 연결, 공유 면의 실제 소유 셀, 경계의 닫힌 고리와 면적을 확인한다.
모든 실제 외부 면은 이름 있는 경계에 정확히 한 번 들어가야 한다. `pipe` 그룹은
영역 내부의 양의 면적 공동 고리 전체여야 한다. 임의 내부 셀의 온도 고정이나
면적0인 선형 틈을 배관 공동으로 인정하지 않는다. `ground`는 선언한 수평 지표의
외곽 경계이며 `far_field`는 나머지 외곽의 검토된 유한 경계다.

`maximum_reviewed_nonorthogonality_deg`는 사용자가 검토한 상한이다. 이 값과
별도로 실제 지원 상한 **1e-6도**를 적용한다. 사용자 검토나 허용값 증가로
현재 미검증 비직교 계산을 실행할 수 없다. 독립 좌표 검사와 실제 FiPy 셀·면
기하에서도 이 조건을 확인한다. 기울어진 메시를 직교 메시처럼 바꾸거나 재배치하지 않는다.

`SoilCrossSectionBoundary`는 경계 ID·`role`·근거 ID와 다음 중 하나를 받는다.
`pipe`에는 실제 연계 대상의 `component_asset_id`도 필요하다.

| `kind` | 필요한 이력 | 부호와 의미 |
|---|---|---|
| `temperature` | `temperature_K` | 공동 또는 지표의 지정 절대온도 |
| `heat_flux` | `heat_flux_into_soil_W_m2` | 양수는 토양으로 들어가는 열 |
| `convection` | `convection_W_m2_K`, `ambient_temperature_K` | 검토한 경계 열전달계수와 외부 저장소 온도 |
| `adiabatic` | 없음 | 경계를 통과하는 열0 |

각 이력은 `time_s`의 행과 그 그룹의 **입력 면 ID 순서**의 열로 이루어진다.
단위가 다른 열을 바꾸거나 누락 값을 0으로 넣지 않는다. 같은 값을 모든 면에
쓸 때도 해당 열 수만큼 명시한다. 이 방식으로 공간에 따라 다른 지표·공동 온도도
지정할 수 있다. 서로 다른 경계의 시간 매핑을 알아서 추측하지 않는다.

`SoilCrossSectionRequest`는 자산·위치·시계·사건·오프셋, 메시, 시간 매듭·최대
단계, 초기 셀 온도, 일정한 `conductivity_W_m_K`와
`volumetric_heat_capacity_J_m3_K`, 검토한 `temperature_support_K`, 경계,
단위 선언과 근거를 받는다. `None`인 물성·초기값·경계값은 `needs_data`이며
NaN/무한대는 invalid 입력으로 거부한다. `model_copy`로 바꾼 객체도 직렬화 전에
다시 검사하므로 NaN을 null로 바꾸어 자료 부족처럼 보이게 하지 않는다.

6개 `REVIEWS`와 해당 검토 근거 ID를 각각 제공해야 한다. 형상, 관축 방향의
동일 단면, 물성과 온도 범위, 지정 경계 이력, 수분·이류·동결·유체 연성 제외,
메시 품질과 유한 외곽 경계가 검토 대상이다. 검토자 선언은 실험·설비 자격의
증명이 아니다. 모든 재료·형상·초기·경계·검토 근거 ID는 선택한 `evidence_ids`와
공통 `RunContext.evidence`에 실제로 존재해야 한다.

`RunContext` 자산·시계는 요청과 일치해야 한다.
`metadata.soil_cross_section_boundary`는 `boundary_metadata(request)`와 정확히 같아야 한다.
여기에는 위치·사건·시간 오프셋·압출 길이·좌표 평면과
`declared_polygon_cavity_soil_surface`라는 경계 의미가 포함된다.

```python
from examples.soil_cross_section import make_case
from pipeline_integrity.soil_cross_section import (
    evaluate_soil_cross_section,
    verify_soil_cross_section_record,
)

request, context = make_case()  # 작성한 형상 예제이며 검토 기본값은 False
record = evaluate_soil_cross_section(request, context)
assert record["status"] == "needs_data"
assert record["native_called"] is False
assert verify_soil_cross_section_record(record, request, context)["valid"]
```

예제와 검증 guide의 `reviewed=True`는 작성한 수치 기준의 검토 선언이다.
새 실제 설비의 검토나 물성을 자동 승인하는 함수로 사용하지 않는다.

## 시간·열 부호와 장부

시간은 로컬0부터 증가한다. `maximum_step_s`에 맞춰 각 매듭 구간을 나누며
경계의 시간 의미는 `history_interpolation`으로 선택한다.
`linear`는 매듭 사이 선형 보간이다. `previous_knot_hold`는 해당 구간의 시작
값을 유지한다. 이 경우 오른쪽 매듭에 저장한 경계 열·표면 온도는 방금 완료한
구간의 **왼쪽 극한으로 적용한 값**이며, 매듭 직후의 새 명령 스냅샷이 아니다.
다음 단계부터 새 값이 적용된다. 중복 시간으로 불연속을 숨기지 않는다.

FiPy가 암시적 후진 Euler 방정식을 조립하고 푼다. 경계 열은 토양으로 들어갈
때 양수다. 공동 경계에서 양의 열은 지정한 배관 표면 저장소가 토양에 공급하는
열이며, 유한한 관벽·유체의 같은 양 손실을 별도로 계산했다는 뜻이 아니다.
대류 경계는 실제 면의 열전달 저항과 셀 중심까지의 전도를 함께 사용하며,
방사나 접촉 저항을 임의로 더하지 않는다.

매 단계의 셀 저장량 변화 `Cv × cell_area × extrusion_length × ΔT`와 모든
경계 유입열의 합을 대조한다. 유입 에너지는 실제 적용한 단계 끝 열률에 단계
시간을 곱한 후진 Euler 장부다. 출력 매듭만 사다리꼴 적분하여 다른 장부를
만들지 않는다. 절대 허용값 기본1e-6J와 상대 허용값 기본1e-8을 요청에 기록한다.
상대 스케일은 총 경계 유입·유출 에너지 크기와 셀 저장 변화에 근거하므로
양·음열의 상쇄가 큰 오차를 숨기지 않는다. 각 셀의 암시적 방정식 잔차도
실제 FiPy `justResidualVector`에서 얻어 시간·압출 길이를 곱한 J로 확인한다.

전체 장부·셀 방정식·모든 셀과 경계 표면의 온도 범위 중 하나라도 실패하면
하류 신호를 보류한다. 원 계산이 끝났어도 이쪽 상태는 `out_of_scope`다.
중간 solve가 실패하면 이미 반환한 정상 prefix만 `partial`로 남긴다. 내부에서
실패한 단계의 온도나 에너지를 만들어 채우지 않는다. 수치 단계·기록 크기는
최대2000단계와 200000개 셀/경계 시간 값으로 제한한다.

## 저장 검증과 Python 연결

`verify_soil_cross_section_record(record, request=None, context=None)`는 요청,
근거·시계·단위, 현재 adapter와 FiPy Python 소스 전체·실제 클래스 파일 해시를
확인한다. 계산 기록은 현재 FiPy로 메시를 다시 만들고 저장한 셀 상태에서 경계
기울기·대류·표면 값과 각 단계의 암시적 방정식 잔차를 다시 계산한다. 이 검사는
시간 적분을 다시 수행하거나 원 기록을 수정하지 않는다. 총 에너지만 일치시키며
지표와 외곽의 열을 같은 크기로 조작하거나 내부 셀 온도를 보존적으로 재분배한
기록도 거부한다. 해시는 서명이 아니므로 과거 실행·검토자 승인 사실이나
실제 관측의 진위를 증명하지 않는다.

`to_signal_frames(record, context, allow_partial=False)`는 검증된 기록의 셀 온도K,
이름별 `boundary.<id>.backward_euler_power_into_soil_W`와
`boundary.<id>.cumulative_energy_into_soil_J`를 공통 `SignalFrame`에 넣는다.
각 시각은 `local_time + clock_offset_s`, stream은 `asset_id:event_id`다.
첫 열률은 적분 구간이 없으므로 None/valid=False이며 누적 에너지는0이다.
후속 열률은 완료 구간의 후진 Euler 적용 열률로, 연속 시간의 평균·피크·매듭
직후 순간 열률과 자동으로 같다고 하지 않는다. context가 synthetic이면
출력도 synthetic, 나머지 원 계산은 simulation이다. prefix는 명시적인
`allow_partial=True`와 `soil-native-partial-prefix` 표시가 있어야 전달한다.

## 검증과 남은 기하 문제

검증 guide는 실제 공동 둘을 가진22셀 이상의 토양 메시를 호출한다. 균일300K
불변, 2차원에 넣은 선형 온도장의24W 지표 열, 양·음 지정열과 압출 길이 배율,
대류 냉각, 배관 경계 사이의 실제 열 전달·상반성·중첩을 비교한다.
공간 세분은 시간에 따라 감소하는 해석적 온도 파형의 **셀 체적 평균**과
대조한다. 별도 시간 세분은 같은 공간 연산자의 더 작은 단계 결과와 비교하며
이를 독립 해석 해나 실측 정확도라고 세지 않는다.

집중 pytest48개를 통과했다. guide는23요청 중18계산·2자료 부족·3범위 밖을
보존하고 수치·자료 계약 기준105개를 확인했다. 별도 직접 라이브러리6실험은
비직교의 실패 기법 조사이며 자격 있는 계산으로 세지 않는다. 독립 검토14개
사례에서도 조작된 열률·표면 온도·보존적 셀 온도 변경, 비유한 복제 입력,
가짜 공동과 비직교 실행의 거부를 확인했다. 모든105개 기준이 독립 수치
정답인 것은 아니며 실제 실험 자료의 독립 검증도 아니다.

독립 검토에서 기본 FiPy `DiffusionTerm`이 `DiffusionTermNoCorrection`을
사용함을 확인했다. 선형장에 전단0.75를 준 직접 기준은 실제 지표 법선 열24W인데
기본 face gradient는19.2W를 보였다. 실제 `DiffusionTermCorrection`과 반복
sweep의 독립 시험도 약19.1874W였다. 셀 Gauss gradient로 출력만 바꾸면 별도
에너지 장부와 일치하지 않을 수 있어 채택하지 않았다. 별도 혼합 경계 조립의
Correction 시도는 `MeshVariable.max(out=...)` TypeError도 발생했다. 이 오류는
해당 조립 시도의 관찰이며 모든 FiPy Correction 계산의 실패라는 뜻이 아니다.
원 FiPy 코드를 고치거나 경고·허용값을 바꾸어 지원 범위를 넓히지 않았다.

후속 확장에는 검토한 비직교 내부 확산·접선 오프셋을 포함한 실제 법선 경계
열·대류·반복 수렴·국소/전체 장부를 함께 제공하는 경로가 필요하다. 선형 전단장,
곡면의 메시 세분, 여러 공동의 상반성, 가열/냉각과 시간 세분을 같은 기준에서
통과해야 한다. 원형 공동 근사 오차·원거리 경계 영향·토양 수분과 재료 변화를
현장 관측으로 검증하는 일도 남아 있다.

## 이용 조건과 출처

[FiPy 공식 사용 문서](https://pages.nist.gov/fipy/en/stable/USAGE.html)와
[FiPy 4.0.3 이용 조건](https://pages.nist.gov/fipy/en/4.0.3/LICENSE.html)을 확인했다.
NIST 조건은 해당 고지와 보증 부인의 보존을 요구하며 국외 저작권 가능성도
명시한다. 포함된 `fipy/tools/dimensions/LICENSE`의 Konrad Hinsen 고지와 허용
조건도 별개로 남아 있다. Typer의 MIT 및 포함 Click의 BSD3 고지, 나머지
의존성의 라이선스를 함께 기록했다. 이 저장소 배포에는 FiPy나 그 의존성의
코드·바이너리를 넣지 않으며 설치한 라이브러리의 고지를 제거하지 않는다.
설명·예제·검증 조건은 이 프로젝트에서 작성했다.
