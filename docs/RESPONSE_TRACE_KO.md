# 경보·인적 대응·차단·공정 결과 연결

`response_trace.trace_response`는 서로 다른 네 결과를 공통 시간 좌표에서
연결한다. 관측한 경보, 작업자에게 사용 가능한 단서, 실제 명령, 밸브 목표
위치 도달과 공정 관측을 각각 보존한다. 하나의 성공 궤적이나 경보 횟수로
인적오류 확률·누설 빈도를 추정하지 않는다.

## 계산 소유와 현재 범위

- 경보: `process_monitoring.events.AlarmPolicy`가 임계값·지속·회복·래치를 계산한다.
- 인적 반응: `HumanResponseClient`가 기존 HRA의 시간·인지·복구 모델을 호출한다.
- 밸브: `simulate_valve_motion`이 기존 장치 엔진의 유한 스트로크를 호출한다.
- 공정: 필요한 관망/상태 모델이나 실제 센서가 독립 관측을 공급한다.
- 이 연결: 명시한 자산·시계·단서 지연·명령 기한·공정 한계의 관계를 확인한다.

원 상태기계·시간분포·밸브 운동·FTA를 새로 구현하지 않았다. 현재 구현은
결정한 시나리오의 시간 관계와 조건부 모델 연결이다. 확률적 공정 기한,
기한 내 복구·경쟁 고장·시간 의존 결합과 전체 동적 위험도는 후속 범위다.
`dynamic_risk_probability`는 현재 항상 null이다.

## 경보 입력과 관측 전이

`AlarmReplaySpec`은 alarm_id, source_stream_id, score_channel, score_unit,
trigger, reset, persistence_s, recovery_s, 양의 max_gap_s, latch와 근거를 받는다.
`policy_reviewed` 기본값은 false다. 검토 전에는 원 라이브러리를 가져오지 않고
needs_data를 반환한다. 임계값은 호출자가 선언한 score_unit의 수치이며 스코어를
확률로 해석하지 않는다. reset은 명시한 원 sample 시각과 근거가 함께 있어야 한다.

`replay_alarm(frames, specification, context)`는 유효한 `SignalFrame`을 순서대로
원 엔진에 보낸다. 시계·stream·단위·origin·최대 행 수를 확인한다. 관측을 정렬,
재표본화하거나 결측을 0으로 바꾸지 않는다. 원 엔진의 UNKNOWN은 active 기억과
함께 남는다. 긴 간격이 지속/회복 timer를 끊는 원 동작도 유지한다.

activation_records는 inactive에서 유효한 ALARM이 처음 기록된 전이다. 실제
물리 사건의 발생 시각이나 표본 사이의 정확한 경보 onset을 추정한 값이 아니다.
UNKNOWN 뒤 ALARM이나 활성 상태의 긴 공백을 새 경보 발생으로 중복 세지 않는다.

`AlarmReplayResult.check_integrity()`는 결과·정책·맥락·provider와 원 frame의
결측/단위/값 digest를 확인한다. 공개 JSON에는 원 frame을 넣지 않으므로 JSON만
다시 읽어 원자료 검증을 주장할 수 없다. 같은 원자료와 정책으로 replay를
재실행하거나 원 snapshot을 보존한 deep model_copy를 사용한다.

공개 공정 적용은 [ALARM_SOURCE_KO.md](ALARM_SOURCE_KO.md)를 따른다. 그 자료에는
작업자 반응·실제 경보 전달 시각·확인한 누설 정답이 없어 성능/HEP 보정에 쓰지 않았다.

## 사건 매핑 API

`ResponseTraceSpec`의 주요 필드는 다음과 같다.

| 입력 | 의미 |
|---|---|
| alarm_activation_id | 원 replay에서 선택한 정확한 관측 전이 ID |
| alarm_asset_id, valve_component_id | 서로 다른 원 자산의 명시적 매핑 |
| command_id, protection_id | 실제 원 명령과 평가 보호 효과의 식별자 |
| condition_id, opportunity_id | 인적 모델을 연결할 동일 조건·기회 |
| alarm_to_cue_delay_s | 기록 경보에서 사용 가능한 단서까지의 지연; 미상 허용 |
| command_deadline_s | 올바른 명령이 발행돼야 하는 절대 경과 시각 |
| process_limit_time_s | 별도 공정 endpoint의 평가 한계 시각 |
| maximum_motion_sample_interval_s | 완료 bracket을 신뢰할 샘플 간격 상한 |
| mapping_evidence_ids | 맥락에 실제 포함한 시계·자산·원점·기한의 근거 |

