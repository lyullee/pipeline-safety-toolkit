# GasLib → 정상 기체 관망 adapter

## 목적과 경계

`read_gaslib`가 읽은 네트워크/시나리오 기록을 기존 `GasNetwork` 입력으로 명시적으로 연결한다. 물성 계산과 질량·에너지·운동량 해는 복제하지 않고 `solve_gas_network`에 위임한다. 이 코드는 파서의 “needs_data”를 자동으로 완성하는 기능이 아니다.

호출자는 실행 전에 다음을 직접 검토하고 고유 evidence ID로 `RunContext.evidence`에 기록한다.

- 유체 조성, CoolProp backend/EOS 선택, 물성 basis
- 모든 노드의 상태 역할과 양의 절대압(Pa), 온도(K); 접합점에는 질량 인출(kg/s)
- 각 pipe의 국부손실, 열 유입(W), 분할 수와 그 출처
- 압력 기준, GasLib nomination을 입력 상태와 연결하는 해석, topology/state 매핑 승인
- 원 ZIP, 선택 .net/.scn 멤버 각각의 SHA-256 및 CC BY 3.0 출처 표기

경계 노드는 고정 압력/온도를 갖고 별도 withdrawal을 가질 수 없다. 모든 노드는 정확히 한 번 상태 매핑해야 하며 모든 원 pipe는 제어 근거를 가져야 한다. context의 시나리오 ID와 mapping 시나리오 ID도 일치해야 한다. 설정된 nomination의 경계값을 질량유량 또는 관측 시계열로 환산하지 않는다. 결과 receipt는 `nomination_values_applied=false`, 원 GasLib 해 재현=false, 현장 자격=false를 기록한다.

## 거부 조건

- 원 아카이브를 다시 읽은 기록과 전달된 파싱 기록의 digest가 다름
- 선택한 source member 쌍에 CC BY 3.0 inline 표기가 없음, source hash/license 근거 누락
- 승인되지 않은 mapping, context와 scenario 불일치, 누락 또는 추가 노드·pipe mapping, evidence ID 중복/누락
- active non-pipe link(밸브·압축기 등), 비영 elevation change, 유효하지 않은 경계/유체 입력

따라서 예시 GasLib-11 topology(8 pipe, 1 valve, 2 compressor)는 이 adapter가 그대로 계산하지 않는다. 이 장치들을 다루는 owner solver capability가 갖춰질 때까지 명시적으로 보류한다. 동등 elevation이며 pipe만 있는 축소 연구 시나리오는 별도 mapping evidence로 사용할 수 있지만 원 사례 재현을 뜻하지 않는다.

## API

```python
source = read_gaslib(archive_path, network_member="case.net", scenario_member="case.scn")
spec = prepare_gaslib_network(source, archive_path, mapping, context)  # 준비만 수행
receipt = solve_gaslib_network(source, archive_path, mapping, context, backend=provider)
verify_gaslib_network_run(receipt)
```

`GasLibNetworkMapping`은 입력 계약이다. `GasLibNodeState`는 boundary/junction, 절대압, 온도, 선택 withdrawal을, `GasLibPipeControl`은 국부 손실/열 입력/segment/evidence를 가진다. `solve_gaslib_network`는 source record, mapping, context를 계산 전에 독립 snapshot으로 고정한 뒤 기존 owner에 전달하고, 같은 snapshot을 결과 receipt에 사용한다. 따라서 owner 실행 도중 호출자가 원래 dict나 mapping의 내부 dict를 바꿔도 계산 입력과 receipt가 분리되지 않는다. 계산 결과는 `GasNetworkResult`를 포함하고 owner checks도 보존한다. `verify_gaslib_network_run`은 receipt 재생 때 mapping의 caller review 승인, 유체·backend·물성 basis·근거 ID, node 역할·경계/접점 상태·pipe control이 저장된 owner specification과 context에 일치하는지, owner 결과의 사양 digest/context가 receipt와 같은지 다시 확인한다. 원 archive/member SHA와 CC BY 3.0 evidence 연결도 검증한다. 이는 실제 archive가 계속 보존되거나 재열람됐음을 증명하지는 않는다. 아직 public re-export 또는 CLI 등록은 하지 않았다.

## 검증/재생

`tests/test_gaslib_network.py`는 네트워크/시나리오 XML을 코드 내에서 생성하고 source 변경, active valve, elevation, composition-evidence 누락 및 context scenario 불일치 경계를 기술한다. receipt를 다시 봉인해도 mapping node 압력, 유체, caller review 표시 또는 pipe-control evidence를 owner specification/context와 다르게 바꾼 경우를 거부하는 매개 사례를 추가했다. owner 실행 중 호출자의 source hash와 mapping pressure를 변경해도 반환 receipt가 원래 계산 snapshot을 유지하는 경우도 기술한다. 이는 synthetic 계약 사례이며 실제 GasLib 전체 모델의 정확도나 성능을 보이지 않는다. 정책에 따라 회귀 실행은 누리온 대기다. 다음 누리온 bundle 갱신 시 해당 시험을 targeted manifest에 포함하고, 실제 계산 전에 적격 Python/dependency 환경과 owner solver provenance를 고정해야 한다.

## 구현 이력과 재개

- 기존 파서를 호출하여 source archive와 선택 member를 매번 재확인한다.
- geometry SI 값만 복사하고 caller-controlled 물리/운전값은 매핑 계약에서 받는다.
- 기존 gas-network owner solver로 실행을 전달한다.
- 새 계산식, 표준식 전재, 원자료 문장/계수 복사는 없다. 원 dataset 이용은 기존 라이선스 및 attribution 조건을 따라야 한다.
- 남은 작업: synthetic 회귀를 누리온에서 실행, CLI 선택성·multi-scenario 사용성 검토, topology/device capability가 확장될 경우 새 매핑 형태 설계, API inventory 및 validation manifest 갱신.
