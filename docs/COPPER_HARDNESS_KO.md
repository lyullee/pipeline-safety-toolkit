# 공개 구리 합금 압흔 자료의 경도 계산 연결

이 안내서는 공급자의 Brinell·Vickers 일차 보고서를 판독해 기존 재료 소유자의
`testing.brinell`과 `testing.vickers`에 연결하는 방법을 설명한다. 원조건의
미상과 명시적으로 가정한 기하 계산은 별도 결과다. 두 결과가 함께 있어도
원 표면 준비나 실제 재료 상태가 확인된 것으로 바뀌지 않는다.

자료는 [KupferDigital v2, DOI 10.5281/zenodo.10820299](https://doi.org/10.5281/zenodo.10820299)이며
저자는 Hossein Beygi Nasrabadi, Felix Bauer, Ladji Tikana, Patrick Uhlemann,
Steffen Thärig, Birgit Rehmer, Birgit Skrotzki다. 공급 자료의 이용 조건은
CC-BY-4.0이다. 이 문서·판독기·검사는 독립 작성했으며 원 보고서의 표나
규격 허용값을 복제하지 않는다. 실제 실행 집계와 소스 해시는
[연구 결과](../research/copper_hardness_results.json)에 있다.

## 확보한 자료와 적용 범위

공급 ZIP `Files.zip`은 1,608,805바이트다. 공식 메타데이터 MD5
`a6808ba63957e6724c0ab983ae67a9d6`와 크기를 공통 `pinned_source_zip`으로
확인한 뒤 읽는다. SHA-256은 공개 집계의 `source.archive_sha256`에 있다.
공통 검사에는 경로·중복·암호화·심볼릭 링크·압축 해제 예산 검사도 포함된다.
XLSX 내부에는 별도 부품/바이트 예산을 적용하고 외부 관계, 매크로, XML
엔티티를 거부한다. 파일을 추출하거나 수식을 실행하지 않는다.

| 구분 | 일차 보고서 | 압흔 행 | 원 모델 |
|---|---:|---:|---|
| Brinell | 11 | 61 | `testing.brinell` |
| Vickers | 12 | 66 | `testing.vickers` |
| 합계 | 23 | 127 | 두 원 모델 재사용 |

이 작업은 해당 일차 보고서만 읽는다. ZIP의 이차 요약 자료를 별도 관측으로
더하지 않으며 인장 곡선은 별도 판독기의 범위다. 같은 시편 문자 표시는
출처의 그룹 단서다. 방법 간 같은 물리 시편, 인장 시편과의 대응, 독립 표본
관계를 자동으로 확정하지 않는다.

23개 보고서 모두 표면 준비 칸이 `NN`이다. 합금명은 있으나 실제 열처리·
가공·조직 상태를 확정하지 못한다. 날짜는 날짜 문자열로 보존하며 시간대,
UTC 시각, 압흔별 경과시간으로 확장하지 않는다. 장비·압자·온도·유지시간과
시편 표시는 원 헤더와 행 번호를 붙여 보존한다. 보고서의 규격 식별자는
출처 메타데이터이며 절차 적합성 확인이나 허용 기준의 근거를 대신하지 않는다.

## 실제 열과 단위

| 출처 필드 | 보존/변환 | 계산에서의 의미 |
|---|---|---|
| `Prüfkraft F` | 숫자와 원 단위 N 보존 | `test_force_N`; 명목 하중으로 대체하지 않음 |
| `Einwirkdauer der Prüfkraft` | s | 검토 조건의 `dwell_s` |
| Brinell `Kugeldurchmesser` | mm → m | `ball_diameter_m` |
| Brinell 행 `d` | mm → m | `mean_indentation_diameter_m` |
| Brinell 행 `d1`, `d2` | 원 mm 값 보존 | 기록 평균을 덮어쓰는 데 사용하지 않음 |
| Vickers 행 `d1`, `d2` | µm → m | `first_diagonal_m`, `second_diagonal_m` |
| Vickers 행 `d` | 원 µm 값 보존 | 반올림된 평균을 두 대각선에 복제하지 않음 |
| `Eindringwinkel` | 원 값과 비어 있는 단위 칸 보존 | 선택한 Vickers 기하의 136 표기 검토 필요 |
| 행 `HBW` 또는 `HV ` | 원 보고 경도와 단위 문자열 보존 | 재계산 결과와의 기술적 비교 전용 |
| `Bemerkung` | 빈 값도 포함해 보존 | 행 삭제/보정의 근거로 자동 사용하지 않음 |

Brinell 보고서의 힘은 612.916 N이다. Vickers에는 49.3 N과 49.03 N이
함께 있다. `HV 5` 표기로부터 5 kgf를 계산해 이 값을 대체하지 않는다.
길이 변환은 원 열의 명시 단위가 일치할 때만 한다. 알 수 없는 단위나
누락된 측정값은 계산 입력 미상으로 남는다. 관측된 0·음수·잘못된 수치형은
오류이며 정상 관측이나 미상 0으로 바꾸지 않는다.

Vickers 12개 보고서에는 요약부의 방법 표제와 실제 Vickers 열/단위가
어긋나는 경우가 있다. 판독기는 `summary_method_label_conflict`로 이를
기록한다. 개별 원행은 일차 폴더·테이블 열·단위·검토에 따라 연결하며,
해당 표제를 조용히 고쳐서 일관된 원자료인 것처럼 만들지 않는다.

## 판독 API와 JSON 구조

`load_copper_hardness(archive)`는 일반 JSON 사전으로 된 봉인 기록을 반환한다.
선택 의존성 `openpyxl`은 이 함수의 실제 XLSX 판독 중에 불러온다. 모듈
import만으로 XLSX 의존성이나 Mat를 요구하지 않는다.

상위 `pipeline_integrity.copper_hardness_source.v1`에는 `provenance`, `tests`,
`record_digest`가 있다. `provenance`에는 DOI를 포함한 citation, 권리,
공급자 체크섬과 `checksum_origin=publisher_metadata`, 검증 여부, ZIP 해시가
포함된다. 시험마다 다음 정보를 갖는다.

- `schema=pipeline_integrity.copper_hardness_test.v1`, `method`, `member`, `sheet`.
- `workbook_bytes`, `workbook_sha256`, 원 헤더/단위 행 번호와 문자열.
- `metadata`: 선택한 시험/시편/조건 필드의 `source_row`, `source_header`,
  `value`, `unit`, `state`. 교정/규격 허용값 표 전체를 가져오지 않는다.
- `rows`: ZIP 내 보고서 순서와 시트 내 행 순서를 유지한 압흔 기록.
- `grouping_basis`, `summary_method_label_conflict`, `record_digest`.

행의 `schema`는 `pipeline_integrity.copper_indentation.v1`이다. `row_id`는
원 ZIP 멤버와 물리 행 번호를 결합한다. `source_row`와 `cells`를 보존하며
셀 이름은 `provider_indent_id`, `d1`, `d2`, `source_mean`,
`reported_hardness`, `notes`다. 각 셀은 값과 상태를 함께 가진다.
`observed`, `missing`, `unresolved_placeholder`, `unevaluated_formula`,
`invalid_nonfinite`, `unsupported_type`은 서로 다르다. 수식은 수식 문자열로
보존하고 계산하지 않는다. 완전히 빈 스타일 행은 압흔으로 세지 않는다.
값이 있는 압흔 행을 정렬·평활·보간·제거하지 않는다.

각 `record_digest`는 내용의 SHA-256이다. 평가 시 시험과 모든 내부 행의
봉인을 확인하므로 행 변조 후 외부 시험 해시만 다시 계산해도 검출한다.
이 해시는 보관 중 일관성 검사이며 공급자 전자서명은 아니다. 원 ZIP과
공급자 체크섬을 함께 보관해야 출처를 다시 확인할 수 있다.

## 원조건 보류와 별도 가정 진단

`evaluate_copper_indentation(test, row_id, review=None, *, client=None)`는
선택한 원행을 새 경도 요청에 연결한다. `review=None`이면 Mat import 없이
원조건의 `needs_data` 결과를 만든다. 이 자료에서 미확인 실제 재료 상태와
표면 준비를 명목 합금명이나 보고서 존재만으로 채우지 않는다.

선택적으로 `CopperGeometryReview`를 제공하면 원조건 결과와 **별도**
가정 진단이 함께 생긴다. 다음은 모든 필드다.

| 검토 필드 | 요구 내용 |
|---|---|
| `expected_test_digest` | 검토한 시험 기록의 해시 |
| `expected_row_digest` | 검토한 바로 그 압흔 행의 해시 |
| `units_and_column_mapping_reviewed` | 원 힘·길이 단위와 방법별 열 대응 검토 |
| `force_dimensions_dwell_and_method_reviewed` | 하중·치수·유지시간·방법 근거의 검토 범위 선언 |
| `geometry_model_reviewed` | 구면/다이아몬드 기하의 적용성 검토 |
| `diagnostic_limitations_reviewed` | 미상 조건·가정·비자격 진단의 한계 검토 |
| `declared_material_condition_id` | 가정 시나리오의 명시 재료 상태 ID; 원 관측 아님 |
| `declared_surface_condition_id` | 가정 시나리오의 명시 표면 상태 ID; NN 대체 관측 아님 |
| `assumption_evidence` | `EvidenceRef`; 출처는 `synthetic` 또는 `user_declared`만 허용 |

4개 검토는 기본 false다. 검토를 채워도 누락된 힘·압흔·유지시간·단위를
가정으로 보충하지 않는다. 두 가정 ID가 미상이면 진단도 `needs_data`다.
가정 근거를 `laboratory`나 `measured`로 표시하면 거부한다. 원 근거와
가정 근거의 ID는 달라야 한다. 명시한 Vickers 각도가 선택 기하와 다르거나
Brinell 압흔이 구의 지름 범위를 벗어나면 검토 진단은 `out_of_scope`다.

반환 `pipeline_integrity.copper_hardness_application.v1`은 `source_test_digest`,
`source_row_digest`, `row_id`, `review`, `source_condition_result`,
`declared_assumptions_geometry_diagnostic`와 봉인을 가진다. 원조건 결과는
항상 따로 남고 가정 진단의 context origin은 `simulation`이다. 원 NN,
재료명, 날짜, 행 값은 수정하지 않는다. `original_observation_modified`,
`field_or_standard_qualification`,
`reported_hardness_comparison_is_independent_validation`은 false다.

두 내부 경도 결과의 정확한 요청·단위·원 receipt 설명은
[경도 원 API 안내](HARDNESS_KO.md)를 따른다. Mat 소유의 `MaterialJob`과
원 receipt, 소스 pin, SI 입력, context와 근거 digest가 유지된다. 출력은
`indentation_hardness_Pa`와 `conventional_HBW` 또는 `conventional_HV`다.
HBW/HV의 `kgf/mm^2` 표현과 Pa를 함께 보존한다. 계산을 강도·취성·균열
확률·합격 여부로 자동 변환하지 않는다.

## 실행 예제

아래 코드는 이 저장소 루트에서 공급 ZIP을 취득한 뒤 실행할 수 있다.
첫 예제는 원조건 보류를 확인하며 Mat가 필요 없다.

```python
from pathlib import Path

from pipeline_integrity.copper_hardness import (
    evaluate_copper_indentation,
    load_copper_hardness,
)

source = load_copper_hardness(Path("data/raw/copper_mechanical/Files.zip"))
test = source["tests"][0]
row = test["rows"][0]
result = evaluate_copper_indentation(test, row["row_id"])
assert result["source_condition_result"]["status"] == "needs_data"
assert result["declared_assumptions_geometry_diagnostic"] is None
assert test["metadata"]["Probe Vorbereitung"]["value"] == "NN"
```

실제 두 모델 적용은 읽기 전용 Mat 소스를 지정한다. 스크립트는 필요한
`src` 경로를 추가하고 bytecode 쓰기를 막은 뒤 원 소스의 전후 해시를 비교한다.

```powershell
python scripts/acquire_data.py --only copper_mechanical
python scripts/check_hardness.py --materials PATH_TO_MAT
python scripts/check_copper_hardness.py --materials PATH_TO_MAT
python -m pytest -q tests/test_hardness_exchange.py tests/test_copper_hardness.py
```

원/변환 행과 전체 원 receipt는 `artifacts/copper_hardness`에만 쓴다.
배포되는 `research/copper_hardness_results.json`에는 집계·출처·권리·해시를
기록한다. 개인 경로나 원 관측 행을 공개 집계에 넣지 않는다.

## 확인한 증거와 남은 자격

실제 127원행은 전부 원조건 `needs_data`였다. 별도로 명시한 가정 시나리오의
127기하 진단은 두 원 모델에서 계산됐다. 구면 cap의 고정밀 Decimal 면적과
다이아몬드 면 기하/중력 단위로 독립 작성한 254산술 기준의 최대 상대오차는
약 3.89e-16이다. 원 ZIP과 Mat Python 소스의 실행 전후 해시가 같았다.

보고서 HBW/HV와 재계산한 값의 차이도 기록했다. 이는 같은 관측을 다시
계산한 재현 비교다. 최대 차이는 각각 약 0.07234 HBW와 1.45765 HV였으며
규격 허용오차를 적용하거나 차이의 원인을 추정하지 않았다. 명목 하중,
평균 반올림 또는 방법 표제만으로 원값을 고쳐 일치시키지 않는다.
산술 기준과 이 비교는 실험실 독립 검증·교정·현장 calibration을 뜻하지 않는다.

후속 적용에는 표면 준비·실제 재료 상태, 계측 교정과 불확실성, 압흔 위치·
간격·가장자리/두께 적용성, 물리 시편 대응, 적용 절차와 사용자 수용 규칙의
근거가 필요하다. 새 근거가 있으면 기존 원행과 해시를 유지한 채 별도 검토
시나리오로 제출해야 한다. 원값을 가정값으로 덮어쓰지 않는다.

`welding.hardness_traverse`는 다른 위치 trace와 구역/불확실성/수용 요구를
가진 기존 소유 API다. 이 자료의 시편 문자와 압흔 순서를 용접부의 좌표·
모재/HAZ/용접금속 구역으로 추정하지 않는다. 연결하려면 해당 trace의
방법·하중·유지시간·위치·시계·확장불확실성·구역·미측정 영역을 별도로
확인하고 사용자가 검토한 규칙을 제출해야 한다. 자동 HBW↔HV 등가도 없다.

추가 지원은 원 소유 모델의 계약으로 요청한다. 입력 단위, 방법/압자·재료
지원 범위, 조건 근거, 원 자료 권리, 독립 평가 자료와 수용 기준을 명시해야
한다. 현장 절차 자격이나 불완전한 시험 자료를 수치 계산 성공으로 마감하지 않는다.
