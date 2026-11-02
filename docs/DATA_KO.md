# 데이터 확보·권리·적용 기록

초기 검토는 2026년 10월 26일, GasLib·TUBERS·결합 탱크·봉재 실험 추가 검토는 10월 27일이다. `research/data_inventory.json`은 취득한
파일명·크기·SHA-256·공급자 체크섬 여부·실패를 보유한다. 전체 후보와 이용
조건·재취득 URL은 `catalog_data/datasets.json`에 있다. 링크 공개와 재배포
허락을 구분한다.

네트워크 다운로드와 사용자가 제공한 기존 파일의 복사/등록은 모두
`require_acquisition_allowed`를 통과해야 한다. `metadata_only`, `deferred_capacity`,
미해결 권리 검토 자료는 로컬 파일이 이미 있다는 이유로 취득 경계를 우회할 수 없다.

각 새 파일 취득 receipt에는 선택 자산의 원본 카탈로그 필드와 전체 선택 자산
메타데이터를 함께 저장하고, 각 객체의 정렬 JSON UTF-8 표현에 대한 SHA-256을
계산한다. 이는 취득 시점의 출처·버전·권리 검토 및 예상 체크섬을 나중에 catalog
변경과 구별하기 위한 provenance이며, 라이선스 법률 검토나 재배포 허가를 대신하지
않는다. `research/data_inventory.json`은 해당 snapshot과 두 digest를 유지한다.
이 변경 전에 만들어진 과거 receipt에는 당시 snapshot이 없을 수 있으며 현재
catalog로 이를 소급 복원하지 않는다. `verify_acquisition_provenance`는 legacy
receipt의 부재는 구분해 두되, snapshot 일부 누락·변조 또는 본문 receipt와의 불일치가
있으면 research inventory 생성 단계에서 오류로 중단한다.

배포 코드의 `datasets.inspect_zip`은 ZIP을 풀지 않고 검사한다. 중복 경로(대소문자만
다른 충돌 포함), 비정규 경로, 암호화 항목, 심볼릭 링크 및 특수 파일은 거부하고
파일/디렉터리 종류를 재고에 기록한다. 따라서 이 목록은 멤버 메타데이터이며 압축을
풀어서 CRC 전체를 읽었다는 뜻은 아니다. 취득한 원본 바이트와 공급자 체크섬 검증은
별도로 원장에 남긴다. `research/zip_ingestion_status.json`과 누리온 대기 회귀를 참조한다.

`datasets.inspect_rar`는 지원되는 `7z/7zz` 또는 `tar` 목록 도구로 경로·종류·크기와 링크를 검사하지만 압축 해제/CRC 검사/CSV 검증은 별도다. percussion 원본 RAR은 publisher SHA-256으로 확보를 확인했고 3,849 CSV member 목록을 읽었다. 멤버 수준 내용을 전부 읽었다는 뜻은 아니다.

현재 집계는57후보·49자료군·252파일·11,047,623,204바이트다. 아래 최초 표와 이후
추가 기록은 각 단계의 확보 상태이며 최신 원장은 `research/data_inventory.json`을 따른다.
water-network 자료 reader 및 기존 역추정 API 연결 계약은
[별도 자료 안내](WATER_NETWORK_DATA_KO.md)에 기록했다.
WDSEventDB 센서 고장·누수·사이버 이벤트의 source-pinned 원본 reader와 의미 한계는
[별도 자료 안내](WDS_EVENT_DATA_KO.md)에 기록했다.
유사한 Mendeley 누수 시험 등록본은 [content crosswalk 안내](LEAK_DEPOSIT_CROSSWALK_KO.md)와 `research/leak_crosswalk_integrity_status.json`에서 출처 메타데이터 일치와 미완료 파일 내용 해시 비교를 구분한다. 대형 RAR의 조건/위치/반복 파일 구조와 단일 CSV 선택 reader는 [배관 타격 실험 안내](PERCUSSION_DATA_KO.md)를 따른다.

## 확보한 자료

| ID | 내용·생성 방식 | 파일 수 | 바이트 | 이용 조건 |
|---|---|---:|---:|---|
| hydraulic | 유압 시험설비 다중 센서·실험 주기 | 1 | 76,601,704 | CC BY 4.0 |
| metropt3 | 철도 압축기 운전 관측 | 1 | 218,381,995 | CC BY 4.0 |
| gas_turbine | 발전 설비 공정·배출 관측 | 1 | 1,090,294 | CC BY 4.0 |
| water_treatment | 수처리 공정 관측 | 1 | 39,385 | CC BY 4.0 |
| ai4i | 합성 예지정비 자료 | 1 | 522,170 | CC BY 4.0 |
| secom | 반도체 공정 품질 자료 | 1 | 1,964,989 | CC BY 4.0 |
| tep | Tennessee Eastman 모사 실험 | 4 | 1,402,950,911 | Harvard 공개 도메인 기여 조건 |
| battledim | 가상 물 관망 모사·관측 신호·정답 | 14 | 188,866,507 | CC BY 4.0 |
| corrosion_images | 부식 이미지 | 1 | 3,017,772 | CC BY 4.0 |
| potash_elbow_cp_slurry | 포타시 brine·모래 슬러리에서 AISI 1018 엘보 보호전류 실험 | 1 | 34,410 | CC BY 4.0; [적용 한계](POTASH_ELBOW_CP_DATA_KO.md) |
| ili | 여러 검사 시점의 배관 외면 특징 | 1 | 333,456,657 | CC BY 4.0 |
| elvhys_bleve | 액체수소 파열·압력파 실험 수치/메타데이터 | 42 | 534,609,893 | CC0 1.0 |
| h2safe | 실내 수소 방출·감지 실험과 보조 메타데이터 | 2 | 4,459,232 | CC BY 4.0 |
| sheffield_acoustic | MDPE 배관의 동시 가속도·누설 실험 | 1 | 492,926,144 | CC BY 4.0 |
| gaslib11 | 시험용 정상 기체 관망·장치·지정 운전 XML | 1 | 7,022 | CC BY 3.0 |
| tubers_nde | 초음파 원 파형·합성 시각 이미지/마스크 | 2 | 46,281,895 | MIT |
| coupled_tank | 두 원통형 탱크의 액위·펌프 명령 실험 | 6 | 286,911 | CC BY 4.0 |
| thermal_rod | 봉재 가열·분포 대류 실험의 원 CSV/보조 자료 | 1 | 2,099,328 | CC BY 4.0 |
| crack_arrest | C1/C2 균열 정지 시험의 원 하중·변위·sample 시계 | 1 | 943,565 | CC BY 4.0 |
| wds_event_db | 실험 물 관망의 누수·센서 고장·사이버 이벤트 계측 | 1 | 2,265,766 | GitHub 저장소 CC0 1.0; [reader 한계](WDS_EVENT_DATA_KO.md) |
| 합계 | 19개 자료군 | 83 | 3,310,772,140 | 자료별 조건 유지 |

