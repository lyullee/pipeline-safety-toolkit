# 공간 노출과 개인·사회적 위험 연결

`spatial_risk`는 기존 PRISM과 human-reliability-toolkit의 위험 API에
공간 지지영역·인구·재실·자료 의미를 연결한다. 조건부 위해확률을 만드는 확산·화재·폭발
모델과 위험 합산 구현은 원래 프로젝트에 남아 있다. 이 모듈은 별도 위험 엔진이나
독립적인 인명피해 상관모델을 제공하지 않는다.

현재 계산 범위는 로컬 ENU 직사각형 셀의 대표 수용체와 분리된 정수 인원 집단이다.
셀 중심에서의 연간화된 개인 위해율, 재실을 적용한 위해율, 기대 사망자 기여를 지도
자료로 반환한다. 실제 정수 사망자 F-N은 검토된 결합 분포를 별도 제공할 때만 계산한다.
연속 등고선, 지리 좌표 자동 변환, 보간, 공개 인구 자료의 현장 노출 검증은 미완료다.

## 책임과 사용 API

| API | 역할 |
| --- | --- |
| `SpatialGrid` | 좌표계·원점·축·수직 기준·경계선·대표 높이 |
| `SpatialPopulation` | 셀별 인원 수·재실확률·수용체 ID·근거 |
| `SpatialHarmField` | 원본 조건부 확률장 또는 전체 `ConsequenceResult` 보존 |
| `SpatialCondition` / `SpatialEvent` | 사상 내 조건 분할과 원래 `RiskFrequency` 연결 |
| `JointCasualtyState` / `JointCasualtyDistribution` | 모든 셀의 정수 사망자 수와 상태 확률을 가진 결합 상태표 |
| `SpatialRiskRequest` | 평가 시간창·시계·사상 관계·검토·작업 예산 |
| `native_backend_metadata()` | 명시적으로 선택 라이브러리를 읽어 현재 코드·권리·실행환경 확인 |
| `evaluate_spatial_risk(request, context)` | 검토가 끝난 자료만 실제 소유 API로 전달 |
| `verify_spatial_risk_record(record, request=..., context=...)` | 원본에서 실제 위험 산술과 의미를 재실행 |
| `to_signal_frames(record, context, allow_partial=False)` | 재검사를 통과한 셀별 지도 값 연결 |

선택 네이티브 호출은 PRISM `lh2_tno.risk.annual_risk`, HRA `event_tree`와 `fn_curve`다.
PRISM `prism_qra.risk.fn_curve`도 호출하지만 기대 사망자 문턱 곡선으로 따로 보존한다.
소스 패키지 전체 Python 트리, 원래 MIT 라이선스 바이트, 버전과 실행환경을 시작·종료에
다시 확인한다. 예제에서 선택 소스 경로를 지정하며, 이 패키지 안에 원래 구현을 복제하지 않는다.

## 좌표·공간 지지영역

`crs="local_ENU"`, 축 순서 east/north/up, 길이 m를 사용한다. `frame_id`와 원점 설명,
수평·수직 datum이 필수다. 지리적으로 알려진 원점이면 위도·경도를 함께 기록한다.
가상의 로컬 예제는 지구상 위치가 확인되었다고 표시하지 않는다. 경위도 도 단위를 m로
해석하는 기능이나 EPSG 변환은 없다.

각 `x_edges_m`, `y_edges_m` 구간의 곱이 셀 면적 m²다. 셀은 `cell:행:열`의 행 우선 순서로
배열되며, 중심점과 한 높이에 대응하는 수용체 하나가 해당 인구 집단을 대표한다.
대표성을 검토했다는 근거가 필요하다. 수용체 값은 셀 평균이나 셀 전체 피해확률로
자동 승격되지 않는다. 좌표 직렬화 오차 허용은 절대 `1e-10 m`이며 보간 허용폭이 아니다.
하나의 수용체를 여러 인구 셀에 반복 연결하거나 셀을 생략하면 거부한다.

모든 조건은 모든 셀의 인원·재실·수용체를 명시해야 한다. 인원 0은 알려진 0이고
`None`은 모름이다. 연결 수용체가 없거나 확률장에 없으면 모름을 유지한다. 존재하는
수용체의 좌표가 다르거나 원래 필드 영역을 벗어난 네이티브 자료는 거부한다.

