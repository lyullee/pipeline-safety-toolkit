"""Isolated download worker so the supervising process can enforce a wall-clock limit."""

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path


def transfer(request, *, opener=urllib.request.urlopen):
    """Resume only size/checksum-pinned assets; the supervisor verifies the complete digest before publication."""
    target = Path(request["target"])
    maximum = request["maximum_bytes"]
    expected = request.get("expected_bytes")
    resumable = bool(request.get("checksum")) and isinstance(expected, int) and expected > 0
    offset = target.stat().st_size if resumable and target.exists() else 0
    if offset > maximum or resumable and (offset > expected or expected > maximum):
        raise ValueError("partial or expected asset exceeds the declared acquisition budget")
    if resumable and offset == expected:
        algorithm, digest = request["checksum"].split(":", 1)
        current = hashlib.new(algorithm)
        with target.open("rb") as partial:
            while block := partial.read(1024 * 1024):
                current.update(block)
        if current.hexdigest() != digest.lower():
            offset = 0
    retries = 0
    while not resumable or offset < expected:
        headers = {"User-Agent": "PipelineIntegrityToolkit/0.1"}
        end = min(offset + 8 * 1024 * 1024 - 1, expected - 1) if resumable else None
        if resumable:
            headers["Range"] = f"bytes={offset}-{end}"
        source = urllib.request.Request(request["url"], headers=headers)
        start = offset
        output_opened = False
        try:
            with opener(source, timeout=30) as response:
                ranged = response.status == 206
                if ranged:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                    )
                    if (
                        not resumable
                        or not match
                        or tuple(map(int, match.groups())) != (offset, end, expected)
                    ):
                        raise ValueError("range response does not match the pinned asset interval and total")
                    remaining = end - offset + 1
                    mode = "ab" if offset else "wb"
                else:
                    if response.status != 200:
                        raise ValueError("unrecognized download response status")
                    # A server that ignores Range supplies a fresh complete body, never an append.
                    offset = 0
                    remaining = expected if resumable else None
                    mode = "wb"
                with target.open(mode) as output:
                    output_opened = True
                    while remaining is None or remaining > 0:
                        block = response.read1(
                            min(64 * 1024, remaining) if remaining is not None else 64 * 1024
                        )
                        if not block:
                            if remaining:
                                raise OSError("download body ended before its declared interval")
                            break
                        offset += len(block)
                        if offset > maximum:
                            raise ValueError("source exceeded the declared acquisition budget")
                        output.write(block)
                        if remaining is not None:
                            remaining -= len(block)
                if not ranged:
                    break
                retries = 0
        except (OSError, TimeoutError):
            retries += 1
            if not resumable or retries > 3:
                raise
            offset = target.stat().st_size if output_opened else start
    return dict(bytes=offset)


def main():
    print(json.dumps(transfer(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
