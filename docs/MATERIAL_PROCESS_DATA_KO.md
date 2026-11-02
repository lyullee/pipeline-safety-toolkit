# 충격시험·수소 투과 원자료

`material_process_data.py`는 실제 자료의 시편·시계·단위와 미상 조건을 읽는다.
관측에 맞추어 원 모델의 계수를 추정하지 않는다. 두 자료군은 CC BY 4.0으로
공개되었고, 원본 5파일 1,620,960바이트의 공급자 체크섬을 확인했다.
이는 계산 인터페이스나 현장 재료 성능의 독립 검증과 구분한다.

## 출처·취득

| 자료군 | 버전·저자 | 선택 파일 | 바이트 | 권리 근거 |
|---|---|---|---:|---|
| BAM S355 강판 | 1.0.0, 2022, Schilling·Glaubitz·Matzak·Rehmer·Skrotzki | Charpy ZIP와 설명·추출도·제조사 선언 PDF 3개 | 1,532,075 | DOI 등록의 CC-BY-4.0 |
| MS-AHSS1300 수소 투과 | 1, 2025, Giuseppe Macoretta | XLSX 1개 | 88,885 | Mendeley 원 메타데이터의 CC BY 4.0 |

BAM의 원 출처는 [Zenodo 6778336](https://zenodo.org/records/6778336), 권리 근거는
[DataCite DOI 등록](https://api.datacite.org/dois/10.5281/zenodo.6778336)이다.
수소 투과의 원 출처는 [Mendeley tnsgf7v76z v1](https://data.mendeley.com/datasets/tnsgf7v76z/1),
권리 근거는 [원 snapshot](https://data.mendeley.com/public-api/datasets/tnsgf7v76z/snapshot/1)이다.
파일 크기·공급자 MD5 또는 SHA-256·로컬 SHA-256·버전·저자를 확인한다.
원 출처 페이지와 원 메타데이터, `license_evidence.json`은 해당
`artifacts/source_metadata/<dataset_id>`에 보존한다.

```powershell
python scripts/acquire_data.py --only bam_s355_charpy ahss_hydrogen_permeation --workers 2 --maximum-seconds 45
python scripts/review_material_process_data.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
```

원본은 `data/raw`, 읽은 원 셀·변환 행·상세 호출 기록은
`artifacts/material_process_data`에만 저장한다. 공개 코드와 집계 JSON에는 원자료,
원 논문·제조사 표, 관측별 변환 행, 상세 receipt를 넣지 않는다.
동일 자료를 다시 받으면 기존 공통 취득기의 크기·체크섬 검사를 통과해야 한다.

## S355 Charpy

`read_bam_charpy(folder)`는 두 원 protocol과 별도 결과표를 구분한다.
한 강판의 KX 3시편은 압연 방향, KY 3시편은 그에 수직한 방향이다.
원 protocol의 시험 온도 22.5°C는 295.65 K로 변환하며, mm는 m로 변환한다.
흡수 에너지는 원 J 값을 보존한다. 여섯 시편 모두 시험 중 분리 파단되지 않았으며
횡팽창의 원 수식 표기 `="-"`를 측정 0으로 바꾸지 않는다.

별도 결과표는 동일 여섯 실험의 반올림된 표현이다. 치수 대응 잔차는 0이며
원 에너지와 결과표 에너지의 최대 절대 차는 약 0.05 J다. 새 독립 관측이나
독립 검증 정답으로 세지 않는다. 결과표의 확대 에너지 불확실성 5.9 J와 k=2는
시험기 평가에서 보고한 값이며, 시편 간 반복성이나 전이모델 불확실성으로
재해석하지 않는다.

같은 묶음의 설명 PDF 4쪽, section 2는 제조사 선언의 조성이 시험한 강판의
생산 lot와 일치한다고 보장하지 않는다고 설명한다. 따라서 해당 선언의 상한표를
측정 조성으로 가져오지 않는다. `manufacturer_composition_tested_lot_match=None`,
`measured_composition_mass_fraction=None`을 유지한다. 명목 S355 이름에서 누락
원소를 0으로 채우거나 pitting index를 만들지 않는다.

시험 온도가 하나이고 여섯 결과가 모두 미파단이므로 상·하부 shelf, 천이 중앙
온도, 폭을 적합하지 않는다. 원조건의 조성 정합·pitting composition index·
impact transition 요청은 모두 `needs_data`다. 이는 자료가 무가치하다는 뜻이
아니라 해당 계산의 명시 입력이 확보되지 않았다는 뜻이다.

## MS-AHSS1300 수소 투과

`read_ahss_permeation(path)`는 두 시트의 전체 1,859행을 보존한다.
첫 시트 1,260행과 둘째 시트 599행은 각각 연속 전류 이력이다.
물리 시편 ID와 시트 간 독립성은 확인되지 않으므로 독립 시편 두 개로 세지 않는다.
시트가 보고한 293 K, 시간 s와 총 전류 μA를 읽고 총 전류만 A로 변환한다.
원시 시각의 시작점을 농도 경계의 투입 시각으로 옮기지 않는다.
정렬·보간·baseline 차감·정류·미상값의 0 대체를 하지 않는다.

원 방법은 명목 50×50×1.2 mm 시편, 양극면의 얇은 Pd층, 0.1 M NaOH를
명시한다. 시트 이름의 음극 전류밀도 양끝은 0.5→1 및 5→10 mA/cm²이며
SI로는 각각 5→10 및 50→100 A/m²다. 이 양끝만으로 시간별 구동 파형을
복원하지 않는다. 명목 외형 면적은 셀의 유효 투과 면적을 제공하지 않는다.
총 검출 전류를 면적당 몰 유량이나 표면 lattice 농도로 변환하지 않는다.

원 lattice 확산계수, entry 농도, 초기 농도·trap 에너지와 site 밀도,
흡수·탈착 속도계수가 제공되지 않았다. 유한 두께 1.2 mm를 자동으로
반무한 영역으로 선언하지 않는다. 각 이력의 trap equilibrium·semi-infinite·
slab kinetic 요청 3개씩, 합계 6개도 `needs_data`로 유지한다.

## 검증·범위

형식 변경 시 조용히 단위나 열을 추정하지 않는다. 원 크기와 공급자 해시,
sheet 이름·순서, 핵심 헤더·단위·시편 대응을 검증한다. SHA-256 record digest는
원 셀·조건·변환값을 함께 묶으며 변경된 레코드는 거부한다. 합성 단위검사는
미상·음의 전류·중복/역전 시각을 보존하고, 파일 손상·권리 미검토·단위 변경을
거부하는지 확인한다. 실제 파일을 읽는 재현 스크립트는 별도로 실행한다.

실제 재료 품질 경계 3요청과 수소 경계 6요청, 합계 9요청은 모두 `needs_data`다.
검토·물리 입력이 부족하므로 원 엔진의 계산 호출은 0회이고 영수증은 `None`이다.
이 보류 경계는 실제 파일에서 읽은 조건·source digest를 context에 붙여 실행했다.
선택한 원 스냅샷의 Python 트리 해시도 실행 전후 같았다. `model_boundary_review`에
원조건 요청 수·상태를 기록하며, 상세 요청과 조건은 로컬 artifacts에만 남긴다.

공개 집계는 `research/material_process_data_results.json`이다. 검토한 두 자료군은
기존의 문헌 종합 fracture transition 자료군이나 copper 기계시험 ZIP과 다르다.
다만 이 자료 취득이나 보류 요청을 독립 물리 검증·강종 적합성·수소 환경 수명·
시험 절차 승인으로 집계하지 않는다.