## 인원·재실·시간·빈도

인원은 셀 내 겹치지 않는 정수 `persons`다. 밀도, 인시(person-hour), 통행량 또는
분수 인구 추정치를 인원으로 받지 않는다. `presence_probability`는 각 조건에서 사람의
재실 확률이며 한 번만 적용한다. 특정인을 계속 그 위치에 둔 `fixed_location_ir_per_year`는
재실이나 인원 수와 무관하다. `present_person_ir_per_year`에는 재실이 한 번 적용되고,
`expected_fatalities_per_year`에는 해당 인원 수가 곱해진다. 인원 수에 셀 면적을 다시
곱하지 않는다. 조건마다 인원·재실을 바꿀 수 있다.

`RiskFrequency`는 원래 사상 ID, `loss_of_containment` 끝점, 발생 조건과 실제 `RunContext`를
유지해야 한다. 연간 빈도는 확률이 아니므로 1/year보다 클 수 있다. 사상별 조건 확률은
유일한 ID로 분할되어 합이 1이어야 한다. 사상과 조건의 빈도 독립성 검토 근거를 보존한다.
여러 사상은 중복 계수되지 않는 사상 정의 또는 독립 발생 과정이라는 검토가 필요하다.
이 연산은 빈도 기여의 덧셈이며 적어도 한 사고가 날 확률이나 동시 사고 확률을 계산하지 않는다.

위해확률은 이미 사상이 발생했고 해당 조건이라는 전제의 확률이다. 추가 점화·방호·FTA
인자를 이 모듈에서 임의로 곱하지 않는다. 그런 분기가 있으면 상위 조건부 자료에 명시한다.
`window_start_s`/`window_finish_s`와 `clock_id`는 초 단위 실제 평가창이다. 유한한 위해 평가창을
발생 빈도로 연간화한 값이므로 모든 후속 노출·대피·회복까지 포함한 시설 총위험을 뜻하지 않는다.

원본 네이티브 결과를 쓸 때는 제공자의 시점 오프셋과 네이티브 평가 시점을 더해 이 평가창과
정확히 맞춘다. 원래 asset/scenario/clock/origin/metadata는 그대로 두고 노출 근거만 추가할 수 있다.
원래 근거도 유지한다. 원래 방출 패킷과 소유자의 압축성 handoff 부착 검사를 거쳐,
소유자의 `build_consequence_request_from_provider_release`로 원래 `case.defaults`와 단위 재실에서
전체 요청을 다시 만든다. 보존된 요청과 정확히 일치해야 한다. 소유자의 시설 digest와
`resolved_source_context`를 포함한 실제 worker payload digest로 시설·높이·원래 요청을 묶는다.
원래 가스 패킷의 자산·유체/에너지 기준과 시설의 구성요소, 외향 기상 출구의 위치·위상도
서로 맞아야 한다. 시설 digest를 다시 만든 것만으로 다른 유체나 자산에 연결할 수 없다.
실패한 `ok`, 계산 이슈, 잘린 영역, 변경된 실행/자격 플래그는 계산된 필드로 내보내지 않는다.
원래부터 withheld인 자료는 실패를 성공으로 바꾸지 않고 위치 지지영역을 유지한 모름으로 남긴다.

물질수지·점화 분할뿐 아니라 보존된 원시 열유속 이력에서 두 민감도 모두의 선량과 위해를
소유자의 `dose_harm`으로 다시 계산한다. 점화 시점 농도·가연성 질량·기하에서 소유자의
flash/overpressure API를 적용하고 `weighted_union_harm`으로 가지 위해를 재생한다.
요약 열 위해·가지 위해·최종 확률이 모두 이 재생과 맞아야 한다. 기존 모듈의 평면 import가
호스트의 같은 이름 모듈과 충돌하지 않도록 작은 격리 Python 프로세스에서 실행하며,
실제 소유 코드 트리·실행환경·입력 digest와 결과를 별도 native receipt로 남긴다.
이 검사는 보존된 공간 필드의 원시량에서 위해로 가는 소유 산술의 재생이다.
확산·화염 기하를 새로 푼 결과나 독립적인 현장 검증으로 표현하지 않는다.
저위·고위 민감도 중 하나를 명시적으로 선택하며
그 쌍을 신뢰구간이나 임의의 casualty 분포로 해석하지 않는다. 물리 현상을 다시 풀어
독립 현장 검증했다고 주장하지 않는다.

