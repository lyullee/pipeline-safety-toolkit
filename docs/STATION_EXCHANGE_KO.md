# 수소 충전소 원 계산 연계

`station_exchange.evaluate(StationRequest)`는 별도 Python 프로세스에서
hydrogen-station-sim 0.1.2(MIT, 연구 Alpha)의 실제 공개 API를 호출한다.
원 열역학·제어·ODE 식을 복제하지 않는다. 원 저장소는
[hydrogen-station-sim](https://github.com/lyullee/hydrogen-station-sim)이며 공개 배포 DOI는
10.5281/zenodo.23084421이다. 원 라이선스와 소스 해시는 결과에 보존한다.

기본 요청은 `needs_data`다. source/model/conditions 세 검토, 원 파일 전체
manifest의 digest, 공통 `RunContext`와 선택한 `EvidenceRef.id`를 제공해야 실행한다.
`context`에 임의 사전을 넘길 수 없다. 선택 evidence는 context의 중복 없는
근거 ID 중 일부이며, 중복·미등록 ID는 계산 전에 거부한다. 근거의 citation,
origin, locator, license와 제한도 context 해시에 포함한다.
검토 선언은 설비 적합성 인증이나 독립 검증을 뜻하지 않는다. pressure는 Pa_abs,
temperature K, time s, mass kg, energy J다. 원점은 episode 경과시간 0이며
모든 초기조건·환경조건·포트·명령은 합성 또는 실제 관측의 출처를 context에 기록한다.

`metadata.station_boundary`는 아래 여섯 항목을 정확히 갖는다.

| 항목 | 계약 |
|---|---|
| `asset_id` | `RunContext.asset_id`와 일치 |
| `clock_id` | `RunContext.clock_id`와 일치 |
| `clock_offset_s` | 0 이상의 초, 로컬 episode 시각에 한 번 더함 |
| `event_id` | 사용자가 정한 비어 있지 않은 사건 ID |
| `pressure_reference` | `absolute` |
| `property_basis` | `Hydrogen TABLE; pinned src/h2station/data/hydrogen_properties_v1.npz` |

`reference_boundary(asset_id, clock_id)`는 작성한 예제 라벨과 offset 0을 반환한다.
현장 사건과 시간 원점은 사용자가 바꾸고 검토한다. 세 검토를 모두 True로
선언해야 실행하며 문자열 `"true"`는 인정하지 않는다. 응답 봉투는
`station-exchange/2`, 아래 두 계산 입력 schema는 `/1`을 유지한다.

```python
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.station_exchange import (
    StationRequest,
    evaluate,
    reference_boundary,
    reference_config,
    validate_stored_result,
)

context = RunContext(
    asset_id="authored-station",
    scenario_id="authored-transfer",
    clock_id="episode-clock",
    origin="synthetic",
    evidence=(
        EvidenceRef(
            id="case",
            citation="Original synthetic verification conditions",
            origin="synthetic",
            license_id="MIT",
        ),
    ),
    metadata={"station_boundary": reference_boundary("authored-station", "episode-clock")},
)
request = StationRequest(
    "OWNER_SOURCE_ROOT", "STATION_PYTHON", reference_config(), context, evidence=("case",)
)
result = evaluate(request)
assert result["status"] == "needs_data"
assert validate_stored_result(result)["valid"]
```

실제 실행에서는 `source_pin=digest(source_manifest(source_root))`를 원 파일 검토
뒤에 지정하고 `source_reviewed`, `model_reviewed`, `conditions_reviewed`를 True로
설정한다. 검토 선언을 프로그램에서 자동으로 참으로 바꾸지 않는다.

`station-network-exchange/1`은 1~8개의 사용자가 구성한 탱크, 최대16개 내부
restriction 연결, 최대100개의 시간별 opening 명령을 받는다. 초기 압력/온도,
체적/열전달/벽 열용량, 환경 온도, 직경, 역류 허용과 연결 포트가 교체 가능하다.
원 `StationNetwork.simulate/evaluate`, `LumpedTank`, `RealGasRestriction`을 사용한다.
유량은 항상 실제 source→target의 양수이며 역류 시 실제 포트를 뒤집어 보존한다.
명령은 시간순 zero-order hold이고 불연속에서 적분을 분할한다. 최대300s,
2001개 요청 격자, 최대100000개 RHS 호출과 별도 wall timeout/output budget을 둔다.
명령 경계가 격자에 없으면 추가되므로 결과 clock을 직접 사용해야 한다.
별도 프로세스는 `input_budget_bytes`/`output_budget_bytes` 각각 기본 2,000,000바이트,
허용 1,024~8,000,000바이트, `timeout_s` 기본30s/허용0.05~120s로 제한한다.
입출력은 UTF-8이며 원 경고와 로그를 무제한 stdout으로 받지 않는다.

`station-reference-episode/1`은 원 `build_reference_scenario`와
`SafeFullStationSimulator.simulate`를 직접 호출한다. `scenario`에 검토된 환경,
초기 차량/3bank 조건, compressor suction, header/package 형상 등 명시 필드를
넘길 수 있다. 최대30s/300 control steps다. no-fault 조건과 uncalibrated
`vehicle_tank_calibration=reference`로 제한한다. 원 risk_snapshots를 그대로
전달하되 consequence가 요청되면 실패한다. 별도 HyRAM/HRA/PRISM 계산, 외부
제어·LLM 호출을 추가하지 않는다. full-loop mass/energy ledger는 이 경계에서
제공하지 못해 `balance_status=needs_data`다. active fault/HyRAM 및 full-loop
질량·에너지 유입출 집계는 원 API 확장과 별도 검토가 필요하다.

환경은 main Python과 분리한다. Windows Python3.12에서 공식 pip의 wheel 총
약55MB를 확인하고 다음 네 직접 의존성을 고정했다. 취득 원본에는 필수
`hydrogen_properties_v1.npz`가 없어 최초 실행이 실패했다. 원본을 보존하고
`station-runtime`에 src/data/LICENSE/pyproject와 원 생성기를 복사한 뒤
CoolProp8.0.0(공식 wheel10.9MB)으로 원 생성기를 실행했다. 생성기와 생성된
테이블도 source pin에 포함된다. 원 소스 코드는 설치/수정하지 않고 worker가
검토된 snapshot src에서 읽는다. 매 실행 전후 snapshot manifest를 비교하고
guide의 upstream 옵션은 원본 불변과 복사본의 기존 파일 일치도 검사한다.
물성은 원 `HydrogenEOS`의 Hydrogen/TABLE 한 종류다. 다른 검토 테이블은
별도 snapshot의 같은 원 경로에 두고 전체 source pin을 다시 산정한다.
동일 source pin 아래에서 테이블을 임의 교체할 수 없다. 원 SciPy 제약 밖의
interpreter는 worker가 계산 전에 거부한다.

```powershell
..\tmp\analysis\venv\Scripts\python.exe -m venv ..\tmp\analysis\venv-station
..\tmp\analysis\venv-station\Scripts\python.exe -m pip install numpy==2.2.6 scipy==1.15.3 fluids==1.3.1 ht==1.2.0
$stationSnapshot = '..\tmp\analysis\station-runtime'
New-Item -ItemType Directory -Path $stationSnapshot -Force
Copy-Item ..\tmp\analysis\repos\hydrogen-station-sim\src $stationSnapshot -Recurse -Force
Copy-Item ..\tmp\analysis\repos\hydrogen-station-sim\data $stationSnapshot -Recurse -Force
Copy-Item ..\tmp\analysis\repos\hydrogen-station-sim\LICENSE, ..\tmp\analysis\repos\hydrogen-station-sim\pyproject.toml $stationSnapshot -Force
New-Item -ItemType Directory -Path "$stationSnapshot\scripts" -Force
Copy-Item ..\tmp\analysis\repos\hydrogen-station-sim\scripts\generate_hydrogen_table.py "$stationSnapshot\scripts" -Force
..\tmp\analysis\venv-station\Scripts\python.exe -m pip install CoolProp==8.0.0
..\tmp\analysis\venv-station\Scripts\python.exe ..\tmp\analysis\station-runtime\scripts\generate_hydrogen_table.py
..\tmp\analysis\venv\Scripts\python.exe scripts/check_station_exchange.py --source ..\tmp\analysis\station-runtime --upstream-source ..\tmp\analysis\repos\hydrogen-station-sim --python ..\tmp\analysis\venv-station\Scripts\python.exe
..\tmp\analysis\venv\Scripts\python.exe examples/station_exchange.py
..\tmp\analysis\venv\Scripts\python.exe -m pytest tests/test_station_exchange.py -q
```

위 guide는 closed/equal/open/reverse/명령변경/격자세분/3노드/full-reference를
실행하고 RHS예산100으로 중단한 명령변경 사례의 완료 prefix도 검사한다.
mass residual은1e-8kg, 에너지 residual은0.5J를 독립 수지 기준으로
선언했다. energy는 가스 내부에너지+벽 열용량×벽온도에서 환경열 사다리꼴
적분을 뺀 값이다. 격자세분 질량차1e-7kg도 별도 검사한다. solver 통과와
수지 검사는 실험 검증이나 제어기·충전 프로토콜 적합성을 증명하지 않는다.
검증 guide의 수지·거동·자료 계약 기준78개와 집중 pytest54개를 실행했다.
78개 모두가 독립 수치 정답은 아니다. 원 결과9개와 중단 prefix1개의 저장 영수증,
시계·출력 단위·원점·역류 부호·보류 경로를 함께 검사한다. 실제 응답에서 시계,
포트, 수지, 물성 라벨, import 위치, 근거, 외부 상태 복제, worker 해시를 바꾸고
해시를 다시 계산한8개 오염 사례도 거부했다. Ruff check와 format check도 통과했다.
연구 결과 JSON에 실행별 집계와 파일 해시를 기록한다.

## 저장 기록과 하류 신호

`validate_stored_result(result, source_root, context)`는 저장된 원 응답과 봉투,
입력·시계·자산·근거, 현재 adapter/worker와 원 snapshot 바이트를 대조한다.
관망에서는 배열 길이, 요청 시계 또는 완료 prefix, 탱크 ID, 실제 흐름 포트,
원 버전과 물성 기준을 확인하고 가스+벽 에너지·환경열 수지를 배열에서 다시
집계한다. 전체 기준 episode의 수지는 미상으로 보존한다. 해시는 서명이 아니며
독립 실행, 물리 교정, 검토자의 승인 사실을 증명하지 않는다. snapshot 경로도
요청 해시에 묶으므로 다른 위치에서 검증하려면 원 기록의 의미를 검토하고
그 위치에서 새 실행 기록을 만든다. 보류 `needs_data` 기록은 소유 소스를
열거나 외부 라이브러리를 import하지 않고 로컬 봉투만 확인할 수 있다.

`to_signal_frames(result, context, source_root=source_root)`는 위 검사를 통과한
완료 기록만 공통 `SignalFrame`으로 변환한다. 관망 질량 잔차1e-8kg 또는
에너지 잔차0.5J 기준을 실패하면 해시·선언 수지·배열이 서로 일치하더라도
하류 신호를 내보내지 않는다. 저장 기록의 valid는 바이트와 계약의 일관성이며
수지 통과를 뜻하지 않는다. 원 solver의 computed 상태는 진단 기록에 보존한다.
시계는 `local_time + clock_offset_s`,
stream은 `asset_id:event_id`다. context가 synthetic이면 출력도 synthetic,
그 밖의 원 계산 출력은 simulation이다. 관망은 각 탱크의 질량kg, 가스 내부에너지J,
벽·가스 온도K와 절대압력Pa_abs를 제공한다. 연결 유량은 설정에 쓴 source→target을
양의 방향으로 정의해 역류는 음수로 출력한다. 원 응답에는 실제 source/target와
양의 크기가 남는다. 원 `StationNetwork.evaluate`는 흐름이 없는 restriction을
행에서 생략하므로 그 유량만 0으로 표현하고 흐르는 stream의 엔탈피는
정의되지 않아 None/valid=False로 둔다. 유량·엔탈피 채널명에는 연결 ID가 포함된다.

전체 기준 episode는 차량 압력·온도·질량, header 압력·온도·질량, nozzle 유량의
노출된7개 scalar 채널만 전달한다. 미상 전체 수지, 위험 확률, 상·분사·누설 유량을
만들지 않는다. `partial`은 기본 변환을 거부하고 `allow_partial=True`가 있을 때만
반환된 완료 prefix를 `operating_mode=station-native-partial-prefix`로 전달한다.
이 선택은 중단한 구간을 완료로 바꾸지 않는다. 위 신호는 감시·경보 입력으로
사용할 수 있으나 경보·HEP·고장 확률이나 설비 자격은 별도로 검증해야 한다.

실제 요청/응답과 배열은 배포 제외 `artifacts/station_exchange`에 저장하고
공개 `research/station_exchange_results.json`에는 집계·소스와 adapter 해시만 둔다.
입력 NaN/unknown/잘못된 부호·포트·clock는 계산 전 invalid로 남긴다. 출력의
비유한 값은 null과 원래 문자열/경로를 함께 남겨 partial로 표기한다. 네이티브 solver의
완료된 분할 이력과 오류 메시지는 partial로 보존한다. 단일 solve가 예외를 내면
해당 solve 내부 배열은 원 API가 반환하지 않으므로 회수할 수 없다. 프로세스
timeout은 완료 응답이 없음을 명시한다. 전체 충전소 qualification은 미완료다.
