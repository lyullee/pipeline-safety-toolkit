# 검토된 지면 액체 수소 pool 연계

`pool_exchange.evaluate_pool_request(PoolRequest)`는 원 DynamicLH2PoolX SDK의
두 동적 경로를 호출한다. SDK0.2.0rc2, commit
`6a700e8e183039bbb81803bacc416f6c9117ed71`, MIT이며 출처는
https://github.com/lyullee/DynamicLH2PoolX 이다. 원8Python 모듈과 LICENSE,
README, pyproject의 SHA256을 고정하며 코드·solver·실험 계수를 복제하지 않는다.
예제는 conductivity1W/(m K), diffusivity1e-6m²/s, water heat flux1000W/m² 등
작성한 가상값이다. 원 README의 실험 검증 주장으로 이 adapter를 검증했다고
보지 않는다. 역사적 quasi-steady 경로는 이 adapter의 동적 경로와 대체 불가다.

입력은 이미 수평 지면에 도달한 액체kg/s다. jet impact, deposition, 분율 추정,
phase/fugacity, 분산, HEP, 기간확률, 자동 누설면적은 계산하지 않는다. 기존
AccidentHistory의 withheld 상태를 바꾸지 않는다. 별도 직접 검토한 boundary를
입력할 수 있다. 원 inflow 시계는0에서 시작하며 ZOH와 마지막 rate의 무기한 hold를
명시한다. output도0부터 엄격 증가한다. clock offset은 SignalFrame 변환에만 적용한다.
boundary에는 ENU origin[m], source_asset_id/event_id/port_id를 명시하고
`RunContext.metadata['pool_boundary']`와 정확히 일치시킨다.

기본 source/model/boundary 검토는false이며 미확정 요청은 source 파일 검사나
native import 전에 needs_data다. 세 검토True, evidence가 있는 RunContext와
source pin을 제공해야 실행한다. None/False/0을 서로 대체하지 않으며 입력 NaN은
invalid다. 유체는 원 SDK의 Hydrogen 포화 Q0/Q1, pressure는 ambient Pa_abs다.
명시 property_basis=`CP-HEOS:Hydrogen:native`만 받는다. ParaHydrogen과 게이지압
자동변환은 미지원이다. 기존 H2 bridge의 다른 prefix와 동치라고 가정하지 않는다.

사용자 교체점은 `reference_config('spreading'|'fixed_area')`의 명시된 필드다.
spreading의 solid/water surface kwargs, ShallowLayerNumerics 전체, closure,
ambient pressure, 초기질량(현재0만), output clock, inflow를 교체한다. fixed_area는
Substrate kwargs와 area/ambient temperature/heat cap(None허용)/thermal age,
초기질량을 교체한다. 원 `DynamicPoolConfig/run_dynamic_pool` 또는
`FixedAreaPoolConfig/run_fixed_area_pool`를 호출하며 모든 원 ledger를 그대로 남긴다.
fixed-area는 영입력·빈재고를 지원하지만 지속 thermal age를 가정하고 마른 표면의
재가열을 지원하지 않는다. water 경험 closure는 약50–60s 범위로60s를 넘기면 보류한다.

기본 client는 JSON subprocess worker다. `PoolRequest.python_executable`로 기존
호환 Python을, `source_root`로 검토한 SDK snapshot을 선택한다. 다른 클라이언트
구현은 같은 schema와 receipt/예산 검사를 유지하는 별도 adapter가 필요하다.
최대1001 timestamps,300s,2000radial cells, nominal cell-steps2백만, wall timeout
0.05~120s와 serialized input/output 각각2KB~8MB를 계산 전에 검사한다. SDK에는 실제 adaptive
substep 상한이 없어 nominal work만으로 시간을 보장할 수 없으며 timeout을 함께 쓴다.
stdout/stderr는 통신에 쓰지 않고 크기가 제한된 응답 JSON과 request pin을 결합한다.

모든 native ledger 필드, 원 residual, 누적 numerical adjustment, escaped mass,
source_status, warning을 보존한다. wet_area와 reported_area는 다르며 reported_area0이
inventory0을 의미하지 않는다. spreading interval_mean/last_substep와 fixed-area
instantaneous rate를 바꾸지 않는다. 독립 ZOH적분과 매행
I0+유입+수치조정−증발−유출−재고를 검사한다. 잔차1e-8kg 초과나 비영 수치조정은
diagnostic_rejected, domain escape 또는 원 out_of_scope는 out_of_scope로 보존한다.
경고는 지우지 않는다. SDK에 에너지 ledger가 없어서 에너지 수지는 unverified다.
SDK예외는 error이며 완료되지 않은 ledger를 성공으로 만들지 않는다. SDK는 예외
중간 ledger를 반환하지 않는다. 비유한 출력은 경로/원 문자열과 null로 partial 보존한다.

`to_signal_frames(result,context)`는 기존 SignalFrame과 RunContext를 사용한다.
누적증발 차분/시간간격으로 질량 보존 interval mean을 만들고 첫행은None/qualityFalse다.
context 일치와 computed 상태가 필요하다. ENU/asset/event/port/evidence는 원 context와
stored result에서 보존한다. `validate_stored_result`는 전체receipt/config/context/
evidence/native/request/source/worker/adapter pin과 현재 원소스 불변을 검사한다.
저장 검증과 신호 출력은 모두 초기 재고, 비음 재고·누적량, 누적 유입·증발·유출의
단조성, 매행 질량 수지, 집계 진단 및 상태의 일치를 다시 검사한다. 원 out_of_scope를
computed로 바꾸거나 음의 재고·증발량을 넣은 뒤 해시를 다시 계산해도 거부한다.
신호 출력만으로 현재 SDK 소스의 무결성을 확인하지는 않으므로 소스 확인이 필요한
인수 경계에서는 `validate_stored_result(result,source_root,context)`도 먼저 호출한다.
이 receipt는 서명이나 실제 실행의 증명·공학 승인서가 아니다.

```powershell
python scripts/check_pool_exchange.py --source PATH_TO_REVIEWED_SDK
python examples/pool_exchange.py
python -m pytest tests/test_pool_exchange.py -q
python -m ruff check src/pipeline_integrity/pool_exchange.py src/pipeline_integrity/pool_worker.py tests/test_pool_exchange.py scripts/check_pool_exchange.py examples/pool_exchange.py
```

guide는 실제 두 route/두surface, 영·미소 유입, dryout, wet/report 분리,
domain escape, 누적 ZOH final hold, 격자/시간 세분과 수지를 실행한다. fixed solid의
uncapped 열유속을 독립 적분한 값과 CoolProp HEOS 직접 포화값을 비교한다.
이는 실험 검증·현장 적합성 입증이 아니다. raw ledger/kwargs/실제 요청응답은
배포 제외 artifacts/pool_exchange에 저장하고 공개 research에는 집계·해시·단위·
제약만 남긴다. 개인 SDK 경로나 Python 경로를 공개 집계에 포함하지 않는다.
최종 검증은 수치·계약·진단 집계68개와 집중 pytest35개를 통과했다. 네이티브 saturation
오류, 응답 크기 초과와 timeout을 별도 실제 프로세스로 실행해 성공과 구분했고,
stored receipt의 ENU/native ledger 변조와 의미가 바뀐 재해시 기록의 거부도 확인했다.
68개 모두를 독립 물리 기준으로 세지 않는다. Ruff 검사와 포맷도 통과했다.