## 기대 사망자와 F-N의 구별

`expected_death_threshold_curve`는 PRISM의 기존 정의를 유지한다. 각 조건의 기대 사망자 수가
어떤 실수 문턱 이상인 사건 빈도를 모으며, 기대값이 1보다 작은 조건은 그 곡선에 나타나지 않는다.
이 값은 정수 사망자 수가 N명 이상일 빈도와 다르다.

`integer_casualty_fn_curve`는 HRA의 정수 결합 분포 API를 실제 호출한다. 별도 제공한
`JointCasualtyDistribution`은 사상·조건·시계·원본 확률장·인구 배열에 묶여 있어야 하며,
재실을 이미 한 번 포함해야 한다. 각 결합 상태는 모든 셀의 정수 사망자 수를 명시한다.
각 셀의 사망자는 그 셀 인원을 넘을 수 없으며 상태 확률의 합은 1이다. **각 셀별 평균**이
해당 셀의 인원×재실×조건부 위해확률과 맞아야 한다. 이 검사를 통과한 상태별 총 사망자를
묶어 정수 분포를 만든 뒤 원래 HRA F-N에 전달한다.

전체 평균만 검사하지 않는다. 예를 들어 각 한 명인 두 셀의 확률이 .9/.1인데 사망자 총수가
0명 또는 2명일 확률이 각각 .5인 분포는 총 평균 1명이 맞아도 두 셀 주변확률과 양립하지 않는다.
셀별 결합 상태 검사는 이 반례를 거부한다. 물리적인 결합 의존성과 자료 적합성은 별도 검토
근거의 책임이다. 같은 셀 주변확률을 보존하는 다른 결합 상태표에서 F-N만 달라지는 것도 검증한다.
사람 간 독립을 가정해
이항분포나 Poisson-binomial 분포를 자동 생성하지 않는다. 결합 분포가 없으면 개인 위해율과
기대 사망자는 계산 가능해도 실제 F-N은 `None`으로 남는다.

두 셀 독립 산술 예제의 입력은 빈도 .02/year, 조건 가중 .25/.75, 인원 2/1,
조건부 위해 .5/1, 재실 (1,.5)/(.5,0)이다. 결과는 다음과 같다.

| 결과 | 값 |
| --- | --- |
| 고정 위치 개인 위해율 | .01 / .02 per year |
| 재실 적용 개인 위해율 | .00625 / .0025 per year |
| 전체 기대 사망자 | .015 person/year |
| 검토된 별도 정수 결합 분포의 F(1), F(2), F(3) | .01125 / .00375 / 0 per year |
| PRISM 기대 사망자 문턱 곡선 | 기대 사망자 1.5에서 .005/year |

이 숫자는 작성한 작은 참조 사례의 산술 검증이다. 실제 인구·사망 자료로 보정한 정확도가 아니다.

## 실행과 다른 모듈 연결

기본 실행은 소유 엔진 설치 없이 검토 대기 결과를 만든다.

```python
import runpy
from pathlib import Path
from pipeline_integrity.spatial_risk import evaluate_spatial_risk, verify_spatial_risk_record

make_case = runpy.run_path(str(Path("examples/spatial_risk.py")))["make_case"]
request, context = make_case()
result = evaluate_spatial_risk(request, context)
assert result["status"] == "needs_data"
assert verify_spatial_risk_record(result, request=request, context=context)["valid"]
```

검토된 외부 소스에 대해서만 `--reviewed`로 실제 네이티브 API를 실행한다.

```powershell
python examples/spatial_risk.py --prism /path/to/prism/lh2-tno --hra /path/to/human-reliability-toolkit --reviewed
python scripts/check_spatial_risk.py --prism /path/to/prism/lh2-tno --hra /path/to/human-reliability-toolkit
python examples/spatial_risk.py --prism /path/to/prism/lh2-tno --hra /path/to/human-reliability-toolkit --reviewed --consequence /path/to/retained_consequence.json
```

