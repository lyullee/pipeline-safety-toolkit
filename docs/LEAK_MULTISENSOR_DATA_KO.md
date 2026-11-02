# 누수 실험 다중센서 자료 경계

이 모듈은 Mendeley Data `xw44wv2g88`, version 1의 CC BY 4.0 원본을 코드와
분리해 사용한다. 공급 7z의 크기와 SHA-256을 먼저 검증하고, archive 경로를 풀어 쓰지
않은 채 정규 파일 목록과 필요한 단일 멤버만 제한적으로 읽는다. 원본·저자·버전·라이선스는
[공급 데이터 페이지](https://data.mendeley.com/datasets/xw44wv2g88/1)를 따른다.

## 실제 확인한 구성

같은 저자의 [tbrnp6vrnj.1 공개 등록본](https://data.mendeley.com/datasets/tbrnp6vrnj/1)도 별도 DOI로 취득했다. 3개 센서 ZIP의 80/80/122개 멤버 경로와 크기는 여기 7z의 신호 파일과 일치한다. 전체 바이트 해시는 누리온에서 비교 대기다. 두 등록본을 독립 반복이나 별도 실험 가족으로 합산하지 않는다. 상세한 pin과 재현 절차는 [등록본 내용 crosswalk](LEAK_DEPOSIT_CROSSWALK_KO.md)을 따른다.


`inventory_leak_multisensor_archive`를 사용자 제공 원본에 실행해 다음을 확인했다.
영수증에는 로컬 절대경로나 다운로드 별칭을 넣지 않고 공급 카탈로그의 정식 파일명,
바이트 수와 SHA-256만 넣으므로 다른 호스트에서도 같은 원본은 같은 digest를 만든다.

| 계열 | 형식 | 파일 수 | 압축 해제 바이트 합계 |
|---|---:|---:|---:|
| 가속도계 | CSV | 80 | 1,848,001,201 |
| 동압 센서 | CSV | 80 | 1,883,226,891 |
| 하이드로폰 | vendor RAW | 122 | 150,702,480 |

이 수치는 archive 재고와 무결성 증거다. 센서 교정, SI 단위, 공통 trigger, 누수 발생시각,
신호 도달시각이나 현장 정확도의 증거가 아니다. 하이드로폰 RAW는 공개된 정확한 포맷·교정
변환과 공급 프로그램의 재사용 조건을 검토하기 전까지 디코딩하지 않는다.

`hydrophone_member_labels`는 실제 archive의 H1/H2, 네트워크, 누수 조건, 유량 조건과
`N`/`NN` 문자열을 손실 없이 보존한다. 단, `N`/`NN`의 의미는 확인 자료가 없으므로
`filename_marker_meaning=None`으로 둔다. 배경소음 파일에는 네트워크·유량을 만들지 않는다.
`read_hydrophone_member`는 정규 파일·목록 byte 수·원 archive와 도구를 재확인한 뒤 RAW
bytes와 SHA-256 영수증만 반환한다. `verify_hydrophone_source_record`도 bytes와 영수증만
검증하며 sample 폭, endian, 채널, 물리량, 단위 또는 교정을 해석하지 않는다.
실제 `Background Noise/H1` 멤버 1,274,840 bytes를 이 경계로 읽고 SHA-256
`74368d6562b84abe345fcf365f5ce0117936a72c8c8eb41076a0ad3923ac6401` 및 영수증 재검증을
확인했다. 이것은 opaque bytes 무결성 증거이며 파형·압력·음향량 검증이 아니다.

## 가속도계 CSV

동일한 대용량 archive에서 멤버 여러 개를 읽을 때는 [재사용 세션 경계](LEAK_MULTISENSOR_SESSION_KO.md)를 사용한다. 멤버별 SHA와 archive 전후 무결성 재확인은 유지하면서 archive 전체 반복 해시를 줄인다.

`accelerometer_member_labels`는 네트워크·누수 조건·배경 유량·A1/A2라는 파일명 표지만
읽는다. 파일명으로 물리량, 단위, 시계 동기화 또는 사건 정답을 만들지 않는다.

`read_accelerometer_member`는 공급 archive와 archive 도구를 전후 재검사하고 허용한 CSV
하나만 메모리로 읽는다. `parse_accelerometer_csv`는 호출자가 검토한 열 번호,
coordinate-to-second scale, value-to-m/s² scale/offset, clock/sensor/evidence ID를 모두
제공해야 한다. 행을 자동 삭제·정렬·보간하지 않으며, 비유한 값·중복/감소 시각·명시 예산
초과를 거부한다. `verify_accelerometer_csv_record`는 원 bytes와 같은 mapping으로 결과를
재생한다.

현재 실자료의 `Sample,Value` 헤더와 숫자 prefix는 확인했지만, 헤더 자체에는 Value 단위가
없다. 따라서 출판 정보나 숫자 간격만으로 SI 변환을 기본 설정하지 않는다. A1/A2 pair
동기화와 독립 사건 정답을 확보한 뒤 모니터링·음향 특징 API에 결합해야 한다.
대표 `Branched/Circumferential Crack/0.18 LPS/A1` 멤버 전체 23,109,495 bytes도 경로
추출 없이 읽었고 SHA-256 `cbf37677c4544c1162baf9b95c1ce17dc6c999143b510861458a11a8549b3c6f`를
기록했다. 이는 reader/원본 무결성 증거이며 SI 변환이나 신호모델 검증은 아니다.

## A1/A2 쌍 자격과 공통 trace

`qualify_accelerometer_pair`는 두 원 CSV record를 bytes부터 재생하고 같은 파일명 조건의
A1/A2인지 확인한다. 호출자가 unit mapping 검토, 공통 trigger, sample alignment를 각각
명시해야 하며 하나라도 false이면 `needs_pairing_or_mapping_evidence`다. ready 주장과 서로
다른 clock ID/좌표가 충돌하면 거부한다. mapping evidence ID와 source/record digest를
센서별로 보존하며 pairing만으로 누수 onset·arrival 정답을 만들지 않는다.

`build_accelerometer_pair_trace_record`는 ready 쌍에 한해 최소 8개 동일 좌표의 fixed-grid
오차를 호출자 상한과 비교한다. 보간·재표집은 하지 않고, source 좌표가 음수이면 검토한
time-origin shift/evidence 없이는 `SampledTrace`의 비음수 시작시각으로 바꾸지 않는다.
출력 quantity/unit은 명시 매핑을 따른 `acceleration`, `m/s^2`이며 A1/A2를 좌우 센서로
추론하지 않는다. 두 verifier는 원 bytes·매핑·pair evidence·trace 생성을 다시 실행한다.
`bind_accelerometer_delay_request`는 이 trace와 qualification digest를 호출자가 고정하고,
mapping·pairing·좌우 센서 역할·분석 근거가 실제 `RunContext.evidence`에 모두 있을 때만
기존 `acoustics.estimate_delay` 입력을 만든다. 좌우를 바꾸려면 새 순서를 명시해야 하며
파일명의 A1/A2로 역할을 추론하지 않는다. 바인더는 상관/GCC-PHAT 계산을 복제하거나
실행하지 않고, 위치·확률도 계산하지 않는다. 위치 계산에는 별도 센서 좌표/역할·파속·
경로·arrival 오차 근거가 필요하다.

## 배포와 검증 상태

원 7z와 추출 신호는 source-only 배포물에 넣지 않는다. 공개 코드에는 reader, 명시 mapping
계약, 테스트와 출처/한계 기록만 둔다. 새 테스트는 작성했지만 사용자 지시에 따라 로컬
pytest는 실행하지 않았으며 누리온 검증 대기다. 정적 상태는
`research/leak_multisensor_adapter_status.json`을 따른다.

누락된 가속도/하이드로폰 mapping·decoder·교정·pair clock·sensor 위치와 독립 event truth는
[PIPE-EXT-013](owner_requests/PIPE-EXT-013.md)의 구체적 소유자 계약으로 전달한다.
