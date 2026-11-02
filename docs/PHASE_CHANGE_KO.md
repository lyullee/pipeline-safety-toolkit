# 기존 상변화·극저온 열 모델의 교환

`phase_change_exchange.evaluate_phase_change(request, context)`는 LH2DT의 계면
질량 전달·가스 열막, 정상 증발기/재액화기, 유한 유체/벽 열용량의 한 단계,
분해된 진공 단열 열유입을 실제 호출한다. 새 상관식이나 열 solver를 복제하지 않는다.
Hydrogen/ParaHydrogen HEOS의 유체 상태와 검토된 경계/근거를 유지한다.
벌크 동결/고체 EOS·응고 전선과 frost 침착/잠열 연성은 지원 밖이다. 전체 상변화
항목은 부분 구현이며 각 계산의 성공을 그 전체의 완료로 표시하지 않는다.

## 요청의 구분

`PhaseChangeRequest.case`는 다음 중 하나의 명시한 signature다.

| 종류 | 필요한 경계 | 결과의 뜻 |
|---|---|---|
| kinetic_interface | 면적·계면 온도·순수 증기 상태·두 accommodation·검토된 근평형 영역 | 액체→증기 signed 질량 유속과 조건부 열 요구 |
| steady_vaporizer | 입구/출구 압력·유량·UA·열원/접근온도·용량 | 정상 엔탈피 증가와 실제 출구 상 |
| steady_reliquefier | 입구/출구 압력·양의 유량·냉각 용량·목표 기상 질량분율 | 용량 제한 엔탈피 감소와 실제 도달 상 |
| dynamic_vaporizer / dynamic_reliquefier | 유체 체적/초기 상태·벽 열용량/온도·UA·외부 경계·유입/유출·검토된 단일 step | 원 RK4 상태와 endpoint 미분/수지 |
| vacuum_insulation | 면적·유효 방사율·MLI/지지/가스/국소 열경로·두 온도·진공압/적용영역 | 지정한 경계 사이의 순간 signed 열률 |

시간·기하·순수 유체/공통 물성 기준·closure 적용성·열 경계와 의미를 검토하고
근거를 선택한다. 모든 포트·자산·사건·시계는 `boundary_metadata`로 문맥에 결속한다.
정상/계면 진단에 시각이 미상이면 신호/유동 경계의 시간을 만들지 않는다.

계면 온도는 입력값이다. native Schrage 유속이 요구하는 잠열은 가용 열 공급이
아니며 계면 에너지 방정식을 풀었다는 뜻도 아니다. 액체측/외부 열이 없으면
잔차는 None이다. 두 공급 열을 모두 선언했을 때만 잔차를 검사한다. 음의 질량
전달은 응축 방향이며 양수로 자르거나 누적 질량으로 바꾸지 않는다.

정상 장치는 ṁΔh와 원 열률을 비교한다. 동적 장치의 derivative는 endpoint 값으로
보존한다. 그 값×step을 RK4 평균 열량으로 쓰지 않는다. 닫힌 단열 사례의 유체/
벽 에너지 합과 질량 증분은 확인하지만, 열린 사례의 적분 열/유입·유출 엔탈피
원장이 없으면 누적값은 None이다. 원 미분 수지0은 전체 step 에너지 증명이 아니다.

## 소스·물성·수출 경계

`native_backend_metadata`는 실제 원 Python tree/라이선스, 런타임과 CoolProp
바이너리/기준을 고정한다. 공유 uncached H/S 표식으로 기준 변경을 검출하며
P/Q·T/Q 등 보조 flash도 `CoolPropProvider(use_cache=False)`로 현재 기준을 읽는다.
이 옵션은 기존 property 계산을 재사용하며 전역 cache를 지우거나 owner를 수정하지 않는다.

`verify_phase_change_record`는 요청·문맥·원 계산·상/예산·현재 버전과 자격을 재생한다.
출력 해시를 다시 만들어도 잘못된 열률·기준·상태·출처·자격을 통과시키지 않는다.
입력의 진실성이나 과거 실행의 전자서명을 대신하지 않는다.

```python
from examples.phase_change_exchange import make_case
from pipeline_integrity.phase_change_exchange import evaluate_phase_change

request, context = make_case()  # 미검토 기본값, native 계산 없음
record = evaluate_phase_change(request, context)
assert record["status"] == "needs_data"
```

`to_signal_frames`는 실제 적용시각과 단위를 보존하고 None 채널은 invalid로 둔다.
`to_flow_boundaries`는 검증된 정상/동적 장치의 두 포트만 제공한다. 계면 closure나
단열 열률을 장치 유량으로 둔갑시키지 않는다. 두 포트는 같은 유체·런타임 H/S
기준을 사용하며 동적 경계는 원 step 종료 시각에 귀속된다.

## 재현과 후속

```powershell
python -m pytest tests/test_phase_change_exchange.py tests/test_property_reference.py -q
python scripts/check_thermal_equipment.py --owner PATH_TO_LH2DT_MODELS --only phase
```

signed 증발/응축, 별도 스칼라 기준, 공급 열/미상, 정상 엔탈피 수지, 단열의 병렬
열경로/동온 극한, 닫힌 동적 수지와 열린 적분 공백, 포트/시계/상·근거·변조를 확인한다.
[실행 집계](../research/phase_change_results.json)와 원 소스 핀을 따른다. 상세 기록은
배포 밖 `artifacts/phase_change`다. 작성한 조건이며 독립 실험 정확도의 검증은 아니다.

벌크 고화/용융의 고체 물성·잠열/전선·경계 및 원 공정 적분 에너지 원장,
frost의 공기/포화 영역·증착/융해 질량/잠열·clipping 진단, 독립 실험을 후속 요청한다.
계수·표·규격 문장을 가져오지 않고 검토된 물성/closure 공급자와 원 엔진을 확장한다.
