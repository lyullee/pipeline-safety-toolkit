# 구리 합금 인장 원자료와 조건부 재료 계산

`copper_tensile.load_copper_tensile`은 구리 합금 인장시험의 LIS 기록을 원 순서로
읽는다. `scripts/check_copper_tensile.py`는 원 시험편 기하·보고 변형률을
기존 Mat의 인장 점 변환과 offset 교차 계산에 연결한다. 원 시계나 변형률을
고치지 않으며 단조 곡선의 분할과 표점 연장 재구성의 가정을 명시한다.

## 출처·범위·무결성

자료는 Hossein Beygi Nasrabadi·Felix Bauer·Ladji Tikana·Patrick Uhlemann·
Steffen Thärig·Birgit Rehmer·Birgit Skrotzki의
[KupferDigital 기계시험 자료 v2](https://doi.org/10.5281/zenodo.10820299)다.
공통 카탈로그의 검토된 CC BY 4.0 권리와 버전·저자·공급자 체크섬을 보존한다.
이 자료의 구리 합금 시험은 강관·볼트·용접부의 재료 자격으로 전이하지 않는다.

원 `Files.zip`은 1,608,805바이트이고 공급자 체크섬은
`md5:a6808ba63957e6724c0ab983ae67a9d6`이다. 이 실행에서 확인한 SHA-256은
`309d4f74e3fa4dab2623c0516b292386a0b0935dd3974e44663e012ca28442ce`다.
ZIP 전체에는 55멤버가 있으며 이 판독기는
`Tensile test/Primary data/` 아래의 LIS 20개만 인장시험 원자료로 선택한다.
같은 ZIP의 다른 시험이나 요약을 새로운 인장시험으로 세지 않는다.

공유 `source_archive.pinned_source_zip`이 허용된 취득·검토된 권리·공급자 크기와
체크섬을 먼저 확인한 뒤 제한된 메모리 snapshot으로 연다. 경로 탈출·중복
멤버·심볼릭 링크·암호화·멤버/전체 크기 초과를 거부하고 파일 시스템에
풀거나 공급자 코드를 실행하지 않는다. 각 LIS의 SHA-256도 별도로 남긴다.

## LIS 판독과 입력 API

```python
from pipeline_integrity.copper_tensile import load_copper_tensile

records = load_copper_tensile(
    "data/raw/copper_mechanical/Files.zip",
    maximum_records=100,
    maximum_rows_per_record=100000,
)
assert len(records) == 20
record = records[0]
record.check_integrity()
si = record.si_columns()
assert len(si["time_s"]) == len(record.data_rows) == len(record.source_line_numbers)
assert "machine_travel_m" in si and "reported_engineering_strain" in si
```

파일은 cp1252로 해독하며 `[Daten]` 블록 하나, 검토된 열 이름·단위와
탭 구분의 5열 행을 요구한다. 원 소수점은 쉼표다. 점 표기나 열·단위의
변경을 임의로 해석하지 않는다. 측정 블록의 빈 행은 제거하지 않고 오류로
처리한다. `NN`, 빈 수치 셀 등 검토한 미상 표기는 `None`으로 남긴다.

| 원 열 | 원 단위 | `si_columns()` 결과 | 변환 |
|---|---|---|---|
| `Zeit` | sec | `time_s` | 원 초 유지 |
| `Weg` | mm | `machine_travel_m` | ×0.001 |
| `Kraft` | kN | `force_N` | ×1,000 |
| `Dehnung` | % | `reported_engineering_strain` | ×0.01 |
| `Spannung` | MPa | `reported_engineering_stress_Pa` | ×1,000,000 |

`CopperTensileRecord`는 원 자산의 출처, 멤버 경로·해시, 시험편 ID·재료 표기,
판독한 모든 `metadata_rows`, 열·단위, 측정 행과 원 파일 행 번호를 담는다.
기하·기계/시험 설정·원 요약 값은 원 메타데이터로 유지한다. 파일명의
시험편 번호와 `Probenbezeichnung`이 다르면 거부하며, 서로 다른 시편을
같은 ID로 합치지 않는다.

`record.metadata(label)`은 같은 이름의 원 메타데이터가 정확히 하나일 때
단위·문자열을 반환한다. `record.quantity(label, expected_unit, scale)`은
원 단위가 정확히 맞는지 검사한 뒤 명시 배율만 적용한다. 값이 미상이면
형상이나 강종 대표값으로 대체하지 않는다. `check_integrity()`는 출처·
메타데이터·측정 행의 변조를 확인하며, 메타데이터 조회와 SI 열 생성에도
이 검사가 먼저 적용된다.

## 원 시계·부호·변형률의 보존

20개 물리 기록의 39,334행을 전부 보존했다. 인접 시각이 반복 또는 역전인
구간은 122개, 보고 변형률이 비증가하는 구간은 10,039개다. 여기서 비증가는
다음 값이 직전 값 이하인 경우이며 반복과 감소를 함께 센다. 음의 힘 표본은
195개, 음의 보고 변형률 표본은 20,064개이고 미상 수치 셀은 0개다.

이상 구간에서 시간 정렬·중복 제거·시각 압축·변형률의 누적 최대값·절댓값·
0 clipping을 사용하지 않았다. `Weg`는 기계 이동이고 `Dehnung`은 원 보고
변형률이다. 보고 변형률이 음수이거나 반전됐다는 이유로 기계 이동을 대신
사용하지 않는다. 기계 이동의 단위가 길이라는 사실은 표점 연장량과 같다는
근거가 아니다.

원 시각의 반복·역전 때문에 이 검토에서는 `SignalFrame`의 엄격한 단조 시계
이력으로 내보내지 않는다. `clock_id`는 시험편의 원 LIS 순서를 식별하고
실제 단조 경과 시계를 만들어내지 않는다. 시간 이력의 내보내기는 장치의
원 시계·리셋·동기화 근거와 명시한 수정 프로토콜이 확보된 뒤 별도로 검토한다.

## 선택한 인장 점과 표점 연장 재구성

각 시험에서 원 첫 행과 원 순서의 최대 힘 행을 명시적으로 선택한다.
이는 두 행을 독립 시험으로 만들거나 전체 곡선을 두 점으로 대체하는
규칙이 아니다. 원 전체 기록과 선택 인덱스·원 행 번호를 함께 남긴다.
같은 행이 두 번 선택되면 중복 호출하지 않는다.

초기 단면적은 `Probenquerschnitt`의 mm²를 m²로, 표점 길이는
`Anfangsmesslänge`의 mm를 m로 환산한다. 표점 연장은 다음과 같은 명시적
재구성이다.

```text
extension_m = reported_engineering_strain × source_gauge_length_m
```

이 값은 측정된 기계 `Weg`가 아니며 별도의 표점 변위 관측을 새로 얻은
것도 아니다. 원 보고 strain을 다시 같은 strain으로 변환하는 roundtrip
확인이다. 모든 점 요청은 `uniform_deformation_declared=False`로 둔다.
공학 응력·변형률만 계산하며 true 응력·변형률을 요청하지 않는다.

선택 행의 힘·변형률이 지원하는 비음 인장 조건을 만족하고 단면·표점 길이가
있을 때만 `materials.tensile_point`를 호출한다. 부호나 기하가 맞지 않으면
`needs_data`로 남긴다. 실제 선택 후보 40개 중 첫 행 20개는 계산됐고
최대 힘 후보 20개는 보류됐다. 최대 힘이라는 사실만으로 지원하는 표점
변형률이나 균일 변형을 확보했다고 가정하지 않는다.

계산된 공학 응력과 같은 source의 내보낸 공학 응력 차이는 최대 약
`4.66e-9 Pa`, 재구성 strain의 roundtrip 오차는 약 `6.78e-21`이었다.
둘 다 같은 원 기하·힘·보고 strain의 수치 대응 확인이며 독립 물성 측정이나
현장 정확성의 증거가 아니다.

## 원 연속 strain prefix의 offset 진단

원 첫 행부터 비음의 유한한 strain·stress가 유지되고 strain이 엄격히
증가하는 최초 연속 prefix만 선택한다. 결측·음수·반복·역전이 처음 나오면
그 앞에서 선택을 끝낸다. 그 뒤의 점을 다시 이어 붙이거나 더 긴 교차를
찾기 위해 원 곡선을 정렬·평활하지 않는다. 전체 기록은 그대로 유지하고
`prefix_end_exclusive`와 선택 규칙을 비공개 결과에 남긴다.

prefix가 적어도 3점이고 원 `Steigung des elastischen Teils (mE)`가 있으면,
이 보고 기울기를 GPa에서 Pa로 바꿔 진단용 `elastic_modulus_Pa`에 명시적으로
사용한다. 새 탄성계수 적합이나 기계 compliance 교정을 수행한 것이 아니다.
offset은 독립 선언한 분율 `0.002`이고 원 `materials.offset_yield`를 호출한다.

20개 호출 중 4개는 기록 prefix 안에서 교차를 계산했고 16개는
`needs_data`다. 제한된 prefix에 교차가 없다는 결과를 전체 시편에 항복이
없다는 결론으로 바꾸지 않는다. 성공한 경우에도 원 인접 bracket과 선택
범위, offset 직선과 계산 교차의 산술 일치만 확인했다. 최대 offset 직선
잔차는 약 `2.98e-8 Pa`였다.

원 `Dehngrenze (Rp0,2)` 내보내기 표기와의 차이도 별도로 기록하지만 같은
source의 요약 라벨은 독립 정답이 아니다. 원 장치의 요약 산출 규약·반올림·
분기 선택과 이번 진단을 자동으로 같다고 보지 않는다. 이 비교를 표준
항복시험 적합 판정이나 독립 모델 검증으로 세지 않는다.

## 재현·배포·후속 근거

```powershell
python scripts/acquire_data.py --only copper_mechanical --workers 1 --maximum-seconds 90
python scripts/check_copper_tensile.py --materials PATH_TO_MAT
```

다른 원 ZIP 위치는 `--archive PATH_TO_FILES_ZIP`으로 지정한다. 원 checksum·
자산명·라이선스 조건은 그대로 검사한다. 해석 계약은
[CHARACTERIZATION_KO](CHARACTERIZATION_KO.md)를 따른다.
[공개 실행 요약](../research/copper_tensile_results.json)은 원 ZIP·멤버와 코드
해시, 행·부호·시계 집계, 계산/보류 수와 산술 오차·한계를 담는다.

원 ZIP·LIS·전체 메타데이터·측정/변환 행·선택 행의 결과·receipt는
`data/raw/copper_mechanical/`와 `artifacts/copper_tensile/`에 로컬로 남기며
코드 배포에서 제외한다. Mat 소스는 읽기 전용으로 사용하고 실행 전후 소스
트리 해시를 확인한다. 스크립트의 조건부 계산 성공을 독립 시험·재료 모집단·
절차·현장 승인으로 해석하지 않는다.

이 자료만으로 대응한 노출 전후 연성 baseline이나 안정화 반복 모집단을
선정하지 않았다. `materials.ductility_retention`의 새 비교값과
`materials.ramberg_osgood`의 반복 계수는 추정하지 않는다. 다음 단계에는
기하·온도·표점·신율 정의가 맞는 baseline, 넥킹 구간을 지지하는 국부 면적·
변형 관측, 별도 반복 시험에서 보정한 계수와 독립 평가 모집단이 필요하다.

시계/strain 수정이 필요하면 원 상태를 보존한 채 장치 기록과 행 매핑,
단위·영점·표점 대응, 불확실성·수정 사유·버전을 갖춘 프로토콜을 먼저
확정해야 한다. 원 Mat의 부호 지원·다분기 선택·넥킹 해석을 확대하려면
현재 입력과 거부/보류 결과를 포함한 최소 재현 사례, 명시적 지원 범위와
기대 출력·검증 기준을 소유 프로젝트에 요청한다. 이 저장소에서 원 모델을
수정하거나 다른 재료식을 복제하지 않는다.
