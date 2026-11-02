# 누리온 wave45 전체 검증 결과 및 wave46 보완 계획

기록 시각: 2026-10-30 (KST)

## wave45 결과

PBS 작업 `24236795.pbs`는 169개 테스트 모듈을 16개 shard로 실행하고 모든 모듈의 JUnit을 수집한 뒤 종료했습니다. strict no-skip gate는 실패했습니다.

- 수집: 3,427개 테스트 항목
- 실패: 27, 오류: 0, 건너뜀: 65
- PBS: Exit_status=1
- 16개 rank receipt의 `provenance_unchanged`가 모두 true
- 각 rank JUnit SHA-256이 receipt 값과 모두 일치
- source-integrity log SHA-256: `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`
- manifest SHA-256: `473cecbc4c31b7dcb0ba086b8f4c3e79d48818b7079b5a0241b6f4febea1e97e`
- summary SHA-256: `5b5eda7fc6b1e66964c4373e44010436e4642761c7f63b17e700ee75b312567f`

실패를 원인별로 나누면 다음과 같습니다.

| 건수 | 분류 | 해석 |
|---:|---|---|
| 22 | friction 원자료 누락 | `metadata.yml` 스테이징 누락. 물리 assertion에 도달하지 않음 |
| 3 | human-control-room 원자료 누락 | 확인된 데이터 맵 미적용. 물리 assertion에 도달하지 않음 |
| 1 | runner 환경 계약 | 임의 상속된 `/unreviewed/source`가 자식 `PYTHONPATH`에 남음 |
| 1 | capability 카탈로그 결함 | 실행 가능 모델 `hydraulics.liquid_head_loss`가 카탈로그에 없었음 |

65 skip은 HRA import 57건, process-monitoring import 7건, 원자료 미설정 1건으로 구분됐습니다. 이는 검증 의존성이나 데이터가 준비되지 않은 상태이며 통과 증거로 계산하지 않습니다. 원격 friction 및 human-control-room 데이터 파일의 SHA-256은 transfer plan과 일치했습니다.

## 코드 보완

- `scripts/hpc_tests.py`: 테스트 subprocess에는 `PIPELINE_HPC_TRUSTED_PYTHONPATH`로 승인한 node-local 경로만 전달하도록 수정했습니다. 임의 `PYTHONPATH`는 상속하지 않습니다.
- `src/pipeline_integrity/catalog_data/capabilities.json`: 누락된 `hydraulics.liquid_head_loss` 항목을 추가했습니다. 모델은 동점도 기반의 정상 단상 수두 요구량을 계산하며, 밀도나 절대압력은 추정하지 않는다고 제한을 명시합니다.
- `artifacts/hpc/full-suite-data.local.json`: friction·human-control-room 자료를 쓰는 두 테스트 모듈에 대해 통합 데이터 맵을 정의했습니다. 원자료는 코드 배포물에 포함하지 않습니다.
- wave46 준비 스크립트는 누리온에 이미 존재하는 HRA, process-monitoring, PRISM 소스 트리를 owner map에 추가해 누락 의존성으로 인한 skip을 줄입니다.

## 진행률과 한계

기존 focused physical 검증의 감사 가능한 통과 수는 279건으로 유지합니다. wave45 결과는 fixture 또는 runner 오류가 포함되어 추가하지 않았습니다. capability 카탈로그 보정 후 검증 목표 범위는 174개이며, 전체 capability 물리 qualification은 **0/174 (0%)**입니다. 수치 회귀 통과는 설비·현장 qualification을 뜻하지 않습니다.

wave46 준비 파일:

- `artifacts/hpc/wave46_prepare.py`
- `artifacts/hpc/full-suite-data.local.json`
- `scripts/hpc_tests.py`
- `src/pipeline_integrity/catalog_data/capabilities.json`

## wave46 재검증 제출

