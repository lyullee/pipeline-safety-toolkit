# 방출에서 조건부 사고 결과와 연간 환산으로

`consequence_exchange`는 기존 PRISM·SLABx의 계산을 호출한다. 장치 방출,
압축성 평면, 확산, 복사·열량, 점화 분기, 폭발 구역과 인체 위해도의 소유
엔진을 복제하지 않는다. 실제 호출 증거는 `research/consequence_results.json`,
전체 요청·응답·실패는 로컬 `artifacts/consequences`에 보관한다.

현재 결과의 범위는 명시한 시간창의 조건부 진단 계산이다. 이를 실제 사고의
전체 위해도나 사업장 전체 위험으로 사용하지 않는다. 원 방출의 진단 종료가
실제 누설 종료·차단 성공·재고 소진을 뜻하지 않기 때문이다.

## 소스 준비와 재현

외부 구현은 별도로 설치하거나 다음 고정 소스를 준비한다. 취득 버전과 실제
실행한 Python 소스 해시를 구분한다. 기존 분석에서 확보했던 SLABx 두 저장소의
메타데이터에 이어 실제 소스를 같은 커밋으로 취득했다. 이 소스는 이 패키지의
배포 경계 밖에 둔다.

```powershell
git clone https://github.com/lyullee/slabx.git PATH_TO_SLABX
git -C PATH_TO_SLABX checkout --detach 820e368ba6454aad8627dac5967115d9648c80c7
git clone https://github.com/lyullee/slabx-lh2.git PATH_TO_SLABX_LH2
git -C PATH_TO_SLABX_LH2 checkout --detach 1ef613102819de1c2feebd062aff3b49997e1927
python scripts/check_consequences.py --lh2dt PATH_TO_LH2DT --prism-source PATH_TO_PRISM/lh2-tno/src --slabx PATH_TO_SLABX --slabx-lh2 PATH_TO_SLABX_LH2
```

PRISM과 lh2dt_models의 고정 취득 버전은 external_source_versions를 따른다.
실행기는 PRISM QRA, 내장 수소 경계 모형, SLABx, SLABx-LH2의 소스를 각각
해시로 고정하고 NumPy·SciPy·CoolProp·Pydantic 버전도 원 워커에서 검사한다.
소스 변경·의존성 불일치·원 엔진 오류를 다른 모형으로 대체하지 않는다.
`check_consequences`는 단계별 파일과 상태를 먼저 남겨 중간 실패도 보존한다.

## Python API

```python
from pipeline_integrity.consequence_exchange import (
    ConsequenceCase,
    CompressiblePlane,
    PRISMConsequenceClient,
)

from lh2_tno.backend import IsolatedBackend
from lh2_tno.public_backend import installed_backend_config

# 원 모듈들이 설치된 환경에서는 기존 공개 검색 API로 소스를 고정한다.
backend = IsolatedBackend(
    installed_backend_config(
        packages=("prism_qra", "prism_lh2_models", "slabx", "slabx_lh2"),
        require_operations=("consequence_chain",),
        timeout_s=60.0,
    )
)
# handoff는 prism_release(history)가 반환한 검사가 끝난 ReleaseHandoff다.
client = PRISMConsequenceClient(backend)
schemas = client.schemas()
case = ConsequenceCase(
    id="reviewed_case_input",
    initiating_condition_id="release_initiated",
    facility=facility_json,
    defaults=consequence_defaults,
    evidence_ids=("reviewed:geometry_weather_ignition_and_time_window",),
    compressible_plane=CompressiblePlane(evidence_id="declared:source_state_expansion"),
)
result = client.evaluate(handoff, case)
result.check_integrity()
```

원 모듈을 설치하지 않고 로컬 소스를 명시하는 방식은 재현 스크립트가 보여준다.
해시는 변경 검사이며 외부 승인·서명이나 입력 근거의 진실성을 입증하지 않는다.