asset_clock_and_origin_mapping_reviewed, cue_delivery_reviewed,
command_endpoint_reviewed, process_limit_reviewed는 기본 false다. 검토는 근거를
대신하지 않는다. 미상 지연을 0으로, 명령 기한을 공정 기한으로 채우지 않는다.
서로 다른 clock/scenario를 암묵적으로 이동하거나 입력 관측을 simulation으로
바꾸지 않는다. 원 motion의 output_origin과 endpoint frame의 source_origin을 남긴다.

```python
import runpy
from pipeline_integrity.alarm_exchange import replay_alarm
from pipeline_integrity.response_trace import trace_response
from pipeline_integrity.valve_motion import simulate_valve_motion

frames, policy, request, process, spec, context = runpy.run_path("examples/response_trace.py")["make_case"]()
alarm = replay_alarm(frames, policy, context)
motion = simulate_valve_motion(request, context.model_copy(update={"asset_id": request.component_id}))
spec = spec.model_copy(update={"alarm_activation_id": alarm.activation_records[0]["id"]})
result = trace_response(alarm, motion, spec, context, process_frames=process)
assert result["recorded_alarm_time_s"] == 3.0
assert result["usable_cue_time_s"] == 4.0
assert result["command_issue_time_s"] == 5.0
assert result["target_completion_bracket_s"] == [8.0, 9.0]
assert result["process_endpoint"]["first_qualifying_sample_time_s"] == 10.0
```

이 예제는 원 monitoring/장치 패키지를 설치한 환경에서 실행한다. 임계값·지연·
공정 관측은 독립 작성한 합성 기준이며 현장 기록이 아니다.

## 목표 도달과 공정 관측의 차이

native motion의 completion_bracket_s는 두 관측 사이 완료 구간이다. 구간 상단이
한계 이하면 met, 하단이 한계보다 뒤면 missed, 한계가 bracket 내부/하단과
겹치면 uncertain이다. 명령 시각이 단서보다 앞서거나 원 command/receipt와 다르면
입력을 거부한다. 목표 미도달·짧은 이력·공백은 미상/자료 부족으로 남긴다.

목표 위치의 effective area가 양수일 수 있다. 0개도·0면적이어도 관망에 남은
재고나 하류 유동은 별도다. 이 함수는 위치로 zero flow를 만들지 않는다.

`SampledEndpointRule`은 stream/channel/unit, le·ge·abs_le, threshold,
required_hold_s, maximum_sample_interval_s, evidence_id와 interpretation_reviewed를
받는다. 이 규칙은 검토한 사용자 조건이다. 실제 승인 기준을 제공하는 내장 표가
아니다. 단서부터 공정 한계까지의 원 frame이 있어야 하며 양 끝의 정확한 표본을
요구한다. 결측/긴 간격은 hold를 끊고, 표본 사이 안전을 추정하지 않는다.

결과 state는 observed_before_limit, not_observed_by_limit, unknown이다. 첫째는
표본 규칙이 한 번 달성됐다는 뜻이며 이후 계속 안전했다는 뜻은 아니다. 둘째는
완전한 지정 표본에서 규칙 달성을 관측하지 못했다는 뜻이다. unknown은 관측·
근거가 부족하다는 뜻이다. status=exchange_checked는 이 연결 검사 상태이므로
공정 규칙 불만족과도 함께 나올 수 있다.

## 인적 시간 모델과 위험 입력

선택 인수 human_response에는 무결성을 확인한 `HumanResponseResult`만 허용한다.
그 결과의 자산·기회·조건·보호 효과가 같아야 하고 DeadlineEffect의 단서와
종료 시각은 이 연결의 usable cue 및 command_deadline_s와 일치해야 한다.

### 가용시간 시나리오 결과 연결

`human_response_scenarios`에는 `HumanResponseClient.evaluate_deadline_scenarios`가
생성한 `HumanResponseScenarioResult`를 전달하고, 같은 source-bound client를
`human_response_client`에 넣어 `verify_deadline_scenario_result`로 owner 재생을
확인한다. 단일 `human_response`와 시나리오 결과는 동시에 넣을 수 없다. 결과의
자산·시나리오·시계·근거, 조건·기회·protection ID가 trace와 같고, HRA 기준
window의 cue와 command deadline도 검토된 trace 값과 일치해야 한다. 각 시나리오의
절대 한계 `usable cue + available_time_s`는 command deadline 및 process limit를
넘을 수 없다. 결과 크기는 `maximum_human_deadline_scenarios`로 제한하며 기본은
256개다.

