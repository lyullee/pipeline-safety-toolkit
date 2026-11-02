# 관 마찰 및 현장 음향 원자료

## 관 마찰 자료

[Zenodo 20025373](https://zenodo.org/records/20025373)의 고정 record를
`pipe_friction_roughness`로 등록했다. 공급자 API는 MIT를 선언하고 취득한 LICENSE도
MIT 고지를 담고 있다. dataset.csv·metadata.yml·README·LICENSE 네 파일 12,340바이트를
공급자 MD5로 검증했다. 소스 프로그램 두 개는 취득하거나 실행하지 않았다.
원자료와 고지는 코드 배포에 포함하지 않으며 재배포 시 원 고지를 보존해야 한다. 대용량 ZIP의 동일-handle streaming checksum 및 메타데이터 인벤토리에는
공통 `source_archive.pinned_source_zip_stream`을 사용할 수 있다. 내부 파일 형식과
수리 단위는 압축 목록만으로 추정하지 않는다.

`friction_data.read_friction_observations(folder)`는 원본 크기·체크섬·예산·열·행 수를
확인하고 여섯 운전점의 압력수두(m), 유량(m³/s), 동점도(m²/s)를 반환한다.
동점도는 온도에서 산정된 값으로 별도 측정 점도로 표시하지 않는다. 압력수두를
절대압력으로 바꾸거나 날짜·독립 반복·확률오차를 만들지 않는다.
`verify_friction_observation_record(record, source_root=...)`는 같은 원자료와
현재 리더로 다시 읽어 비교한다. 무서명 해시는 인증 수단이 아니다.

source_observations.json은 artifacts 아래에만 저장한다. 현재 실행은 원자료 읽기이며
마찰모델 계산·계수 적합·독립 검증이 아니다. 수리 모델 적용 시 실제 직관 길이와
엘보/부차손실을 분리해야 한다. 문헌의 적합 계수를 동일 자료의 독립 정답으로
사용하지 않는다. 리더 회귀 시험은 작성했으며 누리온 실행 대기다.

## 현장 음향 자료

[Zenodo 18631450](https://zenodo.org/records/18631450)의 공급자 API는 CC BY 4.0을
선언한다. 현장 취득으로 설명된 누설·비누설 압축파일만 선택했다. 두 파일
5,000,920바이트의 공급자 MD5를 확인했다. 실제 목록은 누설500개·비누설386개 WAV다.
각 묶음의 한 파일을 헤더 수준에서 읽었고, 두 표본은 1채널·16비트·8kHz·8,000프레임이다.
모든 파일의 동일 형식을 확인한 것은 아니다.

환경소음 묶음은 일부가 제3자 웹사이트에서 가져왔다고 기탁자가 설명한다.
원 권리 관계가 확인되지 않아 취득 대상에서 제외했다. 선택 자료의 기탁자 고지는
제3자 권리 부존재나 모든 사용 조건을 보장하는 법적 판정으로 확대하지 않는다.
원자료를 코드 배포에 포함하지 않고 실제 재배포 시 출처·조건을 별도 검토한다.

PCM 정수는 압력이나 변형률이 아니다. 센서별 교정·계측 지지영역·공통 시계·
물리 실험 그룹이 없으므로 DAS 자료, 도착시간 정답, 독립 886개 실험으로 부르지 않는다.
파일명에 유사한 조건이 반복되므로 임의 clip 단위 훈련/검증 분할은 누출 위험이 있다.
실제 사건·위치·장치 그룹을 확인한 뒤 기존 monitoring 엔진에 연결한다.
이 턴에서는 탐지기 학습·성능평가·누설확률 계산을 실행하지 않았다.

## 재현과 원장

scripts/acquire_data.py --only pipe_friction_roughness water_acoustic_training_base로
선택 취득한다. 원장은 artifacts/acquisition/<dataset_id>.json에 있고 공급자 체크섬과
로컬 SHA-256을 구분한다. 공개 catalog는 출처·기탁자·record·조건·제약을 보존한다.
실제 파일·목록·헤더 결과는 artifacts 및 data/raw에 유지하고 전송 소스 번들에서 제외한다.


## 음향 읽기 API

`audio_data.read_training_audio(archive, member, tar_executable=...)`는 검토된 두
압축파일 중 하나만 받는다. 원본 공급자 크기/체크섬을 먼저 확인하고 안전한 경로의
단일 regular WAV만 stdout으로 읽는다. 파일을 디스크에 풀거나 프로그램을 실행하지 않는다.
목록·출력·시간·프레임 예산을 초과하면 부분 자료를 채택하지 않는다.
실제 확인한 도구는 Windows의 libarchive 기반 tar이며 실행 파일 SHA도 기록한다.
누리온의 GNU tar가 RAR 및 같은 verbose 형식을 지원한다고 가정하지 않는다.
해당 환경에는 호환 도구를 별도로 준비하고 다시 검토한다.

`parse_pcm_wave(raw, maximum_frames=...)`는 mono 정수 PCM의 8/16/24/32비트
코드를 보존한다. 8비트 바이어스만 정수 코드 표현에 맞춰 제거하고 물리 진폭으로
스케일하지 않는다. 실제 취득 자료의 두 표본에서 각8,000프레임을 읽어 artifacts에
기록했다. 이 작업은 자료 디코딩이며 수치 모델 검증/학습 실행이 아니다.

`verify_training_audio_record(record, source_root=..., tar_executable=...)`는
원 압축·멤버·파서·도구 식별자를 다시 확인한다. 교정이 없으므로 SampledTrace나
SignalFrame의 Pa/변형률/농도로 자동 내보내는 함수를 제공하지 않는다. 이후 원 장치의
교정/공통 시계/물리 그룹 근거를 확보하고 기존 계측·모니터링 계약에 명시적으로 연결한다.
정수 부호·잘린 payload·스테레오·프레임 초과·손상 원본 시험은 작성했으며 누리온 대기다.


## 교차 정적 검토 보강

두 reader는 수치/파일 hash 외에 실제 선택 catalog entry의 SHA를 기록한다. 원 catalog의
버전·출처·해석/권리 조건이 바뀌면 같은 값이 나와도 이전 receipt replay와 같지 않다.
직접 PCM parser도 Python version·stdlib wave reader source·adapter source SHA를 보존한다.
PCM code의 물리 압력 교정·공통 시계·사건 그룹을 새로 추정하지 않는다.

음향 replay는 archive_filename이 source_root 안의 basename인지 먼저 검증한다. 상위 이동·
드라이브/절대 경로·직접 symlink는 외부 자료/도구 접근 전에 거부한다. member의 dot/empty
구간 정규화나 제어문자도 허용하지 않는다. 실제 선택 archive executable을 시작 전에
해시하고 추출 후 다시 비교하여 tool provenance의 변경을 거부한다. GNU tar/RAR 호환성을
추가로 주장하지 않는다. 새 경로·catalog version·PCM provenance 회귀는 미실행이다.
