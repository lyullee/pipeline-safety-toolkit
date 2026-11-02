# 검사 결과에서 구조 capacity API로 넘기기

`inspection_profile_bridge`는 검사 run의 특징점 두께 측정치를 기존 `profile_capacity` 소유 API에 전달하기 전에 출처와 좌표를 묶는다. 구조 계산식이나 검사 판정 알고리즘은 복제하지 않는다.

`bind_inspection_profile(run, request, context)`는 asset·origin·clock, 검사 원본 ID와 SHA-256, 시각 근거, 완전 스캔 상태, 제외행, 프로파일 point ID와 특징점 ID, 축 위치, 기준 벽두께를 확인한다. 특징점마다 잔여 리가먼트 오차 상한이 있어야 하고 owner 입력 잔여 두께는 `기준 벽두께 - 최대 깊이 - 오차 상한`과 일치해야 한다. 양수 하한만 허용한다. 오차는 확률 분포로 변환하지 않고 보수적 한계로 적용한다.

`evaluate_inspection_profile_capacity`는 검증 후 기존 `structural.profile_capacity` API에 요청을 넘긴다. `verify_inspection_profile_capacity_record`는 원 소유자 receipt와 추가 binding을 확인한다. 성공 기록은 source/context/request digest, 점 순서, 원 특징점 메타데이터, 보수 입력과 비자격 선언을 보존한다.

```python
from pipeline_integrity.inspection_profile_bridge import (
    evaluate_inspection_profile_capacity, verify_inspection_profile_capacity_record,
)
record = evaluate_inspection_profile_capacity(run, profile_request, context, client=owner_client)
verify_inspection_profile_capacity_record(record)
```

특징점 목록만으로 축 전체 검사 coverage를 추론하지 않는다. 사용자는 원 프로파일 자료와 위치 기준을 확인하고 `profile_capacity`의 전체 축 범위, blunt loss, 압력 범위 및 공급자 적용성 review를 별도 근거와 함께 충족해야 한다. 측정오차 상한은 confidence interval이 아니다. 모델 capacity는 잔여수명, 파괴확률, 표준 적합성 또는 현장 설계 승인으로 해석하지 않는다. 합성 회귀는 작성만 했고 누리온 실행은 대기 중이다.
