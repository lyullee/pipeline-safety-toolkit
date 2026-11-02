# 정해진 인양 자세의 하중 검토

`lifting.evaluate_lifting(request, context)`는 사용자가 지정한 강체 자세에서
인양 줄의 장력을 구하고 검토된 유효 허용하중과 비교한다. 로프·훅·러그·앵커의
제조사 표나 규격 계수를 내장하지 않는다. 실제 적용 방향·결속 방식·곡률·온도·
사용 상태·하중 조합이 반영된 유효 한계와 근거를 호출자가 공급한다.

## 입력과 공개 API

`LiftingRequest`에는 다음을 선언한다.

- 자산·시계·평가시각과 우수계 지역 Cartesian 좌표 프레임/정의 근거
- 질량·질량중심·중력 벡터, 줄의 부착점/상부 앵커 위치와 물리 부품 ID
- 추가 집중력·집중 모멘트와 위치, 전체 자유물체와 기하 검토 여부
- 각 줄·부착점·상부 앵커의 `EffectiveLiftingCapacity`
- 명시 하중 배율, 모멘트 기준점/수치 길이 척도, 평형 잔차/랭크·조건수 한계

단위는 kg·m·s·N·N·m이다. kg 질량과 N 힘의 의미를 자동으로 바꾸지 않는다.
`load_multiplier`는 선언한 무게·추가 힘·모멘트에 적용하는 정적 배율이다.
실제 충격·인양 가속도·동역학·공유 불확실도의 추정값이 아니다. 줄/부착점/
앵커가 공유하는 ID는 좌표도 같아야 한다. 서로 다른 프레임을 몰래 변환하지 않는다.

`boundary_metadata(request)`를 `context.metadata["lifting_boundary"]`에 넣는다.
자산·시계·프레임·평가시각·힘 방향·고정 자세 가정을 정확히 연결한다. 모든
입력 근거 ID는 `RunContext.evidence`에 존재해야 한다. 미검토 기본 예제는
needs_data이며 NumPy 선형해석을 실행하지 않는다.

```python
from examples.lifting import make_case
from pipeline_integrity.lifting import evaluate_lifting, verify_lifting_record

request, context = make_case(reviewed=True)  # 작성한 가상 조건의 수치 예제
record = evaluate_lifting(request, context)
assert verify_lifting_record(record, request=request, context=context)
assert record["qualification"] == "static_force_screening_at_declared_pose"
```

## 계산과 지지 범위

각 줄은 부착점에서 상부 앵커를 향하는 축력만 전달한다. 힘의 합과 지정 기준점에
대한 모멘트 합이 모두0이어야 한다. 모멘트 행을 선언한 길이로 무차원화해 힘
행과 함께 기존 `numpy.linalg.lstsq`로 푼다. 모든 줄 장력이 유일하게 결정되고
선언한 조건수/원 단위 힘·모멘트 잔차를 만족할 때만 명목 해를 남긴다.

일반적인 초정정 인양에서 최소 노름 장력을 실제 하중 분담으로 사용하지 않는다.
강성·원 길이·초기 장력·변형 적합 조건이 없는 중복 줄은 지원 밖이다. 음의
장력을 압축 로프나0으로 바꾸지 않는다. 관측하지 않은 느슨한 줄의 활성 집합,
강체 자세 이동·유한 회전·안정성·접촉·로프 자중/곡률·좌굴·동적 흔들림은 풀지 않는다.
이 API는 선언한 자세의 평형 하중 검토다. 실제 자세와 구조 안정성은 별도 근거가 필요하다.

공유 훅에는 각 장력의 크기 합이 아닌 힘 벡터의 합을 적용한다. 부착점의 힘과
상부 앵커에 작용하는 반대 방향의 반력을 구분한다. `all_directions`는 이미
모든 해당 방향에 대해 검토된 유효 한계다. 방향 의존 한계는 `within_cone`와
정격 방향/각도를 받으며 실제 힘이 영역 밖이면 그 한계를 적용하지 않는다.
각도 경계를 임의로 넓히거나 투영해 통과시키지 않는다.

검토 한계가 없으면 명목 하중은 유지하되 최종 상태는 needs_data다. 알려진 한계
초과와 다른 부품의 한계 미상은 함께 보존한다. 모든 비교를 수행했더라도
`acceptance=exceeds_supplied_limits`는 계산 성공과 별개로 한계 초과를 나타낸다.
`within_supplied_limits`는 공급한 한계 안이라는 뜻이며 작업 승인이나 현장 자격이 아니다.

## 다른 모듈과 연결

`solution.component_forces_n`은 부품 ID와 프레임을 유지한 벡터 하중이다. 기존
`structural`의 노드/부재·재료·좌표에 대응시킨 뒤 별도 구조 요청으로 전달할 수 있다.
이 함수가 노드 배치·관 응력·러그 접촉·앵커 지반 저항을 자동 추정하지 않는다.
`to_signal_frame(record, context)`는 완전한 정적 계산의 줄 장력을 N 채널로
내보낸다. 평가시각은 선언한 자세의 시각이며 연속 동적 이력이 아니다. 작성 자료는
synthetic, 그 밖의 계산 결과는 simulation으로 표시하고 원 출처는 receipt에 남긴다.
valid는 계산값의 유효성이며 허용하중 통과나 작업 승인 신호가 아니다.

`verify_lifting_record(record, request=..., context=...)`는 현재 코드·환경·원 요청으로
평형과 방향/한계를 다시 검사한다. 재해시한 출력·원 입력·버전·자격 변조를 거부한다.
해시는 전자서명이나 원래 입력의 사실성/승인 증명이 아니다.

## 개발과 검증

```powershell
python -m pytest tests/test_lifting.py -q
python scripts/check_lifting.py
```

독립 닫힌형 두 줄 장력, 비공선 세 지점 무게 분담, 집중 모멘트, 회전/원점 이동,
수치 길이 척도, 공유 훅 반력, 방향 한계, 한계 미상/초과, 초정정·음의 장력·
불균형, 복제 NaN/무한대와 재해시 오염을 확인한다. 실제 제조사/현장 자료는
아직 적용하지 않았다. 작성한20kN 한계는 예제일 뿐 어떤 제품의 허용하중도 아니다.
실행 요약과 정확한 소스 핀은 [lifting_results](../research/lifting_results.json)에,
상세 입력/결과는 배포 밖 `artifacts/lifting`에 남긴다.

사용한 기본 평형식은 공개적인 역학 관계이며 설명과 예제를 새로 작성했다.
수치 선형해석의 랭크·특잇값 해석은
[NumPy 공식 API](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html)를 따른다.
현장별 인양 승인·제조사 계수/표·검사 절차는 별도 검토 공급자가 필요하다.

후속 확장은 원 길이/강성/초기 장력과 느슨한 줄의 접촉 조건을 가진 비선형 자세
해석, 실제 동적 하중·다물체·부품 응력, 방향별 공급자 envelope·이력·현장 시험이다.
허용하중 자료를 구하지 못했다고 임의의 안전계수로 대신하지 않는다.