Job `24236825.pbs`를 2026-10-30 08:39 KST에 제출했고, 제출 응답은 Q 상태입니다. 16 shard/40 CPU/4시간 설정으로 기존 169개 모듈 전체를 재실행합니다. PBS SHA-256 `55499e48c5de27ce15974160c7435b519bde24ecf738abf5d032fe95751d6c47`, 수정 runner SHA-256 `e24454403cc5b1ca646bef51c21026efe90774f241c684669a7a795fa4fb618e`입니다.

wave46은 PyYAML, `lh2dt_models`, HRA, process-monitoring, PRISM의 외부 소스 owner map을 포함합니다. 두 raw-data 소비 모듈에는 검증된 통합 transfer plan을 지정했으며, 데이터 plan digest는 `296d6906dcb3a393f92812ad965394d0c67dbf79dba83cf5d3e96912acdd22d7`입니다. 수정된 controller·owner/data map·PBS 파일과 외부 디렉터리를 제출 전에 확인했고 PBS 문법 검사도 통과했습니다. 결과가 수집되기 전까지 wave46 pass는 0건으로 둡니다. 현재 집중 검증 통과는 279건, 전체 capability qualification은 0/174이며 설비·현장 qualification 주장은 하지 않습니다.

08:46 KST 확인에서 작업은 cpu0080에서 R 상태였고, 전체 169모듈 manifest와 16 rank의 pytest 시작 기록이 생성됐습니다. Manifest digest `3975e978c4897d975c7fbaa6dc5e8fe9ee8b50334ab7ed37fac2a7ab2fe487a8`, 파일 SHA-256 `ff0a11d95d404cc534f0dbb7df88c0fafd30e761af5d5940a8c70f4e8d5fe978`, source-integrity log SHA-256 `ce7c91fd341eb90e1a64468aed1ab7fc3177d2d5b03e11996df89391314ac34e`입니다. Manifest에 HRA 70개, process-monitoring 44개, PRISM 308개, `lh2dt_models` 82개, PyYAML 21개 파일이 각 source-tree hash와 함께 고정됐고, data fixture digest/128,834 bytes가 확인됐습니다. JUnit은 아직 없으므로 결과는 미정입니다. Poll 기록: `artifacts/hpc/poll-20261030-0846KST.json`.

08:58 KST 중간 확인에서 16개 중 9개 rank가 JUnit을 냈습니다. 현재 수집분은 1,737건 중 1,736 통과, 실패/오류 0, skip 1입니다. 9개 rank 전부 provenance unchanged이고 JUnit hash가 receipt와 맞습니다. 유일한 skip은 `test_potash_pipe_cp_source.py`의 CC BY 원자료가 wave46의 통합 데이터 맵에 빠져 발생했습니다. 대상 문서의 SHA-256 `de262b94567fb6d72c6ed520cdbecb5660d68579e266e33d0b33c023def9ed2f`는 확장 transfer plan과 일치합니다.

이를 별도로 검증하는 wave47 job `24236835.pbs`는 controller가 참조하는 capability 수정 JSON을 wave 디렉터리에 복사하지 않아 준비 단계에서 실패했습니다(Exit_status=1, 테스트 0건). 이 결과는 모델 실패로 계산하지 않습니다. 빠진 JSON을 복사하고 SHA-256 `731cef3a1070e4b7a1a8252f3a1e356773ded2cdbd892c1802159313ca684b60`을 확인한 뒤 동일 대상 검증을 `24236836.pbs`로 재제출했으며, 현재 Q입니다. 상세 poll: `artifacts/hpc/poll-20261030-0858KST.json`.


## wave46 최종 집계 및 wave47 원자료 재검증

2026-10-30 09:08 KST에 PBS 이력과 controller 출력, 전체 rank receipt를 다시 확인했습니다.

