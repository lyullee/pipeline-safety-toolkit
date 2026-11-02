# 공개 비파괴 검사 자료와 초음파 교환

TUBERS 자료군의 공급자 API에서 MIT 이용 조건과 두 ZIP의 크기·MD5를 확인하고
원본을 취득했다. 출처는 [Zenodo의 버전 고정 자료](https://zenodo.org/records/11239514)이며
작성자는 University of Essex 및 TWI ELLAS ASTIKI MI KERDOSKOPIKI ETAIREIA다.
소스 배포에는 원본·변환된 관측을 넣지 않는다. 출처·권리 검토·체크섬·집계 결과와
재취득 명령을 보존한다. 원 설명 문장·그림·계수는 가져오지 않았다.

## 취득과 재현

```powershell
python scripts/acquire_data.py --only tubers_nde --workers 1 --maximum-seconds 180
python scripts/review_nde_data.py --materials PATH_TO_MAT
python -m pytest -q tests/test_nde.py
```

MatIntegrity는 별도로 설치한다. 원본과 공급자 메타데이터는 각각 data/raw와 로컬
artifacts에 남긴다. 공개 실행 요약은 research/nde_data_review.json이다. CSV 판독과
영상 대응 색인은 표준 라이브러리와 핵심 계약만 사용하며 이미지 픽셀을 디코딩하거나
외부 프로그램을 실행하지 않는다. 두께 계산을 요청할 때만 원 재료 엔진을 호출한다.

## 확인한 원본 상태

| 자료 | 확인 내용 | 적용 한계 |
|---|---|---|
| 초음파 CSV | 231파형, 434000표본, native second/Volt | 영점·소재·교정·물리 에코 정체는 미문서화 |
| Plate_1 | 59파형, 90000표본, 음수 시각 3844개 | 음수 시각을 0으로 이동하거나 삭제하지 않음 |
| Plate_2 | 164파형, 328000표본 | 폴더 주파수 표기를 표본 간격이나 독립 시편으로 바꾸지 않음 |
| Wedge | 8파형, 16000표본, 비증가 인접 시각 4곳 | 파일명 두께를 검토된 정답으로 사용하지 않음 |
| 영상/마스크 | 파일명 대응 318쌍, 각 250×250 px | 짝 영상이 없는 마스크 7개, 물리 크기·클래스 값 미확정 |

전압 결측은 232개이며 모든 마지막 불완전 표본도 원 행과 함께 보존한다.
시각·행의 정렬, 보간, 중복 삭제, 좌표 이동으로 상태를 숨기지 않는다. 영상 전체의
대응 상태는 needs_data이고, 존재하는 318쌍만 표시했다고 전체 자료가 검증된 것은
아니다. 소스 폴더는 그룹 라벨이며 독립 시편이나 독립 실험을 증명하지 않는다.

자료 제목은 합성 시각 자료를 포함한다고 명시한다. 파형의 수치 단위는 알려져
있으나 개별 물리 취득·교정의 상세는 제공된 설명만으로 확인하지 못했다. 판독기는
acquisition_origin과 zero_reference를 not_documented로 유지한다. 영상·신호 파일 수를
POD의 독립 기회 수로 세지 않으며, 마스크를 사람의 검출/미검출 응답으로 바꾸지 않는다.

## 파형 판독 API

```python
from pipeline_integrity.nde import load_tubers_waveform, tubers_visual_pairs

trace = load_tubers_waveform(archive_path, member_name, expected_archive_sha256=reviewed_source_sha256)
audit = trace.audit()
index = tubers_visual_pairs(visual_archive_path)
```

NDEWaveform은 signed time_s, voltage_v, source_rows, invalid_cells와 원 ZIP/멤버
해시를 보존한다. 알 수 없는 헤더·단위·열 개수는 거부한다. 비유한/잘못된 수치는
원 토큰과 행을 남기고 None으로 표시한다. 전부 빈 레코드는 별도 원 행 목록에
기록하며, 부분 결측 표본과 구분한다. 동일한 raw 멤버는 같은 native_clock_id를
가지며 원 시각 0의 물리 의미를 추정하지 않는다. 무결성 검사는 원 관측과 근거가
변경된 객체의 재사용을 막는다.

영상 색인은 원 Images/Masks 파일명을 사용한다. 누락한 쌍과 대응하지 않는
마스크를 함께 반환한다. PNG의 IHDR 크기 일치만 확인하며 픽셀 값, 마스크의
의학적/공학적 의미나 물리 축척을 검증했다고 주장하지 않는다. 파일을 맞추기 위해
번호·크기를 고치지 않는다. ZIP의 중복 이름·경로 이탈·과도한 멤버 크기는 거부한다.

## 검토한 에코 구간의 재료 API 전달

```python
from pipeline_integrity.nde import EchoThicknessRequest, evaluate_echo_thickness

result = evaluate_echo_thickness(trace, reviewed_request, context)
```

EchoThicknessRequest에는 trace digest, 기준/반사 표본의 0기준 index, 음속(m/s),
시간 영점 보정(s), 입사각(rad), 원본·에코 분류·교정 근거 ID를 입력한다. context의
clock_id는 해당 파형의 native_clock_id와 같아야 하며 원본 근거의 SHA-256도 원 ZIP과
일치해야 한다. 음수 원 시각을 그대로 두고 두 표본 사이의 시간 차이를 구한다.
이는 공정의 절대 시각 또는 사람의 반응 시간이 아니다.

선택한 구간 안에 전압/시각 결측, 생략된 원 레코드, 증가하지 않는 시각이 있으면
needs_data이며 원 엔진을 호출하지 않는다. 구간 밖 결측을 채우거나 전체 파형을
일률적으로 승인하지 않는다. 명시적으로 검토한 구간과 해석만 원
inspection.ultrasonic_thickness에 전달한다. 원 입력·상태·시계·근거와 출력 m 단위를
확인하고 원 결과 전체를 보존한다. 보정이 비행 시간을 소진하면 원 out_of_scope를
유지한다. Python 객체를 전달해도 bool을 음속 등 숫자로 정규화하지 않고 재검증한다.

실제 원 파형으로 5개 교환 사례를 실행했다. 기준 표본 0/200의 간격 1µs에 음속
5900m/s·보정 0·각도 0을 가정하면 2.95mm이며, 보정 0.2µs는 2.36mm, 각도 π/3은
1.475mm다. 이 수치는 **가정한 에코·물성의 수치 연결 기준**이다. 실제 판 두께를
측정/검증했다는 뜻이 아니다. 에코와 소재를 자동 판별하지 않으며 field_calibrated와
physical_echo_identity_automatically_verified는 false다.

## POD와 후속 연구

이번 파일에서는 독립적인 정답 크기·시편 ID·검출/미검출/허위 지시 표본을 확인하지
못해 POD 적합 상태는 needs_data다. ILI에 검출 곡선을 옮기거나 검사원 HEP를
추정하지 않았다. PI-X04에 물리 크기·소재·음속/영점·에코 분류·취득 시계·반복
시편·정답/미검출·허위 지시와 데이터 그룹의 수용 계약을 추가했다.

방법 후보로 [AFRL의 POD Pro 4.5](https://github.com/AFRL-Material-State-Awareness/POD4.5)를
확인했다(2026-10-27). 별도 데스크톱/R 기반 구현이며 GPLv3+ 조건을 갖는다. 현재
패키지에 코드를 가져오거나 설치하지 않았다. 기존 Mat의 확률 계산 책임을 유지하고,
추가 회귀 방법과 독립 비교는 시편·관측 설계가 갖춰진 후 소유 엔진에서 평가한다.