실제 결과를 신호로 내보내려면 `to_signal_frames`를 사용한다. 신호는 해당 셀의 평가창 시작 시점에
귀속된 연간화율이며 초당 변화량이나 연간 발생 확률이 아니다. `SignalFrame`의 실제 clock과
asset/scenario를 포함한 stream ID, person/year 또는 year^-1 단위를 유지한다. 계산된 위험의
신호 origin은 작성한 자료이면 synthetic, 그 외에는 simulation이다. 원래 measured/laboratory/
user_declared 출처는 결과 context에 그대로 보존하며 계산 결과를 실측값으로 표시하지 않는다.
부분 결과는 `allow_partial=True`를 명시해야 하고 모르는 채널의 값은 `None`, 유효성은 `False`다.

## 개발·검증 기록과 이어갈 일

먼저 기존 소스를 조사해 PRISM 기대 사망자 곡선과 HRA 정수 결합 F-N의 의미 차이를 확인했다.
이어서 공간 지지영역과 노출 계약을 만들고 실제 네이티브 API를 호출했으며, 작은 닫힌형 참조,
인원/면적/재실 변형, 미확인 지지영역, 빈도 누락 및 결과 변조를 검사했다.
`research/spatial_risk_results.json`에는 실제 실행 수, 기준의 종류, 원본 결과·코드 해시가 있다.
원자료가 필요한 전체 기록은 배포에서 제외된 `artifacts/spatial_risk`에 저장한다.
원래 조건부 공정 결과 적용은 위험 합산 연결의 증거이며 독립적인 물리 정답으로 세지 않는다.

원본 기록의 체크섬만 고쳐도 지도·네이티브 빈도 장부·F-N·면적·원본 인원·시계·소스 버전·
확률 의미·자격을 바꾼 결과는 재실행과 맞지 않아 거부한다. 실제 호출자 `request`와 `context`도
전달하면 원본과 정확히 비교한다. 해시는 서명이나 신뢰 기관의 인증이 아니므로 원자료 전부와
입력 요청 자체를 새로 작성한 사람의 사실 주장까지 판별하는 기능은 없다.

후속 소유 프로젝트 확장 요청은 다음처럼 구체화한다.

1. PRISM: 모든 공간 필드의 원점·CRS·수직 datum·셀/점 지지영역·유효 마스크·시점 및 확률 의미를
   네이티브 typed export에 포함하고, 미계산/외부 영역을 명시하는 API를 추가한다.
2. PRISM/HRA: 주변 인명 위해확률과 재실·인구의 검토된 결합 의존성 자료를 받아 정수 사상 분포를
   내보내는 소유 API를 검증한다. 기대 사망자 문턱과 실제 정수 exceedance를 별도 명명한다.
3. 인구 자료 어댑터: 원래 CRS를 보존한 검토된 좌표 변환과 면적 보존, 밀도/픽셀 인원/개별 인원/
   person-time 구분, 반올림 정책 없는 분수 인구 경로, 독립적인 노출 검증을 제공한다.
4. 현재 대표 셀 지도와 별개로 연속 등고선의 보간·마스킹·격자 세분화 오차를 검증한다.

작은 공개 후보인 [WorldPop Aruba2020](https://hub.worldpop.org/geodata/summary?id=6323)는
WGS84의 픽셀별 모델 추정 인구다. 소수의 people/pixel을 이 정수 인원 ENU 계약에 반올림해서
넣지 않았다. 실제 자료 취득, 좌표 변환과 현장 노출 적용·보정은 서로 다른 작업으로 남긴다.

현재 조사한 소유 코드의 출처는 [PRISM risk](https://github.com/lyullee/prism/blob/884b0553341beb2d51d1d3c563f86982f378e160/lh2-tno/src/lh2_tno/risk.py),
[PRISM expected-death curve](https://github.com/lyullee/prism/blob/884b0553341beb2d51d1d3c563f86982f378e160/lh2-tno/src/prism_qra/risk.py),
[HRA event-tree and integer F-N](https://github.com/lyullee/human-reliability-toolkit/blob/6617d36664a467dc3469905ed30bb2ae21366146/src/human_reliability/event_tree.py)다.
소유 프로젝트의 원래 MIT 고지와 버전·해시를 유지한다. 참조 코드와 자료의 권리 조건을 확인하는
절차가 모든 제3자 자료나 향후 배포의 법적 무문제를 보장하는 것은 아니다.
