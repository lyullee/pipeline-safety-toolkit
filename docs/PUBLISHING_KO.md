# GitHub·PyPI·Zenodo 공개 배포 절차

## 공개 식별자

- GitHub: `https://github.com/lyullee/pipeline-safety-toolkit`
- Python 배포명: `pipeline-safety-toolkit`
- import: `pipeline_integrity`
- CLI: `pipeline-safety-toolkit` 및 호환용 `pipeline-integrity`
- 알파 버전: `0.1.0a1`
- release tag: `v0.1.0a1`

공개 이력에는 원 관측이나 개인 참고 문서를 넣지 않는다. 필수 라이선스 고지는 보존한다. 문장 중복 검사와 파일 제외 검사만으로 모든 권리를 보장할 수는 없으므로, 제3자 자료의 조건을 확인하고 불확실한 데이터·표·계수는 배포하지 않는다.

## 1. 배포를 위한 고정

현재 소스를 작업 중인 검증 캠페인과 구분하고 `artifacts/publication-preflight/RELEASE_FREEZE.json`이 있으면 다른 자동 작업은 release snapshot을 수정하지 않는다. 배포명과 버전을 `pyproject.toml` 및 `pipeline_integrity.__version__`에서 맞춘다. 계산 API를 바꾸지 않는 배포명 변경도 실제 설치·import·CLI를 확인한다.

## 2. 준비와 검증

```bash
python -m pip install ".[engineering,thermo,structural,dev]" twine
python -m ruff check src tests scripts examples --select E9,F821,F822,F823
python scripts/check_publication_boundaries.py
python scripts/run_public_checks.py
python -m build --wheel
python scripts/check_isolated_core.py
python -m build --sdist
python -m twine check dist/*
python scripts/audit_release.py --check-consistency
```

wheel을 먼저 검사하고 그 wheel의 clean-install receipt를 만든 뒤 sdist를 생성한다. 그래야 sdist에 들어간 core receipt가 실제 wheel의 checksum과 일치한다. 기존 dist에 다른 버전이 있으면 새 검토 출력 경로를 사용하고 검토되지 않은 파일을 한꺼번에 업로드하지 않는다.

공개 source archive는 다음 도구로 만들 수 있다.

```bash
python scripts/export_public_repository.py --output artifacts/publication-preflight/pipeline-safety-toolkit-public-source.zip
```

이 ZIP은 Git-visible allowlist로 검토된 파일만 포함한다. 원자료·문서·환경·자격증명을 제외하고 파일별 해시를 sidecar receipt에 남긴다. source ZIP을 wheel처럼 설치하지 않는다.

## 3. GitHub

공개 저장소의 root에 검토한 프로젝트 파일만 넣고 원자료가 있는 상위 폴더를 push하지 않는다. 첫 공개 커밋 이전에 `git ls-files`/diff와 archive 목록을 확인한다. Git 이력에 비밀이나 비공개 자료가 한 번 포함되면 단순 삭제 커밋으로 공개 이력이 사라지는 것은 아니다.

검토가 끝나면 공개 커밋, tag와 GitHub Release에 wheel/sdist/검토된 source ZIP 및 체크섬을 연결한다. 원격 Actions의 정확성·clean core·portable subset 결과를 로컬 결과와 구분해 확인한다. advisory style job의 기술 부채를 정적 정확성 통과로 숨기지 않는다.

## 4. PyPI Trusted Publishing

이 프로젝트는 GitHub Actions OIDC를 이용하는 `.github/workflows/publish.yml`을 제공한다. 장기 API token을 소스에 넣지 않는다. PyPI의 새 프로젝트용 pending publisher는 계정에서 한 번 연결해야 한다.

입력할 값은 다음과 같다.

| 필드 | 값 |
|---|---|
| PyPI project name | `pipeline-safety-toolkit` |
| GitHub owner | `lyullee` |
| Repository | `pipeline-safety-toolkit` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

공식 안내: [새 프로젝트 Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/), [Publisher 설정](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

연결이 완료된 뒤 검토한 tag를 대상으로 workflow를 수동 실행한다.

```bash
gh workflow run publish.yml --repo lyullee/pipeline-safety-toolkit --ref v0.1.0a1
```

등록 전 PyPI 페이지가 없거나 프로젝트 이름이 사용 가능하다는 확인만으로 게시 완료라고 표현하지 않는다. 실제 upload와 PyPI JSON에서 버전·파일 checksum을 확인해야 완료다. 새 환경의 `pip install --pre pipeline-safety-toolkit==0.1.0a1`로 실제 공개 설치를 확인한다.

## 5. Zenodo

`.zenodo.json`과 `CITATION.cff`에 소프트웨어 제목·저자·버전·MIT 라이선스와 저장소 URL을 포함했다. 실제 DOI를 받기 전에는 DOI badge나 임의 식별자를 만들지 않는다.

두 가지 방법을 사용할 수 있다.

1. Zenodo 계정의 GitHub 연결에서 이 저장소를 활성화하고 release를 archive한다.
2. Software 기록을 새로 만들어 검토한 source ZIP과 metadata를 업로드하고 공개한다.

공식 안내: [GitHub release archive](https://help.zenodo.org/docs/github/archive-software/github-upload/).

GitHub release를 먼저 공개했다면 Zenodo에서 나중에 활성화한 기존 tag가 자동 보관되었다고 가정하지 않는다. 실제 record와 DOI를 확인한다. 수동 업로드 시 같은 소스를 여러 새 record로 중복 공개하지 않고 버전 관계를 유지한다.

## 6. 완료 판정

- GitHub의 공개 파일·tag·release와 원격 commit SHA가 검토 소스와 일치한다.
- PyPI에 정확한 버전의 wheel/sdist가 있고 checksum이 검토 산출물과 일치한다.
- Zenodo에 공개 Software record와 실제 DOI가 있으며 파일·라이선스·버전이 맞다.
- 알려진 미검증25개·실측 실패4건·물성 제한이 숨겨지지 않는다.
- 게시하지 못한 서비스는 준비 완료와 실제 게시 완료를 구분해 기록한다.

공개 플랫폼의 로그인·2FA·보안 설정은 사용자가 직접 수행해야 할 수 있다. browser UI가 안전하게 확인되지 않으면 조작을 중단하고 준비한 파일과 정확한 남은 단계를 전달한다. 다른 플랫폼 게시가 막혀도 완료 가능한 로컬 검토와 승인된 GitHub 배포는 구분해서 진행한다.
