# 비직교 토양 열 해석의 유한요소 후보 탐색

이 문서는 초기 후보 탐색 당시의 제한된 실험 기록이다. 당시에는 생산 어댑터를
추가하지 않았다. 이후의 명시 P1 경계·실제 조립 진단·세분 검증은
[현재 유한요소 서비스](SOIL_FEM_KO.md)에 별도로 기록했다. 이 탐색의 실패를
삭제하지 않으며 현재도 FiPy 경로의 직교 제한과 전체 복수관 항목 partial을 유지한다.

FiPy의 기울어진 선형장 열률 오차를 확인한 뒤, 원 solver를 수정하는 대신
[scikit-fem 공식 API](https://scikit-fem.readthedocs.io/en/stable/howto.html)를
별도 후보로 시험했다. [PyPI 12.0.2](https://pypi.org/project/scikit-fem/12.0.2/)의
178,478바이트 wheel을 별도 연구 경로에만 설치했다. 주 개발 환경과 필수 의존성은
변경하지 않았다. `skfem/` 및 배포 메타데이터의 이용 조건은 BSD-3-Clause다.
원 고지·면책을 유지해야 하며 문서 예제의 별도 이용 조건을 추정하지 않는다.
외부 구현·예제·바이너리를 이 저장소에 복제하지 않았다.

## 재현 조건과 확인한 범위

기준 형상은 공개 `examples/soil_cross_section.py.make_case(reviewed=True,
mode="linear")`의 35개 점·22개 볼록 토양 다각형이다. 각 다각형 안에서만
기존 꼭짓점을 fan 삼각분할해 P1 삼각형44개를 만들었다. 두 공동은 비어 있고
토양 단면적은22m²다. 외부 mesher나 원형 관 추정은 사용하지 않았다.

좌표에 x'=x+s·y, y'=y를 적용하고 s=0/0.3/0.75를 비교했다. 각 변환에서 원
경계 edge의 두 node ID를 native facet에 대응시키고, 제공한 경계의 합이 전체
exterior facet과 일치하며 겹치지 않는지 확인했다.

scikit-fem의 `MeshTri`, `ElementTriP1`, `Basis`, `FacetBasis`와 원 `laplace`,
`mass`, `unit_load`, `asm`, `condense`, `solve`로 조립·경계 제거·적분·선형 풀이를
실행했다. 축방향 길이2m, k=1 W/(m·K), Cv=10 J/(m³·K)는 작성한 가상 값이다.
P1 노드 온도와 연속 Dirichlet trace는 독립 해로 직접 제공했으며 기존 셀/면
평균을 노드 값으로 추정하지 않았다.

정상 기준은 T=300+2y K다. 세 전단의 기본 격자 모두 실제 지표 법선 열과
조립된 강제 경계 반력이24W에 일치했다. 최대 노드 온도 오차는 약1.14×10⁻¹³K,
반력 오차는 약5.7×10⁻¹³W였다. 이는 작성한 선형장에 대한 검증이며 곡면·
다른 경계·가변 물성의 일반 정확도를 입증하지 않는다.

과도 기준은 초기310K, 주위300K, 전 경계의 Robin h=0 또는1 W/(m²·K),
dt=0.05s의 backward Euler4단계다. consistent mass와 원 `Basis`의 vertex
quadrature로 조립한 lumped mass를 각각 확인했다. 에너지 수지는 닫혔지만
온도 상한을 위반한 경우가 있었다.

| 실험 | 확인한 사실 | 아직 인정할 수 없는 것 |
| --- | --- | --- |
| 기본 세 전단의 정상 선형장 | 물리 법선 열·원 반력24W | 임의 곡면/경계의 열률 자격 |
| 기본 consistent mass Robin | 에너지 잔차는 작음 | 초기 최고310K보다 약0.465~0.503K 높아지는 수치 overshoot |
| 기본 lumped mass Robin | 이 세 격자에서 상한 초과0 | 일반적인 단조성·최대 원리 보장 |
| 세분화한 전단0.75 lumped mass | 에너지 수지 통과 | 단계2에서 최고온도가310.024673…K; 양의 stiffness 비대각 항544개 |

실제 실험은 기본12사례와 세분12사례·원 sparse 풀이93회다. 기본37개 형상/
대수/기준 확인을 전체 물리 검증 완료로 해석하지 않는다. 에너지 잔차 최대는
약1.87×10⁻¹¹J지만, 수지 통과는 수동 냉각의 온도 상한 준수와 별도다.
실측 보정·성능 우위·생산 어댑터 자격을 주장하지 않는다.

## 다음 생산 계약과 완료 조건

1. P1 노드 점 온도·시간·근거와 기존 P0 셀/면 평균을 구분한다. 투영이 필요하면
   원 field/digest, 부모 cell·node 대응, 적분 에너지 오차·방법/버전·검토를 받는다.
   값이 없는 노드를 보간했다고 숨기거나 면마다 다른 Dirichlet 값의 corner를 자동 평균하지 않는다.
2. ground와 far-field의 공유 corner node는 일반적으로 반력을 경계별로 유일하게
   나눌 수 없다. 이번 대칭 선형장에서는 side corner 기여가 상쇄된다. 전체 관 공동의
   노드는 서로 분리되어 있지만, 경계 분할별 열률에는 별도 분배 정의·독립 기준이 필요하다.
3. 노드·facet·삼각형의 형상과 방향, 구멍·경계 대응·부피/면적·축 길이, 공통
   asset/location/clock/offset과 물성·상변화 근거를 보존한다. 비직교 각도 하나만으로
   정확도를 승인하지 않는다. 조립된 operator의 양의 비대각 항, Robin 이산화,
   mesh/time 세분과 단조성·최대 원리 조건을 함께 확인한다.
4. consistent/lumped mass를 명시하고 원 적분 에너지·경계 반력·물리 법선 flux·
   국소 잔차·미완료 prefix를 구분한다. 첫 coarse 결과가 좋아졌다는 이유로 모델을
   자동 교체하지 않는다. 세분화에서 발견된 overshoot를 포함해 다음 구현을 검증한다.
5. 실제 node/trace 계약·source/runtime pin·저장 검증·예산·신호 변환을 구현하고
   같은 원형 근사/전단/Robin/가열·냉각 기준을 재현한 후에만 기존 partial 상태를 재평가한다.

원 관측과 별도 소스가 필요한 전체 연구 범위는 [ROADMAP](ROADMAP_KO.md)과
[확장 요청](EXTENSION_REQUESTS_KO.md)을 따른다. 이 탐색의 수치·버전·해시는
공개 집계 `research/soil_fem_feasibility.json`에 보존한다.
