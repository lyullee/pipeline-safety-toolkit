# 공개 배수망 누수 자료와 역추정 연결

자료 원본은 [Zenodo 10.5281/zenodo.7255403](https://zenodo.org/records/7255403)에서
별도로 취득한다. 저장소에는 원본 archive를 넣지 않는다. 현재 자료 원장은
`research/data_inventory.json`, 실제 파일 재고는
`research/water_network_archive_inventory.json`이다. 데이터 라이선스는 CC BY 4.0으로
기록되어 있다. 재사용 시 인용을 유지하고 데이터와 출판물 및 별도 복제 코드의 권리를
분리한다.

압축본은 공급자 MD5와 SHA-256으로 고정되어 있고, reader는 archive 경로를 추출하거나
pickle을 역직렬화하지 않는다. 재고에는 ZIP 구조 82항목 중 실제 데이터 파일 22개가
기록된다. CSV 10개는 원 헤더와 전체 행 폭, 열별 빈칸 수를 검사한다. 공개 인벤토리에는
원 관측 행을 복사하지 않는다.
시뮬레이션 pickle 9개는 불투명 자료로 남겨 둔다. 파일명은 진실 레이블로 간주하지 않는다.

세 가지 모델/자료 묶음에서 원 문자열 ID를 대조하면 HPC_run_08과 HPC_run_05의 pressure
logger 37개 및 현장 이벤트 누수 ID 9개가 각 INP 파일의 `[JUNCTIONS]`와 `[COORDINATES]`
절에 모두 존재한다. HPC_run_04에서는 각각 39개 센서 및 8개 이벤트 ID가 일치한다.
이는 소스 간 정확한 식별자 겹침이지, 계측기 설치·현장 좌표계·좌표 단위나 시계 동기의
검증은 아니다. 자동 교차참조 결과는 원자료 inventory JSON에 남긴다.

## API

```python
from pathlib import Path

from pipeline_integrity.water_network_data import (
    build_water_network_inverse_bridge,
    inspect_water_network_archive,
    read_water_network_pressure_window,
)

archive = Path("data/raw/probabilistic_leak_localization/data.zip")
inventory = inspect_water_network_archive(archive)
window = read_water_network_pressure_window(
    archive,
    member_name=("data/raw/BKTown/pressure_meas/mobile_pressure_data-08122020_15022021-UTCp01h00.csv"),
    sensor_ids=("1130816", "1239222"),
    start_timestamp="2020-12-16 09:00:00",
    end_timestamp="2020-12-16 09:10:00",
)
```

`inspect_water_network_archive`는 공급자 체크섬을 확인하고 CSV 형식을 훑는다.
`read_water_network_pressure_window`는 허용된 두 압력 로그 중 하나와 명시한 센서 열·
시작/끝 시각(양 끝 포함)만 선택한다. 시각은 원본의 naive local 문자열로 보존한다.
`UTCp01h00` 파일명만으로 시간대 의미를 선언하지 않는다. 반환 레코드는 archive와
member 해시, source row, raw 문자열 값을 포함한다. 빈 셀은 `None`이다. inverse 연결 전
`verify_water_network_pressure_window(window, path=archive)`로 현재 archive를 재읽어
원본 바이트·센서 선택·raw 관측을 replay한다.

역추정 연결에는 먼저 각 센서에 대해 호출자가 검토한 물리 port, 채널, measurement
basis와 근거를 제공한다.

```python
from pipeline_integrity.metrology import MeasurementBasis
from pipeline_integrity.water_network_data import WaterHeadSensorBinding

bindings = {
    "1130816": WaterHeadSensorBinding(
        channel_id="head_1130816",
        physical_port_id="reviewed_node_1130816",
        basis=MeasurementBasis(
            quantity="pressure_head",
            unit="m",
            definition_id="reviewed_water_head_definition",
            evidence_id="dataset_and_quantity_review",
        ),
        mapping_evidence_id="sensor_port_review",
    ),
    # 선택한 나머지 센서에도 같은 수준의 명시적 mapping을 제공합니다.
}
```

`build_water_network_inverse_bridge(window, event_origin_timestamp=..., sensor_bindings=...)`는
이 근거를 사용해 기존 `inverse_localization.LocalizationRow`들과 매핑·clock·source 근거를
보존하는 sealed receipt를 만든다. `bridge.verify_digest()` 뒤 `bridge.rows`를 기존 요청에
전달한다. 원본 시각에서
호출자가 정한 naive 기준 시각까지의 차이를 초 단위로 계산하며, 기준시각이 측정보다 뒤면
거부한다. 날짜 해석·시간대 변환·head-to-pressure 변환·고도 datum·센서 port 연결은
추측하지 않는다. 숫자 변환에 실패한 값은 raw 레코드에 그대로 남고 inverse 입력에서는
무효값으로 표시된다. 출력 최대 512행을 넘기면 window나 센서 수를 줄여야 한다.

## 후보 좌표 연결

EPANET 좌표값도 source file에 기록된 x/y 숫자이며 원 archive만으로 CRS, 축 순서의 현장
방향, 원점, 좌표 길이 단위를 확정하지 않는다. 명시적으로 선택한 run과 후보 node만
원문자로 읽고 원 `member_sha256`와 reader hash를 묶는다.

```python
from pipeline_integrity.water_network_data import (
    WaterNetworkCoordinateTransform,
    read_epanet_coordinate_record,
    to_water_network_location_candidates,
    verify_epanet_coordinate_record,
)

coordinates = read_epanet_coordinate_record(
    archive,
    run_id="HPC_run_08",
    candidate_node_ids=("1239007", "1240389"),
)
verify_epanet_coordinate_record(coordinates, path=archive)

reviewed_map = WaterNetworkCoordinateTransform(
    source_frame_id="caller-reviewed-source-frame",
    source_coordinate_unit_id="caller-reviewed-source-length-unit",
    target_frame_id="caller-reviewed-site-frame",
    linear_m_per_source_unit=((1.0, 0.0), (0.0, 1.0), (0.0, 0.0)),
    offset_m=(0.0, 0.0, 0.0),
    evidence_id="coordinate-transform-review",
)
candidate_set = to_water_network_location_candidates(
    coordinates,
    transform=reviewed_map,
    candidate_id_by_node={"1239007": "candidate_1", "1240389": "candidate_2"},
    location_id_by_node={"1239007": "site_location_1", "1240389": "site_location_2"},
    physical_port_id_by_node={"1239007": "model_node_1", "1240389": "model_node_2"},
    evidence_id_by_node={"1239007": "source_node_1", "1240389": "source_node_2"},
    candidate_parameters_by_node={
        "1239007": {"leak_parameter": 0.1},
        "1240389": {"leak_parameter": 0.1},
    },
)
candidate_set.verify_digest()
```

행렬은 source x/y를 target의 3차원 metre 좌표로 보낸다. 예제의 단위 행렬은 코드 사용법만
보이는 값이며 실제 데이터에 적용할 좌표계 사실이 아니다. 두 축의 rank가 부족한 행렬은
거부한다. 모든 candidate node에 candidate/location/physical-port/evidence/parameter mapping이
필요하며, 파라미터 key 집합은 일치해야 한다. 각 node에서 parameter value가 다르면 후보가
위치와 고정 파라미터를 함께 바꾸는 복합 가설임을 인식해야 한다. 2–256 후보만
`inverse_localization.LocalizationRequest.candidates`에 전달할 수 있다.

후보 변환 기록을 확인한 뒤 역추정 요청을 만들 때는 아래 근거들을 `RunContext.evidence`에
모두 추가한다: 데이터셋 인용 및 archive/member 해시, 좌표 변환 검토 근거,
`candidate_set.candidates`의 각 `evidence_id`, 관측 bridge의 각
`sensor_bindings[*].mapping_evidence_id`, 요청에 선언된 모든 basis와 review 근거.
각 caller 근거 ID는 실제 검토 자료를 가리키는 `EvidenceRef`로 제공한다. `bind_water_network_localization_context(request, bridge, candidate_set, ...)`는 request rows/candidates/source hash와 두 sealed receipt의 일치, 동일 archive SHA 및 필수 근거 ID를 검증한 뒤 정확한 요청 digest·asset·clock을 담은 `RunContext`를 반환한다. `dataset_evidence_id`의 EvidenceRef에는 고정 archive SHA-256과 CC-BY-4.0을 기록하고 `origin`은 압력 관측의 실측/시뮬레이션 의미를 검토해 지정한다. 관측 basis, 센서 port, 좌표 변환, 각 후보 및 요청 review를 설명하는 모든 EvidenceRef를 전달한다. 이 결속을 생략한 context는 기존 inverse API가 거부한다.

생성한 행을 위치 판정으로 쓰기 전에 기존
[`inverse_localization`](INVERSE_LOCALIZATION_KO.md) 계약이 요구하는 완전한 공동 공분산,
후보 형상과 고정 파라미터, 센서 clock·port 리뷰 및 기존 원 물리엔진 예측 provider를
별도로 설정한다. 제공된
현장 이벤트 표의 날짜·시각·위치 라벨은 압력 시계에 자동 정렬하지 않는다.

두 source receipt를 기존 요청에 조립한 뒤 `bind_water_network_localization_context`에 전달한다. 이 함수는 archive/member SHA, 요청에 넣은 행과 후보, 동일 archive, EvidenceRef ID를 대조하고 `inverse_localization_boundary` 및 source receipt digest를 `RunContext.metadata`에 기록한다. dataset EvidenceRef는 archive SHA-256·CC-BY-4.0을 가져야 하며, `origin`과 mapping/basis/review EvidenceRef는 사용자가 검토해 제공한다.

## 적용 한계

- 공개 자료는 모의 수두와 현장 누수 실험을 합친 연구 자료다. 무작위 행 분할 대신
  실험·network 경계를 유지한다.
- 현장 이벤트 표의 `Datum`, `Beginuur`, `Einduur`, `Locatie`, `3GE FID`, `Opmerkingen`은
  원 셀 그대로 보존한다. 관측 시계와 동기·시간대가 확인될 때까지 압력 로그와 join하지
  않는다.
- 모델 노드 ID와 센서 asset ID의 문자 일치만으로 실제 설치 위치·고도·계측 오프셋을
  입증하지 않는다. 실제 현장 매핑 근거를 caller가 결합해야 한다.
- 측정 head의 baseline, 표본 지원, 계측 지연, 결측 메커니즘, 측정 오차·모델 불일치의
  공동 공분산은 archive reader가 추정하지 않는다.
- 이 연구 자료는 빠른 압력 과도파 도착 시간 정답, leak 발생 빈도, 일반화 성능 또는
  현장 안전 판정을 제공하지 않는다.
- 연결 함수는 owner inverse API용 관측행을 만든다. 수리 엔진을 복제하거나 자동 후보를
  보간하지 않으며, 결과는 독립 누수 위치 확인과 구분한다.

## 검증 및 재현

`tests/test_water_network_data.py`는 원자료의 경계 조건을 본떠 작성한 authored fixtures를
사용한다. 현재 정적 검사와 `py_compile`만 수행했으며 이 테스트 파일은 아직 실행하지
않았다. 테스트는 최신 validation queue가 가리키는 누리온 targeted manifest에
누리온 2-shard targeted 캠페인으로 준비되어 있다. 실행 성공·물리 타당성·현장 자격을
주장하려면 새 source-only bundle과 이 manifest를 사용해 실제 원자료 전송 및 HPC 결과를
별도 확인해야 한다.