각 시나리오의 caller probability mass와 절대 기한에 관측된 command issue를 별도 표로 기록한다. `caller_weighted_observed_command_scenario_mass`는 선언된 시나리오 가중치에서 명령 기한 met/missed/uncertain/unknown의 합계만 집계한다. 이는 실험 빈도나 인간실패확률, 동적 사고확률이 아니다. 한 궤적의 command가 각 availability scenario의 기한에 도달했는지 검토하는 비교량이다.

시나리오 가중 실패확률은 전달된 deadline 집합과 기존 HRA owner 출력에서 온다.
관측 command issue time과 process endpoint는 별도 필드로 남고 이 한 궤적으로
HRA 확률을 재보정하지 않는다. 동적 FTA, 경쟁 사건, 독립 trial 불확실성 및 전체
공정 사고확률은 계산하지 않으므로 `dynamic_risk_probability`는 계속 null이다.
이 연동의 회귀 네 건은 작성만 했고 누리온 검증 전이다.
작업자 모델의 endpoint는 올바른 명령 발행이다. 밸브 이동/공정 달성 시간을
같은 인간 시간분포 안에 암묵적으로 포함하지 않는다.

timing/recovery가 이미 포함된 초기 추정에 같은 효과를 다시 적용하는 경우는
기존 HRA 경계에서 거부한다. 이 trace는 관측한 성공/실패로 해당 조건부 확률을
업데이트하지 않는다. 모델 모집단이 없는 반응 시간을 임의로 적합하지 않는다.
전체 사고 빈도로 이어가려면 command 성공/실패 뒤 물리 조건의 별도 조건부
모델과 자료·의존성 근거가 필요하다.

## 재현·검증

```powershell
python scripts/check_response_trace.py --monitoring PATH_TO_MONITOR --lh2dt PATH_TO_LH2DT --hra PATH_TO_HRA
python scripts/check_alarm_source.py --monitoring PATH_TO_MONITOR
python -m pytest -q tests/test_response_trace.py tests/test_alarm_exchange.py
```

원3엔진을 실제 호출하고 실행 전후 소스 해시를 비교했다. 작성 기준은 위
3/4/5초·8~9초·10초, 미상 단서/공정, HRA 명령 기한 교체 거부·timing 재소비,
두 HRA 수치의 독립 erfc 산술과 ETA 말단 합계를 포함한다. 합성 관망에서는
명령0.2초, 밸브 목표0.275~0.30000000000000004초를 확인했다. 공정 한계1초 전에
목표는 도달했지만 하류 유동은 목표 시각에0.01m3/s였고 선언 흐름 규칙은
달성되지 않았다. 기존 관망의 체적/접점 수지도 함께 확인했다.

단위 검사에는 공통 시간 이동, 미상·공백·예산·원단위/시계·완료 bracket과
가짜/변조 receipt 거부가 있다. 독립 검토에서 발견한 임의 객체의 motion 검증
우회는 실제 타입·엄격한 clone·복제 후 digest 재검사로 수정했다.

집계는 research/response_trace_results.json과 alarm_source_results.json,
원별 상세 결과는 로컬 artifacts/response_trace와 artifacts/alarm_source에 있다.
공정 수치행·변환행을 코드 배포에 포함하지 않는다. 후속 소유 엔진 확장은
EXTENSION_REQUESTS_KO의 PI-X06/03/01 계약으로 넘긴다.

## 추적 결과 재생

`verify_response_trace_record(record, alarm=..., motion=..., specification=..., context=..., process_frames=..., human_response=..., human_response_scenarios=..., human_response_client=...)`는 원 owner receipt와 동일한 요청·맥락·공정 frame으로 trace adapter를 다시 계산해 digest와 전 결과를 대조한다. 시나리오 HRA를 썼다면 원 `HumanResponseClient`도 전달해 owner scenario replay가 다시 수행되도록 한다. 공정 원 frame은 trace 결과에 중복 포함되지 않으므로 호출자가 같은 source slice를 보존해야 한다. 이 verifier는 trace 연결·표본 endpoint 변환을 확인하며, alarm/valve/HRA owner 계산 자체를 각각 재검증하는 기능은 아니다.