- wave46 `24236825.pbs`: 169/169 모듈, 16/16 shard 완료. JUnit 3,427건 중 통과 3,408, 실패 0, 오류 0, skip 19입니다. 각 rank의 provenance 상태가 유지됐고 JUnit SHA-256은 receipt 기록과 모두 일치합니다. PBS Exit_status=1은 strict no-skip 게이트 때문입니다.
- 19 skip 중 potash 원자료 1건은 wave47 `24236836.pbs`에서 원자료 계획 digest와 SHA-256을 확인하고 별도 실행해 1/1 통과했습니다. 따라서 남은 18건은 선택적 보존 실제 풍하중 결과 파일이 제공되지 않아 조건부로 생략된 사례입니다. 물리 계산 실패로 분류하지 않습니다.
- wave47 첫 제출 `24236835.pbs`는 capability JSON이 대상 폴더에 없어 테스트 전에 끝났으며 계산 결과에 포함하지 않았습니다. JSON을 보완한 재제출은 PBS Exit_status=0으로 끝났고 JUnit 1/1 통과, provenance unchanged, JUnit/receipt digest 일치가 확인됐습니다.
- wave46 summary SHA-256 `f1cc872618d3e935cfa711a741a562e180a8eb563b52d839e3c647074d9475c2`; manifest file SHA-256 `ff0a11d95d404cc534f0dbb7df88c0fafd30e761af5d5940a8c70f4e8d5fe978`.
- wave47 JUnit SHA-256 `c7e690cf50836c7fd729fa5d7592ce54a0ea7b9fcde485db52b16e262d76d485`; receipt SHA-256 `2639df5b8b5dce701a6e9ee86f390ae39dc65925cdc8217ace0551e8b1085729`.

두 작업 모두 전체 물리 capability qualification을 대신하지 않습니다. 현재 174 capability의 독립 물리 qualification은 0/174이며, 전체 JUnit 통과율과는 별도 지표입니다. poll 기록은 `artifacts/hpc/poll-20261030-0908KST.json`입니다.


## wave46 최종 집계 및 wave47 원자료 재검증

2026-10-30 09:08 KST에 PBS 이력과 controller 출력, 전체 rank receipt를 다시 확인했습니다.

- wave46 `24236825.pbs`: 169/169 모듈, 16/16 shard 완료. JUnit 3,427건 중 통과 3,408, 실패 0, 오류 0, skip 19입니다. 각 rank의 provenance 상태가 유지됐고 JUnit SHA-256은 receipt 기록과 모두 일치합니다. PBS Exit_status=1은 strict no-skip 게이트 때문입니다.
- 19 skip 중 potash 원자료 1건은 wave47 `24236836.pbs`에서 원자료 계획 digest와 SHA-256을 확인하고 별도 실행해 1/1 통과했습니다. 따라서 남은 18건은 선택적 보존 실제 풍하중 결과 파일이 제공되지 않아 조건부로 생략된 사례입니다. 물리 계산 실패로 분류하지 않습니다.
- wave47 첫 제출 `24236835.pbs`는 capability JSON이 대상 폴더에 없어 테스트 전에 끝났으며 계산 결과에 포함하지 않았습니다. JSON을 보완한 재제출은 PBS Exit_status=0으로 끝났고 JUnit 1/1 통과, provenance unchanged, JUnit/receipt digest 일치가 확인됐습니다.
- wave46 summary SHA-256 `f1cc872618d3e935cfa711a741a562e180a8eb563b52d839e3c647074d9475c2`; manifest file SHA-256 `ff0a11d95d404cc534f0dbb7df88c0fafd30e761af5d5940a8c70f4e8d5fe978`.
- wave47 JUnit SHA-256 `c7e690cf50836c7fd729fa5d7592ce54a0ea7b9fcde485db52b16e262d76d485`; receipt SHA-256 `2639df5b8b5dce701a6e9ee86f390ae39dc65925cdc8217ace0551e8b1085729`.

두 작업 모두 전체 물리 capability qualification을 대신하지 않습니다. 현재 174 capability의 독립 물리 qualification은 0/174이며, 전체 JUnit 통과율과는 별도 지표입니다. poll 기록은 `artifacts/hpc/poll-20261030-0908KST.json`입니다.


## wave48 피로·부식·파괴 경계 검증 제출