`schemas()`는 원 Facility·ConsequenceRequest와 이쪽 입력·연간 집계의 JSON
스키마를 반환한다. facility는 SI의 자산·출구·수용체 ENU 좌표와 물성 기준을
갖는다. defaults에는 기상, 점화, 공간 격자, 평가 시각, 복사율 범위, 종료
근거와 원 엔진의 수치 설정을 넣는다. 실제 필드 전체는 재현 스크립트를 본다.
파라미터·단위·가정과 검토 근거를 사용자가 교체할 수 있다.

defaults로 원 방출·재고·위치나 사건 빈도를 덮어쓸 수 없다. 조건부 위해도는
사람이 그 위치에 있다는 조건이므로 occupancy는 1이다. 연간 재실 확률은
뒤의 집계에서 한 번만 적용한다. 합성 소스를 검토·현장 확인된 설비로
승격하지 않는다. 셀 수, 셀×평가 시각 수, 수용체 수의 예산도 선언한다.

공간 범위는 기상 풍하·횡풍 좌표와 절대 높이다. 원점에서의 상대 거리로
자동 이동하지 않는다. ENU 수용체를 같은 풍향 좌표로 투영해 범위 밖 입력을
거부한다. 반응계가 요구하는 시각은 원 출구 이력과 결합하되, 실제 문맥의
시작 오프셋은 source_record에 별도로 유지한다.

## 압축성 출구를 처리하는 방법

기존 일반 수소 예제의 기하학적 구멍 면적과 대기압 밀도에서 계산된 속도는
약 6,027 m/s였다. 원 기체 패킷의 구조·질량 장부를 통과했어도 PRISM의 직접
저마하 유입 조건을 만족하지 않는다. 원 엔진이 이 입력을 거부한 응답을
보관했다. 이력의 exchange_checked를 전체 후속 물리의 유효성으로 해석하지 않는다.

명시적으로 압축성 평면을 요청하면 기존
`lh2_tno.compressible_source_adapter.build_compressible_plane_handoff`와
`attach_compressible_plane_handoff`를 호출한다. 원 장치가 내보낸 상류·목
상태와 같은 유체의 물성 공급자를 요구한다. 출구 속도만으로 압력을 추정하거나
장치 질량률을 다시 계산하지 않는다. 원 패킷에서 추가 평면 외의 값이 달라지면
거부한다. 생성·부착 감사와 원 결과 해시를 함께 보존한다.

사용한 Birch 운동량·에너지 경계의 평면 에너지 잔차를 검사한다. 이는 전체
소스부터 공기 혼합·확산까지의 에너지 보존이나 독립적인 근접장 검증이 아니다.
이 경계는 synthetic_diagnostic이며, 엔트로피 보존이나 실제 설비 적합성을
추가로 주장하지 않는다. 액체·2상·다른 물성의 기존 보류 문제는 그대로 남는다.

## 사고 결과 수용 조건

원 엔진의 실제 실행 여부, 원 방출의 정확한 동일성, 장치·유출 계산 책임,
누적 방출량과 소스부터 구름까지의 질량 장부를 확인한다. 수용체 ID·좌표,
농도 시각·범위, 점화 분할·확률, 위해도 범위와 원 한정 상태를 유지한다.
조건부 위해도에 연간 빈도나 재실 확률이 미리 곱해졌으면 보류한다. 영역
밖 가연 구름의 미계산분이 남는 원 판정도 보류한다.

재현 예제는 1 MPa·300 K의 일반 수소, 길이 1 m·내경 0.05 m의 유한 관과
1 mm 실패 개구를 사용한다. 1초 방출 이력의 20·40구간을 비교하고 3초까지
조건부 위해도를 계산한다. 실제 문맥의 방출 시작은 10초이므로 출력 시각과
원 문맥을 연결할 때 오프셋을 더한다. 복사율 0.04~0.08, 점화 확률과 노출·
빈도는 직접 작성한 민감도 가정이며 현장 보정값이 아니다.

