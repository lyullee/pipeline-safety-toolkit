# 압축기·극저온 공정 자료의 의미와 판독

새 자료3군8파일을 직접 확보해 권리·크기·체크섬과 원본을 보존했다.
원관측·다운로드·상세 기록은 코드 배포에 포함하지 않는다. 공개 저장소에는
취득 URL·라이선스·저자·버전·처리/검증 코드·한계와 집계를 남긴다.

| 자료 | 원 생성 방식 | 이용 조건과 확인 |
|---|---|---|
| Refinery Compressor Sensor Data | 실제 정유공장 DCS의1년 관측 | [Zenodo14866092](https://zenodo.org/records/14866092), CC-BY-4.0·공급자 MD5 |
| Cryogenic Pool Boiling | LN2 지상/감소중력 실험과 ANSYS spreading 처리 | [Zenodo19792024](https://zenodo.org/records/19792024), CC-BY-4.0·5파일 공급자 MD5 |
| NASA TN D-4171 | 구형 LH2 탱크의 자체 가압 실험 | [NTRS19670028965](https://ntrs.nasa.gov/citations/19670028965), 원 미국정부 작업의 공개 사용 허용·로컬 SHA-256 |

NASA checksum은 공급자 검증으로 표시하지 않는다. 논문 그림을 digitize하거나
원 프로그램을 실행하지 않았다. 저자별 인용과 정확한 원 파일/MD5/SHA는
카탈로그·[집계](../research/thermal_process_source_results.json)·제3자 고지를 따른다.
비상업적 조건의 별도 압축기 맵 후보와 모사 값을 실측이라고 부르는 후보는
이 퍼미시브 코드 경로의 실험 검증 자료로 채택하지 않았다.

## API와 실제 판독

`read_thermal_process_source(dataset_id, folder, source_spec=None)`는 선택한 원
파일 모두의 권리·크기·체크섬을 확인한 뒤 형식/의미를 판독한다.
`verify_thermal_process_record(record, source_root=...)`는 원 파일에서 재생한다.
`iter_refinery_observations(path)`는 원 행·source timestamp·signed/0/None의
25센서 값을 스트리밍한다. 원본 근거를 한 번 확인하고 닫힌 workbook 수명을 유지한다.

정유공장35036관측·25센서를 실제 전부 읽고 원 시간 순서의 중복/감소·공백과
채널 통계를 남긴다. 원 timezone·단위·압력 기준/포트 역할·유체 조성·유량/
총상태·맵·효율은 추정하지 않는다. DCS 채널은 성능 맵이나 서지 정답이 아니다.
현재 압축기 모델 대응은 needs_data다. 시간대를 붙이거나 채널 이름에서 단위를
만들어 SignalFrame으로 넘기지 않는다. trailing 빈 XLSX 셀은 None이며0이 아니다.

LN2의 불확실도 workbook89행은 원 ΔT, q, HTC와 U열을 보존한다. W/cm²와
W/(cm²·K)는 각각10000을 곱해 SI로 변환한다. U의 포함계수·표준/확대 의미·
공동 공분산은 미상이다. h와 q/ΔT의 원 반올림/불일치 잔차도 보존하고 값을
수정하지 않는다. 질소 곡선을 수소 전용 계면 closure의 검증값으로 전이하지 않는다.

다른 corrected/raw/uncertainty 파일의 지지영역은 서로 다르다. 보정 곡선의
Bombardieri/Edeskuty 등 제3자 비교 열을 독립적인 재사용 권리가 확인된 원 실험으로
표시하지 않는다. 이 reader는 그 비교 수치를 모델 보정/공개 수치 집계에 사용하지 않는다.
추가 원 압력/온도2파일은 checksum과 bounded sheet/header 미리보기를 확인했으며,
모든 원 행의 시계·단위·공식·대응을 검증했다고 표시하지 않는다. 원 `+273` 공식과
수식 캐시는 고치거나 실행하지 않는다. 원 온도/압력/중력 지지영역을 먼저 확인해야 한다.

NASA 보고서는 원50페이지와 표 위치/주제를 확인하고 실제 표2페이지를 렌더링해
비교했다. OCR이 숫자·행/열·표 선을 섞어 읽으므로 numeric_table_cells_qualified는
False다. 자동 수치 복원을 보류하며, 탱크 가압·분포 열입력 자료를 독립 계면/관
모델 정답이라고 사용하지 않는다. 이 한계를 숨기기 위해 손으로 숫자를 코드에 내장하지 않는다.

## 재현과 한계

```powershell
python scripts/acquire_data.py --only cryogenic_ln2_boiling refinery_compressor_process nasa_lh2_d4171
python scripts/review_thermal_process_source.py --raw-root data/raw
python -m pytest tests/test_thermal_process_data.py tests/test_source_budgets.py -q
```

공용 pin/workbook guard에 명시한 호출별 byte·압축 해제·member·cell budget을 추가했다.
기존8MB/64MB/32MB 기본값은 유지하며 새11MB/25MB 파일만 검토한 크기로 읽는다.
예산 확장이 라이선스·체크섬·경로/중복 member·양의 정수 budget 검사를 끄지 않는다.
원자료의 뜻을 바꾸는 재해시 오염과 단위/coverage/유체/출처/시각 변조도 거부한다.
소프트웨어/출처 의미의 검증이며 물리 성능 정확도나 현장 자격의 검증은 아니다.

후속은 검토된 실제 압축기 맵과 총상태/유량/효율/제어 관측, LN2 원 행의 시계·
압력 단위/기준·열입력/기하·불확실도 법칙, NASA 표의 source-cell별 시각적 대응과
탱크/벽/열입력 조건의 원 모델 연결이다. 자료와 모델의 빈칸을 가정으로 채우는
경우 작성한 시나리오로 별도 표시해야 한다.