2026-10-30 09:08 KST, 기존 KISTI 공유 연결을 통해 `24236848.pbs`를 `norm_skl` 큐에 제출했습니다. 3개 shard/40 CPU/90분 설정이며 `test_cyclic_fatigue.py`, `test_corrosion_wear.py`, `test_fracture_arrest.py`를 대상으로 합니다. PBS SHA-256 `68ae4854c2d7e8b87c5f28eb25d32e1909285eba4b59baffe98b6d9990d2ed58`, controller SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`입니다. 제출 직후 PBS 상태는 Q였으며, 결과와 receipt 검증 전까지 pass는 0건으로 둡니다. 이 계약·경계 검증은 전체 174 capability의 물리 qualification이나 현장 적합성을 주장하지 않습니다.


## wave48 준비 실패와 wave49 재제출

wave48 `24236848.pbs`는 PBS preflight가 `wave48/hpc_tests.py`를 찾지 못해 테스트 시작 전에 종료했습니다(Exit_status=1, CPU 0초, 테스트 0건). 이는 환경 준비 오류이며 코드 실패로 해석하지 않습니다. 필요한 controller를 wave 디렉터리에 포함하고 SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`을 검증했습니다. 수정된 PBS SHA-256 `b7b1342b911cf5ca55c54d9a897d536c3791d6aba15174ae7ca74c13c7afb53f`와 `bash -n` 확인 후 wave49 `24236850.pbs`를 재제출했습니다. 제출 당시 Q이며 아직 테스트 결과는 없습니다.


## wave49 피로·부식·파괴 검증 완료와 wave50 열·유동 검증 제출

wave49 `24236850.pbs`는 PBS 종료 코드 0으로 완료했습니다. `test_corrosion_wear.py` 40/40, `test_cyclic_fatigue.py` 60/60, `test_fracture_arrest.py` 25/25로 총 125/125 통과이며 실패·오류·skip은 없습니다. 3 rank가 모두 종료했고 각 provenance unchanged 및 JUnit SHA/receipt 일치가 확인됐습니다. Summary SHA-256 `ff772cd87a0b96c81836ddb8321fee0bda9da6ec5e1f5c8ff302d9218c116ba6`, source integrity SHA-256 `315274069716fe1d84845c0992184adde0eb61ece848abd8ccda86790d9808f5`입니다.

다음 wave50 `24236854.pbs`는 buried thermal, heated transport, pump transient의 세 모듈을 각각 shard로 실행하도록 제출했습니다. PBS SHA-256 `6ea02acb32717d48a23f8aa3a8a9cb3c26e95dba4f82abb653989c465d75c7e5`; 제출 시 Q이며 아직 결과는 없습니다. poll 파일은 `artifacts/hpc/poll-20261030-0914KST.json`입니다. 전 물리 capability 독립 qualification은 여전히 0/174이며, 이 수치에는 계약 테스트를 더하지 않습니다.


## wave50/51 추가 물리 모듈 병렬 검증

wave50 `24236854.pbs`는 buried thermal, heated transport, pump transient를 실행 중이며 09:17 KST 확인에서 세 pytest rank 모두 시작했습니다. 아직 결과를 집계하지 않았습니다.

wave51 `24236855.pbs`는 pipe-soil transient, pressure-wave pair, valve transient 세 모듈을 대상으로 제출했고 확인 시 Q입니다. PBS SHA-256 `054bc721630a3b333dee10c2e82033852229abe7ad4e14144c6c2906a3fe2a34`, controller SHA-256 `ee77d48033415f29f92978accc8a0e67c0948e5891b277a5a4d331bf312cf7b3`입니다. 중복 검증을 줄이기 위해 앞서 완료한 thermal-coupling, soil-moisture, acoustic-clock, vapor-events, dynamic-mechanics, batch/contact/transport, compressor/metrology 목표와 다른 모듈을 골랐습니다. poll 기록은 `artifacts/hpc/poll-20261030-0917KST.json`입니다.