현재 모델은 실외의 바람 조건과 유한 체적의 구름 재구성을 사용한다. 점화
전 구름 계산과 화재 간 열 피드백, 중첩 공기의 에너지 평형, 짧은 방출의
화염 형성은 미검증 범위다. 폭발은 제한된 폭연 구역 모형이며 연속
압력·충격량 곡선이나 DDT를 검증한 것이 아니다. 한 수용체의 0 위해도도
그 장소가 실제로 안전하다는 증거가 아니다.

## 연간 환산과 미계산 조건

`AnnualRiskRequest`는 사건별 `AnnualEvent`, 연간 조건별 `AnnualCondition`을
받는다. 같은 사건의 조건은 합이 1이어야 하며 같은 원 방출 패킷을 사용한다.
전체 수용체·설비 기하학을 유지하고, 빈도 ID·조건·문맥을 결과와 맞춘다.
빈도의 endpoint는 loss_of_containment여야 한다. HRA 요구 시 실패 횟수를
직접 누설 빈도로 바꾸지 않는다. 사건 분할·조건 독립성·재실·시간 범위의
근거를 요구하며 같은 점화·방호를 추가로 곱하는 입력은 받지 않는다.

집계는 원 PRISM의 AnnualScenario와 annual_risk를 호출한다. native 결과를
보존하고, 외부 지표는 annualized_declared_window_harm_rate로 표시한다.
이는 명시한 시간창의 조건부 위해도에 연간 빈도를 적용한 값이다. 실제 사고
전체 또는 사업장 전체 위험으로 이름을 바꾸지 않는다. 선택한 사례 이외의
사건, 이후 누출·재고·구름의 영향은 이 값에 들어 있지 않다.

빈도가 없으면 needs_frequency이며 완료값과 유한 상한은 null이다. 계산하지
못한 기상 조건이 있으면 needs_consequence다. 해당 조건을 삭제하거나 나머지
가중치를 재정규화하지 않는다. 원 annual_risk를 다음 네 입력으로 호출한다.

1. 알려진 모형의 낮은 민감도와 미계산 위해확률 0.
2. 알려진 모형의 낮은 민감도와 미계산 위해확률 1.
3. 알려진 모형의 높은 민감도와 미계산 위해확률 0.
4. 알려진 모형의 높은 민감도와 미계산 위해확률 1.

0과 1은 미계산 확률의 명시적인 수학적 범위를 만들기 위한 값이다. 완료값을
0으로 채우지 않는다. value_low_high는 null, known_contribution_low_high는
계산된 조건의 기여, uncomputed_harm_envelope_low_high는 미계산분까지 포함한
범위다. 빈도도 없으면 이 범위도 null이다. native 결과는 이 가정과 함께
native_results에 남는다. 이 구간은 통계 신뢰구간이 아니다.

## 발견한 소유 엔진의 경계와 확장 요청

동쪽 방향 평면과 바람이 정렬된 270도 입력은 실행됐다. 바람 180도에서는
양의 풍하 속도가 없어 보류된다. 250도에서는 평면 속도를 풍하 방향으로
투영하면서 기하학적 단면이 유지되어 약 6.031%의 질량 불일치가 발생한다.
원 계산의 엄격한 수지 검사를 완화하거나 유량·방향·폭을 임의로 수정하지 않는다.
투영에 따른 면적·속도·질량 보존과 횡풍 근접장 처리는 PI-X03 요청으로 남긴다.

세 기상 조건의 가중치 0.6·0.2·0.2를 보존한 집계가 미계산 상태와 범위를
반환하는지 실제 확인한다. 별도 정렬 조건 하나는 수치 대조용 기준이다.
실제 기상 분포나 빈도가 확보됐다는 뜻은 아니다. 상세 수치는 공개 실행
요약을 따른다. 코드 해시는 현재 모듈과 맞추고 원 응답·상세 시계·물성·근거는
로컬 파일 해시로 추적한다.

남은 일은 독립 실험 비교, 실제 차단·자연 종료와 잔류 재고, 완전한 구름
꼬리, 받는 쪽의 미계산분·원 계보 수용, 실제 기상·노출·빈도 자료다. 인원별
한계 위해확률만으로 사망자 수의 공동 분포나 F-N 곡선을 만들지 않는다.
