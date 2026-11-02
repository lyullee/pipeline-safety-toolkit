# 물리 검증 증거와 적용 범위

이 표는 검토한 시험의 판정식과 실행 증거를 연결한다. 전체 174개 기능의 자격 완료를 뜻하지 않는다. 각 모델의 일반 운전영역 검증에는 추가 경계조건과 물성·구성법칙의 독립 자료가 필요하다. 시험 이름이나 호출 목록만으로 통과 또는 독립성을 판정하지 않는다.

기준 실행은 한강 1185073이다. 169개 모듈의 JUnit 3,427건이 실패·오류·건너뜀 없이 완료됐으며 16개 rank의 JUnit/capture 해시와 provenance 보존을 로컬 증거에서 확인했다. 이후 수정한 증기 공동 시험은 이 결과에 포함되지 않는다. 1185109는 기존 소스 v4에 해시로 고정한 시험 delta를 적용하는 5개 모듈 병렬 재검증이다.

| API | 독립 기준과 판정 | 검토한 시험 | 남은 적용 범위 |
|---|---|---|---|
| gas_headspace.evaluate_headspace | 강체 용기의 제1법칙과 이상기체식; 단열 압축의 Poisson 법칙, 시간격자 오차 감소 | test_thermal_conservation_validation.py | 이상기체·상수 열용량 범위; 실제 유체 상변화 및 운전자료 검증 필요 |
| frost_growth.evaluate_frost_growth | 두 열저장체의 지수 온도차 해와 총에너지 보존; 질량 증가의 잠열·현열 수지 | test_thermal_conservation_validation.py | 지정 물성과 증착 유속; 실측 성장법칙 검증 필요 |
| spatial_phase_change.evaluate_spatial_phase_change | 단일 셀 엔탈피와 잠열구간 통과 | test_thermal_conservation_validation.py | 다중 셀 공간 수렴 및 계면 이동 기준 추가 필요 |
| rotating_dynamics.evaluate_rotating_shaft | 상수 토크의 각운동량·일; 양방향 선형 항력의 지수해와 2차 시간 수렴 | test_transport_conservation_validation.py | 지정 유량·선형 부하; 네트워크 피드백과 비선형 실제 성능자료 필요 |
| inventory_transfers.evaluate_inventory_transfers | 종별 개방계 질량·에너지 수지; 시간분할·stream 순서·에너지 기준 이동 불변성 | test_transport_conservation_validation.py | 지정 stream의 재고 회계; 유량을 결정하는 연성 물리 검증 별도 |
| vapor_cavity_moc.evaluate_vapor_cavity_moc | 반사파 도달 전 특성선으로 구한 공동체적과 발생·소멸 시각; 비음수 체적·포화압 하한·격자 저장오차 감소 | test_vapor_events_validation.py | 4단계 격자 보강본 포함 1185109의 5개 모듈 25건 통과; 반사 이후·열 및 증기 질량 효과 추가 필요 |
| gas_potential_network.evaluate_gas_potential_path | 층류 압력 적분 기준, 독립 이분법 셀 기준 및 격자 세분화 | test_gas_potential_analytic_validation.py | 이상기체·층류 기준; 난류·실제유체·광범위 운전조건 추가 필요 |
| gas_potential_network.evaluate_gas_potential_plenums | 고도차에 의한 위치에너지 일과 정적 에너지·질량 수지; 역류와 구간 분할 | test_gas_potential_plenum_validation.py | 국소 압력·Mach 허용영역; 일반 과도 유동과 장거리 네트워크 추가 필요 |

추적 파일: `artifacts/hpc/hangang-1185073-current-source-audit.json`, `artifacts/hpc/physics-wave58b-submission.json`, `artifacts/hpc/physics-test-call-index.json`. 정적 호출 색인은 검토 후보를 찾는 용도이며 수치 실행 결과로 집계하지 않는다.