## wave52 mechanics and gas verification queued

Job `24236858.pbs` was submitted for `test_lifting.py`, `test_pig_motion.py`, and `test_gas_network.py` (three shards, 40 CPUs). It targets force/moment equilibrium, analytic stick/slip and event transitions, and network mass/energy closure. PBS SHA-256 `e0a7efb771d484cd0fa4f1358b9c86c271e438170c074bd584abab1dfc4d9d8e`; submission state Q. No result is counted yet. Poll: `artifacts/hpc/poll-20261030-0920KST.json`.


## wave50 완료 결과

PBS `24236854.pbs`는 Exit_status=0으로 완료했습니다. buried thermal 42, heated transport 13, pump transient 8건으로 총 63/63 통과(실패·오류·skip 0)입니다. Heated transport 묶음에는 일정 열용량의 해석 에너지 수지와 점도 갱신 검사 등이 포함됩니다. 3/3 rank receipt가 완료됐고 provenance unchanged 및 receipt-JUnit SHA 일치가 확인됐습니다. manifest digest `d3b21ee0b8ac538b0981cf90a682c59f9d44802d9b9d308a0e9a33c782a844df`, summary SHA-256 `fadea45550e15e883a69fb2d654cbcce947975e66e715246b568fedd63e800d7`입니다. 검증 범위는 해석/회귀 동작에 한정되며 설계·현장 qualification은 아닙니다.


## wave51/52 완료 결과

- wave51 `24236855.pbs`: pipe-soil transient 40, pressure-wave pair 50, valve transient 10. 총 100/100 통과, 실패·오류·skip 0, PBS Exit_status=0. 3/3 rank receipt가 완료됐고 provenance/JUnit SHA가 모두 일치합니다. manifest digest `32b88166bd1d5b5a3d4554fa69ff5227d0f06098c51dcee456a3e7fbe5b5e46d`, summary SHA-256 `31e3f51c220c4dbbc81775ac4a4d0c1a08b085d515a6e9ccd753c1cbb76c8cfc`.
- wave52 `24236858.pbs`: gas network 23, lifting 47, pig motion 18. 총 88/88 통과, 실패·오류·skip 0, PBS Exit_status=0. 3/3 rank receipt가 완료됐고 provenance/JUnit SHA가 모두 일치합니다. manifest digest `f88485e7217e35dea4ff33b4fc447332b50f653ed54bc3cfdc3f0a1fec918423`, summary SHA-256 `ad84ad06a2618cd954662d0d3d1305ca61842c954e9be3ce19e597714c53e8ac`.

wave50–52에서 추가 focused regression 251개가 모두 통과했습니다. 이는 테스트 묶음 통과 수이며 174개 capability 독립 qualification 분자에 자동 합산하지 않습니다. poll: `artifacts/hpc/poll-20261030-0928KST.json`.


## wave53 tank/slurry/water-quality verification queued

Job `24236879.pbs` targets tank inventory and property integration, slurry constitutive-flow analytic references, and water-quality transport/mixing checks. Three shards/40 CPUs; submission state Q. PBS SHA-256 `55867da5b703d800abb286ea659868bf0d98b07f1e262eb3339e2c4a4b0b3790`. Results remain uncounted until rank receipts, JUnit, and provenance are checked. Poll: `artifacts/hpc/poll-20261030-0931KST.json`.


## wave53 진행 중 / wave54 gas property 추가 검증

wave53 `24236879.pbs`는 세 rank 중 slurry transport 30/30, tanks 30/30이 완료됐고 water quality rank가 실행 중입니다. 부분 결과는 최종 집계에 반영하지 않고, controller summary와 남은 receipt 확인 후 기록합니다.

