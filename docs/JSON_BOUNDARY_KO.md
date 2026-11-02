# 비유한 값과 JSON 경계

공통 `Contract`와 `json_value`는 NaN·양/음 무한대를 미상 값인 `None`으로 바꾸지
않는다. 값이 미상이면 처음부터 `None`을 명시한다. 품질이 invalid이면 해당 계약의
valid/status/진단으로 표현하며 NaN을 결측 데이터의 약자로 보내지 않는다.

독립 검토에서 복제한 객체의 NaN/무한대가 JSON 처리 중 null로 바뀌어 기존 위험·
결과 어댑터의 재검사에 통과하는 경로를 재현했다. Pydantic의 복제 갱신은 별도
검증과 구분되며 JSON 비유한 값 설정의 기본 동작도 이 문제에 영향을 준다.
[복제 API](https://docs.pydantic.dev/latest/api/base_model/#pydantic.main.BaseModel.model_copy)와
[JSON 설정](https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.ser_json_inf_nan)을
참고한다. 이 프로젝트의 입력·출력 계약은 더 엄격하게 적용한다.

`Contract`는 JSON 직렬화 직전의 원 필드와 변환 직후의 수치를 검사한다. 내부적으로
비유한 값을 보존하는 설정을 사용하므로 사용자 field/computed serializer가 만든
NaN도 null로 숨지 않는다. 최종적으로 NaN/Infinity 상수를 포함한 JSON을 내보내는
기능은 제공하지 않으며 해당 입력을 오류로 거부한다.

`json_value`는 외부 Pydantic 모델도 원 필드를 먼저 검사한다. JSON에서만 동작하는
외부 변환기가 만든 비유한 값은 별도의 복사 스키마와
[SchemaSerializer](https://docs.pydantic.dev/latest/api/pydantic_core/#pydantic_core.SchemaSerializer)로
확인한다. 소유 모델의 스키마·설정·serializer 객체를 수정하거나 전역 monkeypatch를
하지 않는다. 외부 모델의 정상 변환 의미는 유지한다. 형식 변환기는 부수 효과 없이
재현 가능해야 하며 외부 물리 모델 자체의 정확성을 인증하는 경계는 아니다.

Python 모드의 `model_dump(mode="python")`는 원 값을 그대로 보존한다. 이것은 엄격한
재검사와 진단을 위한 동작이다. 새 API는 실행 전에 해당 Python 값으로 계약을
재검사한다. `model_copy`/`model_construct` 또는 수정 가능한 내부 dict가 검증을
대신한다고 가정하지 않는다. 정상 null·0·음수·UTC datetime·튜플의 기존 JSON 표현과
검증/직렬화 스키마는 별도로 대조했다.

새 외부 통합에서는 원 모델 객체 또는 유한한 Python 자료를 `json_value`에 넘긴다.
앞선 외부 단계가 이미 비유한 값을 null로 바꿨다면 원래 값은 복구할 수 없다.
원 응답·소스·스키마와 상태/품질을 함께 검증하고, null만으로 계측 실패·미상을
추정하지 않는다. 이 경계는 서명이나 과거 실행의 진위 증명이 아니다.

검증은 `tests/test_contract_serialization.py`와 전체 회귀에 포함된다. 원래 재현한
JSON-only field/model serializer, 소유 객체 불변, 복제 NaN/무한대, 기존 위험/결과
경계, 정상 날짜/스키마, 공유 하위 자료와 실제 순환을 구분하는 기준을 보존했다.
테스트 환경은 Pydantic2.14.1/pydantic-core2.50.1이며 두 설치를 그대로 사용했다.
