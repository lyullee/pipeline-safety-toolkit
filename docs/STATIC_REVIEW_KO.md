# 새 어댑터의 교차 정적 검토

검토 방식은 Python/원 엔진 소스·입출력 키·식·부호·문맥과 원자료 처리 경계를
대조하는 것이다. pytest·native·수치 예제·벤치마크는 실행하지 않았다.
Ruff/컴파일과 정적 검토를 실제 연동·보존수지 수치 검증으로 바꾸지 않는다.
현재 소스 차이와 수정 파일의 해시는 research/static_review_results.json에 기록한다.

## 구조·운영

운영 배치 영수증에는 performance_source_id, 배송 window와 required mass/service를
포함해야 하는데 누락돼 있었다. 변경된 공급자·기간·서비스에 같은 영수증을 재사용할
수 없도록 바인딩을 보완했다. 실제 EvidenceRef에 forecast SHA가 있는 경우 선언한
forecast와 충돌도 거부한다. 관련 회귀는 작성했으며 실행 대기다.

pulling의 법선/중력·force ledger, soil_pipe의 가상하중 제거와 실제 반력 복원,
buckling의 native Ke/Kg 부호·support·gauge·고유쌍 잔차, route의 Pareto/미상 후보와
lifecycle/CP-SAT 목적 구분을 원 코드와 대조했다. 추가 버그를 확인하지 못했다는
정적 결과가 해당 계산의 시험 통과 또는 완전성을 증명하지는 않는다.

## 열·유체·사건

탱크의 사건/시나리오와 초기 상태/closure가 원 요청 문맥에 완전히 묶이도록 수정했다.
피그는 마지막 구간에서 속도영점 reset 뒤 장부 행이 없으면 reset이 에너지/운동량
감사에서 빠질 수 있었다. reset 직후 행을 추가하고 breakaway/stuck 전이도 작업 예산에
센다. 슬러리는 정지 항복 구간 중 유효한 양압 가지를 보존하고 임의 기준 가지로
전체 허용 구간을 거부하지 않는다. 가열 수송은 u+pv=h 분해 오차를 실제 cell/global
수지 판정에 포함한다. 가스는 원 success가 enthalpy/junction까지 포함함을 확인하고
각 성분 flag/허용오차를 단계 기록에 노출하며 모순된 성공은 거부한다.

대조한 원 엔진은 lh2dt network/dynamic_network/dynamic_pipe/stratified_tank/valve와
기존 수리·열·CheckedProvider다. 설치된 원 엔진 실행을 수행한 것은 아니다.

## 압력파·자료·절차

압력파의 threshold/timing/review 전체 입력 문맥 바인딩, 평탄 peak의 전체 지지구간,
압축 멤버/원본 재확인, 절대압 및 CSV 단위변환 의미, 원자료 재생의 경로 경계를
보강했다. 절차의 명시적인 부정 판정과 증거/선행 작업 준비 상태는 별도로 보존하도록 수정했다.
최종 수정 상세·파일 해시는 정적 검토 결과 JSON과 각 전용 매뉴얼을 따른다.

## 후속 확인

변경된 입력 바인딩을 실제 owner가 준 영수증과 함께 재검토하고, 작성한 회귀와
기존 회귀를 누리온에서 실행한다. 압력파33개와 전체2,403개는 수정 이전 스냅샷의
실행 기록이므로 현재 소스 통과로 확대하지 않는다. 코드가 바뀌었으므로 과거 HPC
manifest를 재사용하지 않고 현재 소스의 계획·전송 번들을 생성한다.