wave54 `24236951.pbs`는 gas thermal transient, gas meter real-gas correlation, tank breathing 세 모듈을 대상으로 제출해 Q 상태입니다. 3 shards/40 CPU, PBS SHA-256 `ce3dd596db1e82e9afb509d47e52194ddb52b483e56a910929380d69427b7189`입니다. 이전 gas-thermal owner-map 없는 실행은 8개 선택 계산을 건너뛴 이력이 있어, 이번에는 `lh2dt` source map을 포함한 파이프라인으로 실제 계산 경로를 다시 확인합니다.


## wave54 최종 분류 및 wave55 교정 재실행

wave54 `24236951.pbs`는 83 JUnit 노드 중 74 통과, 9 skip, 실패·오류 0으로 controller 집계를 마쳤으나 strict no-skip 게이트가 Exit_status=1을 반환했습니다. 9 skip은 gas thermal transient 8건과 tank breathing 1건에서 `lh2dt`가 import되지 않아 발생했습니다. 수치 assertion 실패가 아닙니다. 모든 rank는 provenance unchanged이며 JUnit SHA/receipt가 일치합니다. Summary SHA-256 `ffecd755bb9166ad7b5d36ce906f51f00242a76cb752bb038b4e71f8a65e4493`.

원인을 재검토해 wave54 생성 템플릿에서 PyYAML 외부 owner map만 남고 `lh2dt`가 빠졌음을 확인했습니다. 직전 진행 기록의 “wave54에서 lh2dt source map을 포함” 표현은 잘못이며 이를 정정합니다. wave55 `24236967.pbs`는 원래 owner map을 포함한 wave27 템플릿에서 세 대상 모듈을 구성하고, remote map 경로 존재·PBS 문법·controller hash를 확인해 재제출했습니다. 현재 Q이고 실제 계산 결과는 미집계입니다.


## wave55 실행 전 실패 및 wave56 교정

wave55 `24236967.pbs`는 owner map 경로 및 manifest 준비까지 완료했으나 rank 실행 인자 `--size 1`이 3-shard manifest와 달라 모든 worker가 결과 폴더를 만들지 못했습니다. Summary는 controller incomplete, 0 node로 끝났으며 물리 계산은 한 건도 실행되지 않았습니다. rank size를 3으로 고친 wave56 `24236970.pbs`는 PBS 문법, controller SHA, `lh2dt` map 경로 및 3-rank 실행 설정을 확인해 제출했고 현재 Q입니다. PBS SHA-256 `007c357ecb0c376ffe407fe3b6f951b8e72f4e8006b0493e42d5a798fa52a4a5`.

wave53의 water-quality worker는 첫 native concentration transport test 시작 이후 진행 이벤트가 나오지 않은 채 계속 실행 중입니다. 나머지 두 rank는 각각 30/30 통과했으나 이를 aggregate 완료 결과로 계산하지 않습니다. walltime 및 테스트 이벤트를 계속 추적합니다.


## 10:05 KST 연결 상태와 작업 확인 한계

2026-10-30 10:05 KST, 허용된 공유 wrapper가 `No usable nurion shared connection`을 반환했습니다. 설정된 절차에 따라 추가 원격 명령, PBS 조회·제출 및 새 SSH 인증을 중단했습니다. 마지막 연결 가능 확인은 09:42 KST입니다. 그때 wave53 `24236879.pbs`는 R(관측된 walltime 00:10:34, water-quality rank receipt 미생성), wave56 `24236970.pbs`는 R(00:00:36, 아직 결과 없음), wave55 `24236967.pbs`는 테스트 0건으로 종료된 상태였습니다. 이후 실제 상태는 알 수 없으므로 완료/실패로 추정하지 않습니다. 기존 KISTI 공유 연결로 수동 재로그인한 후 현재 job 상태부터 재확인해야 합니다. 상세 기록: `artifacts/hpc/poll-20261030-1005KST.json`.

집중 wave49–54의 검증 유형과 범위 해석은 [`NURION_TARGETED_EVIDENCE_CLASSIFICATION_KO.md`](NURION_TARGETED_EVIDENCE_CLASSIFICATION_KO.md)에 별도로 정리했다. 회귀 pass 수와 174 capability 독립 qualification을 구분한다.
