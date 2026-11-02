# 대용량 ZIP 원본 인입 경계

## File-backed checksum access

`pipeline_integrity.source_archive.pinned_source_zip_stream`는 ZIP 전체를 Python 메모리에 복사하지 않고 같은 열린 file handle에서 공급자 checksum을 확인한 뒤 central directory를 읽고 종료 record와 연속 배치인지 확인한다. archive·개별 member·전체 uncompressed byte·member 수 예산은 호출자가 반드시 정수로 지정한다. 경로가 일반 파일인지, 공급자 크기 및 checksum이 맞는지, 사용 중 inode/크기/수정시각과 파일 바이트가 바뀌지 않았는지 확인한다. `deferred_capacity` 취득자료는 `allow_deferred_capacity=True`를 명시해야 하며, 권리 미검토나 공급자 pin 부재는 opt-in으로 우회할 수 없다.

ZIP 경로 중복, 대소문자 충돌, 파일/하위 경로 충돌, traversal, symlink/special/encrypted member, 미지원 compression method 및 byte/count 예산 초과는 거부한다. archive를 풀지 않고, 함수는 CSV schema·단위·시간 의미를 해석하지 않는다. central-directory metadata 검사는 모든 파일 내용이나 모든 member CRC를 판독했다는 뜻이 아니다. 선택 member를 실제 읽으면 `ZipFile`이 해당 member의 CRC를 검사한다. 대용량 source adapter는 member를 읽기 전 자체 row/byte/decompression budget을 적용해야 한다.

## LeakDB 대용량 archive

[LeakDB Zenodo v1](https://zenodo.org/records/13985057)은 공식 API에 CC BY 4.0, 13,888,061,642 bytes, `LeakDB.zip`, MD5 `d607929ef7caea432e663aee8071f2d7`로 등재되어 있다. GitHub publisher README는 현실적 누수 시나리오와 benchmark scoring toolkit, 데이터 취득과 citation을 설명한다([KIOS-Research/LeakDB](https://github.com/KIOS-Research/LeakDB)). 대용량 파일을 받기 전에도 metadata pin을 확인할 수 있지만, 실제 파일을 검사하기 전에는 archive member/schema/CSV 열·단위·시계·scenario grouping을 확정하지 않는다. generation toolkit/software rights는 dataset file rights와 별도로 검토한다.

원본 파일을 확보하면 raw 경로를 직접 지정해 bounded metadata inventory를 실행한다. 다음 예산은 예시일 뿐이며 실제 저장공간·메모리·압축비에 맞춰 승인 가능한 한도로 정한다.

```python
from pathlib import Path
from pipeline_integrity.source_archive import pinned_source_zip_stream

with pinned_source_zip_stream(
    Path("data/raw/leakdb/LeakDB.zip"),
    "leakdb",
    maximum_archive_bytes=16 * 1024**3,
    maximum_member_bytes=2 * 1024**3,
    maximum_total_bytes=1024 * 1024**3,
    maximum_members=250_000,
    maximum_central_directory_bytes=256 * 1024**2,
    allow_deferred_capacity=True,
) as (archive, provenance):
    inventory = [
        {"name": item.filename, "compressed_bytes": item.compress_size,
         "uncompressed_bytes": item.file_size, "crc32": f"{item.CRC:08x}"}
        for item in archive.infolist()
    ]
```

이 예시는 central directory 목록만 순회한다. `CRC` 표시는 archive metadata에 기록된 예정값이지 검사 완료 증거가 아니다. 후속 reader는 실제 member를 bounded read하고 publisher schema·row identity·time/label semantics를 검토한 뒤 별도 receipt와 row-level provenance를 생성해야 한다. 전체 raw archive나 관측 CSV를 소스 bundle에 포함하지 않는다.