UCI의 여섯 묶음은 기존 로컬 캐시의 SHA-256과 재확인한 원출처 조건을 사용했다.
해당 SHA-256을 공급자가 발표한 체크섬이라고 표시하지 않는다. TEP와 Zenodo
파일은 공급자 MD5도 확인했다. ILI는 공급자 체크섬이 없어 취득 후 SHA-256과
ZIP 목록을 기록했다. 이 차이는 원장에 별도 필드로 남는다.

원출처: [UCI 유압설비](https://archive.ics.uci.edu/dataset/447/condition+monitoring+of+hydraulic+systems),
[MetroPT-3](https://archive.ics.uci.edu/dataset/791/metropt+3+dataset),
[TEP](https://doi.org/10.7910/DVN/6C3JR1),
[BattLeDIM](https://doi.org/10.5281/zenodo.4017659),
[부식 이미지](https://doi.org/10.5281/zenodo.10809418),
[반복 ILI](https://doi.org/10.17632/c2h2jf5c54.1).
각 자료의 작성자·버전·정식 인용은 카탈로그를 따른다.

## 취득하지 않은 후보

실험 누설 및 부식 타격 자료는 라이선스를 확인했지만 서버 생성 압축 응답이
지연됐다. 동압 파일 개별 URL도 60초 실행 한도 안에 완료되지 않았다. 실제
취득한 자료군에 포함하지 않는다. 캐나다 사고 자료는 OGL Canada 2.0을
확인했으나 원본 CSV가 HTTP 403을 반환했다. PHMSA도 공개 메타데이터를 확인했으나
다운로드가 403으로 거부됐고 모든 원자료의 재배포 권한을 일괄 인정하지 않았다.

LeakDB 대형 묶음은 공식 Zenodo API에서 13,888,061,642 bytes, CC BY 4.0, 공급자 MD5
`d607929ef7caea432e663aee8071f2d7`로 확인했다. 사용자가 요청한 대용량 자료 수동 전달
기준에 따라 아직 내려받지 않았으며, 제공 선택을 기다린다. 배포 코드의 EUPL과 자료의
CC BY 조건은 별도 권리다. 파일 메타데이터는 `research/data_inventory.json`에 기록했다. SWaT는 별도 접근 동의가 필요하고, HAI의
버전별 조건, SKAB의 데이터 이용 조건, 비상업 조건이 붙은 합성 부식 자료는
현재 자유 재배포 가능한 자료로 취급하지 않는다. Water Pilot 후보는 원신호가
아닌 파생 이상 점수이므로 물리 상태 검증 자료로 쓰지 않는다.

## 적용한 판독기

BattLeDIM은 압력 **수두 m**, 유량 m³/h, 탱크 수위를 읽는다. 유량만 m³/s로
변환하고 동일한 시각인지 검사한다. 원시각은 현지 시각이므로 검증된 시간대가
없을 때 경과 시간만 사용한다. 세 파일의 37개 채널, 864개 표본을 확인했다.
수요·누설 정답은 검출기의 입력 열에 넣지 않는다.

공급 관망은 생성 관망의 정확한 복제물이 아니다. 782개 접점·905개 관·1개
펌프·3개 밸브·1개 탱크를 1일 해석하고 질량수지 잔차를 확인했다. 관측 신호와
명목 관망의 수치 일치를 독립 검증으로 주장하지 않는다.

유압 주기는 압력 100 Hz, 유량 10 Hz, 온도·진동 1 Hz로 서로 다르다. 한 주기를
60초 독립 실험으로 유지한다. L/min→m³/s, °C→K, mm/s→m/s를 명시 변환한다.
압력 기준이 공개 메타데이터로 확정되지 않아 bar 단위를 별도 의미로 보존한다.
상태 레이블은 신호 입력에서 제외한다.

ILI의 4개 통합문서에서 15개 열과 총 3,198,420개 데이터 행을 실제로 읽었다.
초기 범위 정보 확인에 이어, 시트의 행 수 메타데이터를 재설정하고 XML 끝까지
읽어 같은 범위를 확인했다. 기본 적용은 용접부 14번의 13·22·12·16개 관측이다.
전체 읽기와 전체 배관의 정합 검증은 다르다. 원본에 포함된 R 코드는 실행하지 않았다.

`load_ili_run`은 mm를 m로, 원주각을 rad로 바꾸고 이음부 내 상대거리를 유지한다.
절대거리 원점은 회차마다 달라 기본 정합에서 제외한다. 검사 라벨 1·3·5·7은
실제 설비 나이·초 단위 시각으로 추정하지 않는다. 특정 좌표의 단위 설명이
충돌하므로 그 좌표도 쓰지 않는다. 실제 관측의 시각·측정 오차·동일 결함 경로
검토가 없어 성장 계산은 보류한다. 좌표 등록·대응 후보·재료 함수 연결의 상세와
재현 명령은 [반복 검사 문서](INSPECTION_KO.md)를 따른다. `load_ili_run`의 report는 선택된 원 source row 번호, 유효 feature의 source row 순서, 선택 행 목록 SHA-256 digest를 함께 남긴다. `maximum_source_rows` 예산으로 scan이 조기 종료되면 `scan_stop_reason`을 `maximum_source_rows_reached`로 명시하고 `source_scan_complete=false`로 유지한다. report는 원 feature 값을 복제하지 않으며 archive/workbook SHA와 run의 source_row로 선택을 다시 추적한다. archive는 기본 5 GB 입력 예산 안에서 regular non-symlink 파일이어야 한다. 해시 계산과 ZIP member 읽기는 같은 열린 파일 핸들을 사용하고, 읽기 종료 시 inode/device/size/mtime이 바뀌면 결과를 반환하지 않는다. `verify_ili_load_record`는 report digest와 선택 행 집합·유효/invalid feature 행·run source hash·scan completeness를 대조하고 원 archive를 전달하면 다시 읽어 같은 report/run인지 확인한다.

## 추가 수소 실험의 적합성 검토

[ELVHYS](https://doi.org/10.18710/M5LOLM)는 2026년 10월 21일 공개된 V1의
수치·텍스트 42개 파일을 공급자 MD5와 대조했다. 전체 자료의 영상은 취득하지
않았다. 초기 병렬 취득의 네트워크 실패를 기록한 뒤 순차 재시도로 선택 파일
전체를 확보했다. 열·상변화·파열 실험은 일반 단상 관망의 성능 증명이 아니다.

첫 실험의 고속 압력은 262,144행이며 실제 시간 간격 중앙값은 10 μs다.
설명의 10 kHz와 맞지 않아 파일 시간을 따른다. 일부 압력의 기준 표기와
온도 0 K, 반복 시각도 발견했다. `elvhys_table`은 원값·invalid·불명확한 기준을
보존하고 `iter_elvhys_frames`는 반복 시각을 조용히 제거하지 않는다. 음수인
사건 상대 시간을 Frame으로 옮기려면 사용자가 공통 시간 오프셋을 명시해야 한다.

[H2SAFE](https://data.openei.org/submissions/8799)는 2026년 10월 5일 공개 자료로
실험 5개와 센서 좌표·형상·운전 메타데이터를 확보했다. CSV의 신호 단위와 시간
단위는 명시돼 있지 않다. 보조 정보에는 방출·환기 조건과 미터 단위 누설 좌표가
있으나, psi의 기준압과 표준 리터의 기준 상태는 확정되지 않는다. 원신호의
결측·실험별 전부 결측인 채널을 보존하며 물리 모델 입력을 승인하지 않았다.
연결된 소프트웨어 URL은 검토 당시 404였다.

센서 좌표가 같은 프레임이라는 설명과 보조 도면을 확인했지만 좌표 단위의
간접 추론과 ENU 변환의 자격을 구분한다. 신호 이름 p1을 압력으로 해석하지
않는다. 실제 판독·결측·표기 충돌·해시는 `research/hydrogen_data_review.json`에 있다.

## 학습·교정·검증 분할 원칙

실험 자료는 독립 주기/배관/실험 조건으로, 공정 시계열은 시간과 운전 모드로,
TEP는 run과 fault 시나리오로 분할한다. 같은 고장의 인접 표본을 무작위로
훈련·검증 양쪽에 넣지 않는다. BattLeDIM 2018 전체가 정상이라는 가정을 하지
않는다. 초기 3일을 이용한 연동 실험은 훈련·교정·실행을 각 288행으로 나눴으나
누설 검출 정확도나 미래 오경보율의 평가가 아니다.

파생 파일을 공유할 때도 원자료 라이선스와 변경 내용을 따른다. 현재 공개
배포에는 원관측·변환 관측·대형 바이너리를 넣지 않고 출처·해시·집계만 넣는다.

## 공개 관망의 선언된 품질 시나리오

성분 수송은 취득한 L-TOWN 토폴로지를 사용하되 농도·물 나이·출처 비율의
초기/경계 조건은 자체 합성 입력으로 선언했다. 공개 SCADA를 수질 관측으로
사용하지 않는다. 결과는 원자료 출처와 합성 파라미터 출처를 구분한다.
세 mode의 수송 수지는 통과했으나 수력 반복 초과 경고로 보류했다.
`research/water_quality_results.json`에 집계만 공개하며 전체 시계열은 artifacts에 둔다.

## 동시 음향 실험 자료

Sheffield CID MDPE 관 실험은 Mohammad Reza Shekofteh (2026), University of
Sheffield, DOI 10.15131/shef.data.32229270.v1, CC BY 4.0으로 고지한다. 공급자
MD5와 원본 SHA-256을 유지한다. 원본에는 63개 파일이 있고 두 센서 동시 CSV는
40개다. 시계는 8192Hz, 가속도 열은 m/s²이며 전파속도·정밀 시계 오차·참고
센서의 정확한 누설 오프셋은 확정하지 않았다.

여러 실험을 모은 전체 거리 행렬을 동시 파형으로 쓰지 않는다. 초기 4초
분석과 한 기록의 7개 창 적용은 `research/acoustic_results.json`에 집계했다.
전체 파형·지원 설명/도식·창별 변환 결과는 공개 배포에서 제외한다. 정책
통과를 누설 위치 정확도나 비누설 오경보 성능으로 표시하지 않는다.

## GasLib 시험 관망 추가

2026-10-27에 gaslib11 원본 ZIP 1개·7022바이트를 취득했다. 원 XML의 CC BY 3.0
표시와 Schmidt 등의 DOI 10.3390/data2040040, Pfetsch 등의 ZIB Report 12-41
인용 요구를 보존했다. 공급자가 시험 자료로 분류한 형상·정상 지정 운전이며
실제 관측·인적오류 기회 자료가 아니다. 이 단계 당시 후보는 24개, 취득은 14자료군·
72파일이었다. 원 압축기·밸브·조성·열·유량/압력 기준의 매핑은 미완료다.

Python HTTPS의 인증서 체인 검증 실패 뒤 OS 기본 검증의 Invoke-WebRequest를
사용했다. 검증을 끄지 않았고 취득 SHA-256·ZIP 구성과 재취득 원장을 남겼다.
고정 값은 출판사 체크섬이 아니다. 형상 판독과 별도로 선언한 한 관 사례는
GAS_NETWORK를 따른다. 원 XML·SVG·장치 구성은 공개 배포에 포함하지 않는다.

## TUBERS NDE 추가 검토

공급자 API의 MIT 조건과 MD5/바이트를 확인한 두 ZIP을 추가 취득했다. 작성자는
University of Essex 및 TWI ELLAS ASTIKI MI KERDOSKOPIKI ETAIREIA이며 버전은 v1,
출처는 https://doi.org/10.5281/zenodo.11239514다. 231파형에는434000표본·232전압
결측·4개 비증가 인접 시각이 있고, 영상은318쌍과7고립 마스크다. signed 시각을
변환하지 않았으며 원 파일명의 물리 두께/주파수나 픽셀 크기를 교정으로 사용하지
않았다. 독립 검출/미검출 시험은 needs_data다. NDE_DATA의 판독·원 재료 교환 및
research/nde_data_review.json을 확인한다. 전체 취득은25후보·15자료군·74파일,
3305176570바이트다. 원 CSV·영상·변환 표·원 설명 문장은 배포에 넣지 않는다.

## 결합 탱크 실험의 자료 적합성

[Silva·Coelho·Leite의 결합 탱크 자료](https://doi.org/10.5281/zenodo.19856119)는
v1.0.0, CC-BY-4.0이다. 출판사 API의 조건·작성자·파일 MD5를 확인하고 6파일
286911바이트를 취득했다. h1/h2 각각 81행·10실험, U 80행, 전체 수집 4502행을
읽었다. U의 마지막 시각은 없으며 보간하지 않는다. 각 실험 상대 시계는 분리하고
원 약 4초 간격의 작은 변동을 유지한다. 펌프 명령 %를 유량으로 해석하지 않는다.

운영점 JSON은 파손돼 있으며 읽을 수 있는 h3/h4 mm와 README h1/h2 cm의 관계가
미확정이다. 명목 용량 200L만으로 내부 직경·높이와 액위 영점을 확정하지 않는다.
따라서 source_status는 needs_data다. cm/mm·영점·원본·각 근거를 명시하면
공통 SignalFrame으로 연결할 수 있다. 원 단위 선택·두 형상·물 상태 가정의
3240개 재고 계산은 별도 연구 조건이며 실제 설비 교정·물리 수지 검증이 아니다.
TANK_INVENTORY와 research/tank_inventory_results.json에 계약·해시·공백을 기록했다.
원 CSV·README 문장·파라미터·변환 관측은 공개 아카이브에 포함하지 않는다.

## 결합 탱크 관측의 제어 명령 재생

기존6파일을 재사용했다. 추가 취득으로 집계하지 않는다. 원10개 실험의 각각
첫 시각은 초기 판단 기준으로 쓰고 이후80개, 합계800개의 shadow PID 명령을
계산했다. cm·영점·목표0.474m·이득은 선언한 연구 가정이며 원 pump %나 실험의
반응을 바꾸지 않았다. source_status=needs_data와 laboratory origin을 유지하고
계산 출력은 simulation으로 구분했다. 각 단위 매핑 receipt와 h1.csv 해시,
원 상대 시계는 로컬 상세 기록에 남는다. 원81행의 모니터링/HRA 교환도 확인했다.
이 적용은 실제 제어 성능·고장률·동기화/교정 검증이 아니다. CONTROL_EXCHANGE를 따른다.

## 봉재의 비정상 가열·대류 자료

[Tomanek·Stutts의 Mendeley V1](https://doi.org/10.17632/sbcf36976g.1), CC-BY-4.0의
원 ZIP2099328바이트를 이전 취득SHA-256과 대조했다. 공급자 체크섬이라고 부르지
않는다. 원 reader를 재사용해77CSV215001행의 모든 숫자와28161개의 음수 초과
온도를 독립 CSV 판독과 비교했다. 여섯 TC는 주변 온도 대비 차이이고 제어기
카운트는 W/열유속이 아니다. source fitted 결과는 같은 관측에서 추정한 값이다.

1937행 run을 실제 모니터링/HRA로 전달했다. explicit ambient/geometry/properties/
sensor-face 가정21점 열 경계는 독립 예측 검증이 아니다. 실제 센서 위치·형상·
ambient·열 입력 효율/재료 상태 공백은 needs_data다. 원/변환 행과 fitted표는
배포에서 제외하며 작성자·버전·권리·원/member 해시·단위 의미를 보존한다.
계약과 재현은 MATERIAL_SECTIONS를 따른다.

## 원시 균열 정지와 별도 metadata 후보

[Puyi Gao의 C1/C2 V1](https://doi.org/10.17632/g69875w65c.1)은 CC-BY-4.0이며
943565바이트를 이전 취득SHA와 대조했다. 공급자 체크섬이 아니다. 원 reader를
재사용해18CSV79341행과 SI 변환값을 모두 대조했다. Negative force76285개·machine
변위79341개·extensometer75250개, 원 sample clock/cycle과 한 header 표현 결함을
보존했다. Filename 온도는 실제 온도 계측 이력이 아니다.4631표본의 모니터링/HRA
교환도 확인했다. 기하·균열 길이·동적 reduction·유효성은 미확정이라 source
needs_data며 KIC/Paris계수/실제 dynamic arrest/파손확률을 추정하지 않는다.

[Meshii 등 V1 전이구간 compilation](https://doi.org/10.17632/2vkgrbsy8j.1)은
출판사 수준 CC-BY-4.0과 training/testing 설명만 이번 프로젝트에서 재확인했다.
원 관측별 lineage·물리 독립성·단위/정의/조건·재배포 조건은 미검토다. metadata_only
후보로 기록하고 archive/row를 취득하거나 service 인성/성장계수에 쓰지 않았다.
원/변환 시험 관측은 공개 배포에서 제외하며 FRACTURE_EXCHANGE의 원 해시·권리·
형상/하중/저항 정의를 유지한다.


## 기존 공개 하중의 구조 API 적용

추가 파일을 확보한 것으로 세지 않는다. crack_arrest의 검토한 archive와 원
판독기에서 한 시편4631행을 읽고 처음32행의 force_N을 가정한 축 프레임
하중에 연결했다. 32개 모두 음수이며 절댓값·정렬·리샘플링 없이 원 시각과
SI 값을 대조했다. 원 machine force는 laboratory, 계산된 프레임 변위/반력은
simulation이다. 원 시험의 구조 치구·동적 유효성·파괴 기구를 검증하지 않았다.
상세 source frame/결과는 artifacts/structural, 공개 집계는 structural_results에
남기고 기존18자료군·82파일과 CC-BY-4.0 credit을 유지한다.


## 열 원자료의 기계적 조건부 적용

기존 thermal_rod50cu3의1937행 중처음16행의 tc1 excess K와 원 시각을 유지했다.
실제 원 ambient/초기 응력은 결정하지 않았다. 가정한 프레임의 ambient와
stress-free 온도를293.15K로 별도 선언하고 상수 α로 uniform fixed-frame
반력을 구했다. source/laboratory와 predicted/simulation을 구분하고 원 신호·
정의·source hash를 상세 비배포 기록에 남겼다. 실제 봉재 응력·열 계수의
독립 검증이나 새 파일 취득으로 세지 않는다. 원 authors·CC-BY-4.0 credit은
유지한다. 공개 집계는 research/frame_loading_results.json이다.


## EPS1의 관측 구간 적분과 재무 가정

기존 hydraulic archive를 이전 취득 SHA로 대조하고 EPS1의0·100·1100·2204
회차를 읽었다. 4×6000개 값을 원 ZIP row와 비교했다. 각 원 시계0~59.99초를
독립 trapezoid와 대조하고 60초 회차의 마지막0.01초는 채우지 않았다. 실제
SignalBatch/SignalSeries에 원값·시간·laboratory origin을 전달했다.

별도 REF요금·100000회 duty·배출/credit 가정 아래 비용·CO2e로 연결했다.
이 항목을 두 대안에 동일한 추가 항목으로 적용하고 실제 효율·정비·수명·투자
차이 검증으로 쓰지 않았다. 반복 시험의 profile을 가격·연간 빈도·독립 현장
검증 label로 승격하지 않는다. 원본/가공 표본은 비배포이며 새 자료 취득으로
집계하지 않는다. 전체29후보·18자료군·82파일을 유지한다.

## 유압 회차의 전력·유량 공동 적용

check_uncertainty.py는 UCI447의 EPS1/FS1 원 수치 행을 각각 네 회차에서 읽고
같은 회차 짝을 유지한다. 100Hz 전력과 10Hz 유량의 공통 구간 [0,59.9]초만
각 원 시계로 적분해 에너지/체적을 계산한다. 마지막 전력 구간이나 유량의
끝 이후를 채우지 않는다. 선정한 네 사례의 0.25씩 비중은 선언 시나리오다.
16개 교차 조합은 명시적 반사실이며 모집단/독립성 추정이 아니다. 회차별 변환
값과 원 기록은 artifacts/uncertainty에만 남기고 공개 요약은 출처·집계·해시를 담는다.

## 원 조건별 분포 적합 자료

check_uncertainty_fit.py는 UCI447의 EPS1/FS1 전체2205회차를 줄 단위로 읽어 원
파일 해시·공통59.9초 구간의 에너지/체적 짝을 유지한다. 첫 동일 조건 블록210행은
128학습/18미사용/64평가로 고정한다. flag1은 정적 상태 미확정이고 독립성이
확보된 시계가 아니다. 안정 프로필의 축압기 라벨90은 열화 조건19행이며, 모든
부품이 정상인 안정 프로필은10행이다. 두 경우 모두 기존 최소30/10을 충족하지
못해 보류했다. 원 행·변환 값·개별 점수는 artifacts에 두고 공개 요약/그림에는
집계·출처·해시·남은 한계만 포함한다.

## 도금 전기화학과 보호 전류 후보

Zenodo13886478 v3의 워크북4개/README1개315521바이트를 원 캐시에서 재사용하고
공급자MD5·로컬SHA를 확인했다. DOI 등록메타데이터도 CC-BY-4.0으로 확인했다.
Tafel24요약의 전해질0.1M HCl/H2SO4·5%NaCl, signed cathodic slope·mA/cm2와
Ω·미상 면적/전극 기준·불특정±를 보존한다.24개 면적 정규화는 보류하고12개
비도금 전류 추정은 가정Fe2+ 시나리오만 실행했다. 전기화학 단계 당시 자료군은31후보/19취득/87파일,
3308821895바이트였다. potash CP V2는 공개 CC-BY4 메타데이터를 검토했지만 HTTP403으로
원 관측을 얻지 못했다. 코드 배포물에는 원/변환 관측·공급자 문장·아카이브를 넣지 않는다.

## CMSX-6 크리프의 시편·검열과 처리 이력

BAM의 단결정 Ni계 CMSX-6 자료(DOI10.5281/zenodo.14750432)를 원 판독기에 적용했다.
Ávila Calderón·Schriever·Han·Olbricht·Dolabella Portella·Skrotzki의 CC BY 4.0 자료다.
DOI 등록 버전1.1과 workbook 파일명 v1.0을 구분해 카탈로그를 정정했다.
DataCite 권리 캐시 SHA·출처·버전은 research/creep_methods.json에 기록했다.

workbook1개와LIS12개의13파일5,470,578바이트를 공급자MD5/로컬SHA로 확인했다.
12시편은9파단·3시험 제한시간 우측 검열이다.1253.15K 한 온도와 초기 명목응력
140/170/200/230MPa를 유지했다. 원 stress-only 수명 적합은 실행됐으나 온도 의존
적합은 자료 부족이다. 독립 시편/공통 기구 가정은 연구 선언이며 현장 자격은 아니다.

처리 이력140032행의 시간·변형률·온도 수치를 대조하고 반복 시각23구간을
보존했다. 하중 부여 종료 시계와 시편/파단 구분은 CREEP_KO를 따른다. 마지막
이력 행을 파단으로 세거나 이력 행을 독립 시편으로 만들지 않는다. 원본/변환
행은 코드 배포에서 제외한다. 크리프 단계 당시32후보·20취득 자료군·100파일·3314292473바이트였다.

## PE100RC 압력감소·온도 이력

Miloš Mičian·Radoslav Koňár의 DOI10.5281/zenodo.20582921,2026-06-07 v1 자료를
추가했다. DataCite CC BY4.0·Zenodo 공급자MD5와 대조해 workbook1개78721바이트를
취득했다. 권리/metadata 캐시 해시는 research/pressure_tests_methods.json에 있다.
현재33후보·21자료군·101파일·3314371194바이트다.

실제 파일은5기체×2모세관 조건의10이력·각31행,총310행이다. 별도 무결함 기준/
주변온도 채널과 체적/대기압은 파일에서 확인하지 못했다. 보고 온도가 대표
기체 온도인지도 미해결이다. 공급자 설명만으로 부족한 열과 시험을 만들지 않는다.
실제 누설 경로 유량/합격 판정은 보류한다. 지정 물성의8가정 시나리오(기준 Air
모델 포함)와 조성 미상 PB2보류·음의 순 유출5구간을 PRESSURE_TESTS_KO에 기록했다.
원/변환 관측은 artifacts에만 두고 코드에는 독립 설명·집계·출처를 제공한다.

## Tandem-GMAW v2와 EBW 열 이력

BAM Tandem-GMAW의2026-08 v2(DOI10.5281/zenodo.21824586)를 이전v1 대신 고정했다.
Prashant Kumar Chaurasia·Çağtay Fabry·Andreas Pittner·Michael Rethmeier의 CC BY4
자료다. WID631의L1/L2 ZIP과experiment_list.csv,3자산231713903바이트를 공급자MD5와
대조했다. 전체30pass를 확보했다고 주장하지 않는다. NetCDF/HDF는 h5py3.16.0으로
읽으며 스캔·비디오·notebook을 실행하거나 코드 배포에 넣지 않는다.

4원기록은696814전기행·280590로봇행이다. 각 전기 채널1NaN,4ms/1ms 시간 공백,
음의 U×I를 보존했다. thermo_2의 절대영도 미만60/89값과 다수의 좌표 결측도
수리하지 않았다. 명시한100ms창4개에서 기존 signed integration과 원 숫자 기준을
대조했고, 미상 실제 거리/열효율로 전체pass 입열은 계산하지 않았다. UTC 원점
변환은 기록 좌표의 일치이며 하드웨어 시계 동기/드리프트의 검증이 아니다.

별도 EBW DOI10.17632/c7d9mt9h9x.1(Sisodia·Śliwiński·Weglowski,CC BY4)의 원ZIP
368314바이트를 공급자SHA256과 대조했다. 사용자 전달 wrapper와 내부 원본을
구분하고 원본만 취득 원장에 기록했다.5실험19781시간행·7센서25416온도 표본을
보존했다. 유일한 실제 냉각 bracket7개와 전체 동시 band5사례를 원 엔진으로
계산했다. 파일명 label과 실제 계산의 차이, S1300Q filename/S1300QL sheet의
두 불일치를 남겼다. 두 자료를 동일 용접의 다른 채널로 결합하지 않는다.

현재35후보·23자료군·105파일·3546453411바이트다. 원관측·변환행은 data/artifacts에만
있고 공개 연구에는 집계/출처/검사/해시를 제공한다. 상세한 보류·선택 정책과
권리는 WELDING_KO 및 research/welding_methods.json을 따른다.

## 경보 재생과 사건·노출 자료 공백

기존 BattLeDIM2018의 첫864행을 재사용했다. pressure_head:n1[m]에서 독립
선언한 (35m-h)/5m score를 만들고 trigger1/reset0·지속/회복600s·간격300s를
적용했다. PUMP_1유량은 맥락/품질 확인으로만 남겼다.4원파일 공급자MD5와
현재SHA를 전후 대조했고 원 AlarmPolicy와864순차 결과가 같았다.2PENDING/
862ALARM·관측 activation1건은 선언한 재생 결과이며 성능 평가가 아니다.

이 구간에는 작업자·단서 전달·오반응·중단·실제 개도/유량 대응이나 독립 누설
정답이 없다. HEP/실제 전달지연/오경보율/검출률은 추정하지 않았다. 물리 신뢰도
예제도 작성 count/exposure/prior/수명이며 현재 공정 시계열은 현장 고장 빈도의
노출 분모를 제공하지 않는다. 사고 원본의 기존 취득 실패를 완료로 바꾸지 않았다.
자료군/파일/바이트 수는 그대로다. 원/변환 행은 artifacts에만 두며 공개 요약은
research/alarm_source_results.json이다. ALARM_SOURCE_KO의 출처/라이선스를 따른다.

## 체결과 구리 합금 기계시험 추가

CC BY4.0의 fastener_clamping v4, DOI10.17632/jffwrb4pcd.4에서 토크48·힘48·
DOE6CSV,102파일1281783바이트를 공급자SHA로 확인했다. 전체34950토크/32159힘
행을 보존했다. 전체 angle grid가 정확히 일치하는 쌍은0/48이고96곡선중29개는
엄격 증가가 아니다. 토크332개·힘146개 음수를 남겼다. DOE16조건×3반복과
CSV속도rad/s/논문rpm 불일치를 유지했다. 실제 T를 받아도 K/인장면적과 적용성
근거가 없어48preload는 needs_data다. 측정 힘을 같은 sample의 정답으로 보간하지 않았다.

KupferDigital v2, DOI10.5281/zenodo.10820299의 Files.zip1608805바이트를 공급자
MD5와 현재SHA로 확인했다.20인장LIS39334행과 primary Brinell11/Vickers12보고서가
있다. 인장 시간 비증가122구간·strain비증가10039구간·음의 힘195/보고 strain20064를
보존했다. 원strain×sourcegauge의 명시 재구성을 사용하고 machine travel을 대체하지
않았다.40선택 후보 중20point를 원 모델로 계산했고20후보는 미지원 부호/조건으로
보류했다. 원 첫 연속 strain prefix로 offset4계산/16보류를 남겼다. 같은 export와
보고 기울기/반올림Rp0.2는 독립 실험 정답이 아니다.

원파일은 data/raw, 전체 원/변환 행과 상세receipt는 artifacts에만 보존한다.
출처·조건·단위·자료 적용/보류와 재현은 FASTENER_DATA_KO, COPPER_TENSILE_KO,
COPPER_HARDNESS_KO를 따른다. 기계 판독 요약은 각 source_results에 있다.

## 검사·피로·부식·마모의 추가 연동

| 추가 자료 | 파일 | 바이트 | 이용 조건 | 적용과 보류 |
| --- | ---: | ---: | --- | --- |
| copper_wire_corrosion | 1 | 2,545,534 | CC BY 4.0 |9,387관측·28열 보존; loss단위와 물리노출원점 미상 |
| afm_steel_wear | 1 | 99,505 | CC0 1.0 |1,346행·9그림 구간; 12실측 부피의 차원 특정량; 대응경도/면적/시편미상 |

[Zenodo 구리 센서](https://zenodo.org/records/10640939)와
[Dryad AFM 자료의 공식 Zenodo 보존본](https://zenodo.org/records/4743140)의 권리·작성자·
버전·공급자MD5와 로컬SHA-256을 확인했다. 합계2,645,039바이트는 자동 취득했다.
현재 전체는39후보·27자료군·210파일·3,551,989,038바이트다. AFM 방법 근거는
[원 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC8336757/)의 캐시 해시로 고정했다.
구리 CSV와 연구 그림의 척도 관계를 추측하지 않는다. AFM 부피/(힘·거리) 값은
무차원 Archard 계수가 아니며 다른 하중의 압흔 경도를 자동 대입하지 않는다.
상세 원행은 artifacts, 공개 집계는 research/corrosion_source_results.json에 있다.
단위/원점/경도/면적이 미상인14모델요청은 모두 needs_data·native0회다.
원 피트 최대값·독립 hit/miss·blank 자료가 없어 면적 극값과 POD를 실측 보정하지 않았다.

## 재료 품질·수소 수송·감육 용량과 신뢰도 대조

| 자료 ID | 파일 | 바이트 | 이용 조건 | 원 조건 적용 |
| --- | ---: | ---: | --- | --- |
| bam_s355_charpy | 4 | 1,532,075 | CC BY 4.0 |6시편·22.5°C·5mm·미파단; 다른 batch 조성은 원시편에 대입하지 않음 |
| ahss_hydrogen_permeation | 1 | 88,885 | CC BY 4.0 |2이력1859행·전류/시각/293K; 면적/경계 농도/D/포획은 미상 |

[BAM S355 원 자료](https://zenodo.org/records/6778336)와
[MS AHSS1300 수소 투과 원 자료](https://data.mendeley.com/datasets/tnsgf7v76z/1)의 작성자·버전·
라이선스·checksum 증거를 cache/원장에 보존했다. 작은 파일은 직접 취득했다.
공통 ZIP guard와 workbook reader가 원 구성·크기·MD5/SHA256을 검사하며 변형된 단위·구성을 거부한다.
에너지 반올림 secondary6개는 같은6시편이며 추가 독립 시험으로 세지 않는다. 한 온도·미파단
관측으로 전이 곡선을 추정하지 않고 전기화학 전류를 격자 표면 농도로 바꾸지 않는다.
원조건9개 실제 API 요청은 전부 needs_data·native0회다. 자세한 조건·원 행·보류 receipt는
artifacts/material_process_data, 공개 집계는 research/material_process_data_results.json이다.
현재 합계41후보·29자료군·215파일·3,553,609,998바이트다.


## 감시 재적용과 별도 소스·의존성의 구분

기존 BattLeDIM의 공급자 MD5·로컬 SHA-256과 원 시각/관측 ID를 유지해 첫 3일
864행을 288학습·288보정·288평가로 나눴다. 세 압력/유량 채널을 원 SI로 옮기고
결측을 보간하거나 물리 fault truth를 만들지 않았다. 원 참조 기간의 정상성은
caller의 별도 선언이다. zscore/PCA/HBOS는 평가 288행, lag_ridge는 창4의 처음
3행 미상 뒤 285행이 유효하다. 이 적용은 탐지 성능이나 HEP의 관측 검증이 아니다.
원 자료는 늘지 않았고 합계 41후보·29자료군·215파일·3,553,609,998바이트다.

새 Pool SDK 소스 11파일 69,347바이트, 독립 station 물성표 및 worker 환경의
공개 의존성은 실험 관측으로 세지 않는다. 원 MIT LICENSE/NOTICE·Git blob과
로컬 해시, 실제 source/runtime 버전은 각 연계 결과에 기록한다. SAGA의 독립
작성 corpus도 원 측정자료가 아니며 실제 import된 PyMuPDF의 별도 이용 조건은
THIRD_PARTY_NOTICES를 따른다. 코드 릴리스에는 원 소스·환경·원관측·DB·상세
trajectory/receipt를 포함하지 않는다. 토양 실측 자료는 아직 확보·보정하지 않았다.


## 토양 관측 추가 확보

[SOIL_DATA_KO](SOIL_DATA_KO.md)의 두 CC BY4자료군·7파일·7,822,891바이트를 직접 취득했다.
현재43후보·31자료군·222파일·3,561,432,889바이트다. 시추공315깊이×1054시각·332010온도,
첫5초·비균일간격·미상calendar원점과 원 수직 좌표를 보존했다. 다른 실험의 일별 온도극값과
원 열 정렬 불일치·미상 수분/흡인단위도 보존한다. 독립 배관 열 검증·물성 보정은0사례다.
기계판독 요약은 research/soil_source_results.json이며 원관측/방법코드/상세receipt는 배포에서 제외한다.


## 2026-10-27: 유량계 공개 교정과 압축공기 조건

새 자료2군·3파일·547,329바이트를 직접 취득해 공급자 체크섬과 로컬 SHA-256을
독립 재검사했다. [계량 자료](METER_DATA_KO.md)에 저자·고정판·권리·변환·회차/시계와
미상 조건을 기록했다. 물/LNG 교정과 압축공기 조건은 서로 교환할 수 없으며
관측 취득을 가스 유량계의 독립 교정/현장 검증으로 표시하지 않는다.


## 교정·적산·공간 위험의 통합 단계

CC-BY-4.0 EMUE 소닉 노즐 자료1군1ZIP1,406,894바이트를 직접 확보하고 공급자 MD5와 로컬 SHA-256을 확인했다. 실험 Type-A 30점과 공급자 Monte Carlo 변환값/전체60×60공분산을 구분한다. 한 캠페인의 적합 재현이며 독립 검증 자료가 아니다. 원 프로그램은 실행하지 않았다.

정확한 API·실행 순서·발견한 반례와 한계는 [통합 기록](EIV_SPATIAL_INTEGRATION_KO.md)을 따른다.


## 인양·압축·상변화·공정 자료의 통합 단계

새3군8파일55,737,399바이트를 확보했다. DCS35036×25센서는 단위/역할/유체/유량이 미상이며 맵이 아니다. LN2 처리89행은 U의 k/공분산 미상과 원 h−q/ΔT 잔차를 유지한다. 원 압력/온도2파일의 전체 행과 NASA OCR 수치표는 자격을 부여하지 않았다. 제3자 비교 열과 supplier/local checksum의 차이를 보존한다.

정확한 계약·원 소유 버전·검증·재현과 남은 작업은 [최신 통합 기록](THERMAL_LIFTING_INTEGRATION_KO.md)을 따른다.


추가 확보한 관 마찰6운전점과 현장 음향886 WAV의 원자료 경계는
[관 마찰·음향 자료](FRICTION_ACOUSTIC_DATA_KO.md)를 따른다.
두 자료군6파일5,013,260바이트를 공급자 체크섬으로 검증했고, 환경소음 제3자 음원은 제외했다.


확보 자료와 원 엔진 사이의 새 분석 코드는 [관 마찰 study](FRICTION_STUDY_KO.md),
[현장 PCM study](AUDIO_STUDY_KO.md), [현장 수두/유량](FIELD_LEAK_DATA_KO.md)을 따른다.
126개 현장 workbook 관측은 형식 읽기를 완료했으나 수리/탐지/통계 모델은 미실행이다.

원자료를 누리온으로 별도 전송할 때는 [전송 확인 절차](NURION_VALIDATION_KO.md)를
따른다. `scripts/hpc_data.py`는 기존 자료 목록에서 선택한 파일의 상대 경로·크기·
SHA-256·권리 기록을 고정하고 수신 root와 대조한다. 관 마찰 4파일의 실제 바이트
확인 요약은 `research/raw_transfer_status.json`이다. 자료 해시 일치는 모델 검증이나
원자료 재배포 허가를 뜻하지 않는다.


## 확률적 물 배수망 누수 위치 자료

Zenodo DOI [10.5281/zenodo.7255403](https://zenodo.org/records/7255403), 버전 0.2.0의
`data.zip`을 카탈로그에 추가했다. 공급 기록상 크기는 195,518,700바이트이며 파일
MD5 `2a79f0f02000d9d6fecf33eab02a87e3`이다. API 메타데이터는 데이터 라이선스를
CC BY 4.0으로 표시한다. 본문/그림 및 별도 소프트웨어에 이 데이터 라이선스를
자동으로 확대하지 않는다.

취득·원장 갱신 명령은 다음과 같다.

```bash
python scripts/acquire_data.py --only probabilistic_leak_localization --workers 1 --maximum-seconds 300
python scripts/build_research_index.py --data-only
python scripts/inspect_water_network_archive.py data/raw/probabilistic_leak_localization/data.zip
```

압축 archive는 공급자 크기와 MD5를 확인해 취득했고, 로컬 SHA-256은
`51474a77a35414dfdc0c2748450729fc4ea78fc2bf8c7b2f4cc3e56b6e3f7374`다.
`research/water_network_archive_inventory.json`에 82 ZIP 엔트리 중 데이터 22파일의
이름·크기·CRC를 기록한다. 이 중 CSV 10개는 원 헤더·행 수·열별 빈칸 수·초기/마지막
행과 행 폭을 점검하고,
pickle 9개는 역직렬화 없이 opaque 자산으로만 기록한다. 네트워크·모의 run·현장
계측의 분리와 명시된 물리시험 경계를 보존한다. 두 현장 이벤트 표는 원 날짜·시각·위치
라벨·주석을 셀 단위로 보존하지만 날짜 형식, 좌표 및 수량 단위는 해석하지 않는다.
파일명만 보고 정답 또는 모델 입력을
추론하지 말고, archive 내 구조를 조사한 뒤 시험 이벤트와 site/network 경계로 분할한다.
이는 압력 과도파 도착 정답이나 누수 발생 빈도 자료가 아니다. 연관 복제 코드 저장소의
[LICENSE](https://github.com/predict-idlab/phys-ml-leak-localization/blob/main/LICENSE)는
상업적 재사용이 제한된 manuscript-review 전용 조건을 둔다. 해당 코드·노트북은
가져오거나 배포하지 않는다. 여기서는 데이터 자체의 CC BY 4.0 및 별도 인용만 유지한다.
다운로드 후 파일 SHA-256, zip member 목록, 추출 크기와 자료 기원 검토를 원장/후속
검토 기록에 추가하기 전에는 적용 또는 독립 검증 자료로 표시하지 않는다.
추가 경로 계층 충돌 규칙은 ZIP_PATH_HIERARCHY_KO.md를 참조한다.





대용량 ZIP을 RAM 전체 복사 없이 확인하는 공통 file-backed API와 미취득 LeakDB 대기 상태는 [대용량 원본 안내](LARGE_ARCHIVE_KO.md)를 따른다.

Pipe percussion dataset의 공식 Mendeley API pin도 확인했다. 단일 Pipe percussion dataset.rar 파일은 5,426,985,868 bytes, 공급 SHA-256
22a5d01347a2dce8830763954a6cb99ba3914ec97070277096370f52e0529c6, CC BY 4.0이다. 파일이 수 GB이므로 내려받기 전 metadata만 기록하고 취득은 사용자 수동 전달 대기다. [공식 데이터 페이지](https://data.mendeley.com/datasets/csyshmrpvg/1).
