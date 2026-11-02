"""Bounded HTTP range behavior; supplier digest acceptance stays in the acquisition supervisor."""

import hashlib
import io

import pytest

from pipeline_integrity._download import transfer


class Response(io.BytesIO):
    def __init__(self, body, status, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


def request(tmp_path, body, **changes):
    return (
        dict(
            url="https://example.invalid/pinned.zip",
            target=str(tmp_path / "asset.partial"),
            maximum_bytes=100,
            expected_bytes=len(body),
            checksum="sha256:" + "0" * 64,
        )
        | changes
    )


def test_pinned_partial_resumes_at_exact_offset(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body)
    (tmp_path / "asset.partial").write_bytes(body[:4])
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        return Response(body[4:], 206, {"Content-Range": "bytes 4-9/10"})

    assert transfer(spec, opener=open_)["bytes"] == 10
    assert seen == ["bytes=4-9"] and (tmp_path / "asset.partial").read_bytes() == body


def test_ignored_range_restarts_instead_of_appending(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body)
    (tmp_path / "asset.partial").write_bytes(b"wrong")
    assert transfer(spec, opener=lambda *a, **k: Response(body, 200))["bytes"] == 10
    assert (tmp_path / "asset.partial").read_bytes() == body


@pytest.mark.parametrize("algorithm", ["md5", "sha256", "sha512"])
def test_complete_valid_partial_needs_no_network(tmp_path, algorithm):
    body = b"0123456789"
    spec = request(tmp_path, body, checksum=f"{algorithm}:{hashlib.new(algorithm, body).hexdigest()}")
    (tmp_path / "asset.partial").write_bytes(body)

    def unexpected_request(*a, **k):
        pytest.fail("a checksum-valid complete partial must not open the network")

    assert transfer(spec, opener=unexpected_request)["bytes"] == len(body)
    assert (tmp_path / "asset.partial").read_bytes() == body


@pytest.mark.parametrize("status", [200, 206])
def test_complete_invalid_partial_restarts_after_connection_retry(tmp_path, status):
    body = b"0123456789"
    spec = request(tmp_path, body, checksum="sha256:" + hashlib.sha256(body).hexdigest())
    (tmp_path / "asset.partial").write_bytes(b"corruption")
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        if len(seen) == 1:
            raise TimeoutError("controlled connection failure before replacement")
        return Response(body, status, {"Content-Range": "bytes 0-9/10"} if status == 206 else {})

    assert transfer(spec, opener=open_)["bytes"] == len(body)
    assert seen == ["bytes=0-9", "bytes=0-9"]
    assert (tmp_path / "asset.partial").read_bytes() == body


def test_complete_invalid_partial_survives_rejected_response(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body, checksum="sha256:" + hashlib.sha256(body).hexdigest())
    corrupt = b"corruption"
    (tmp_path / "asset.partial").write_bytes(corrupt)

    with pytest.raises(ValueError, match="range response"):
        transfer(spec, opener=lambda *a, **k: Response(body, 206, {"Content-Range": "bytes 1-9/10"}))

    assert (tmp_path / "asset.partial").read_bytes() == corrupt


@pytest.mark.parametrize("value", ["bytes 0-9/10", "bytes 4-9/11", "bytes 4-8/10", ""])
def test_inconsistent_range_or_total_is_rejected(tmp_path, value):
    body = b"0123456789"
    spec = request(tmp_path, body)
    (tmp_path / "asset.partial").write_bytes(body[:4])
    with pytest.raises(ValueError, match="range response"):
        transfer(spec, opener=lambda *a, **k: Response(body[4:], 206, {"Content-Range": value}))
    assert (tmp_path / "asset.partial").read_bytes() == body[:4]


def test_short_body_retries_only_missing_suffix(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body)
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        return (
            Response(body[:4], 206, {"Content-Range": "bytes 0-9/10"})
            if len(seen) == 1
            else Response(body[4:], 206, {"Content-Range": "bytes 4-9/10"})
        )

    assert transfer(spec, opener=open_)["bytes"] == 10 and seen == ["bytes=0-9", "bytes=4-9"]


def test_short_ignored_range_resumes_from_replacement_body(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body)
    (tmp_path / "asset.partial").write_bytes(b"wrong")
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        return (
            Response(body[:3], 200)
            if len(seen) == 1
            else Response(body[3:], 206, {"Content-Range": "bytes 3-9/10"})
        )

    assert transfer(spec, opener=open_)["bytes"] == len(body)
    assert seen == ["bytes=5-9", "bytes=3-9"]
    assert (tmp_path / "asset.partial").read_bytes() == body


def test_corrupt_prefix_can_be_recovered_after_completed_resume(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body, checksum="sha256:" + hashlib.sha256(body).hexdigest())
    (tmp_path / "asset.partial").write_bytes(b"wrong")
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        return (
            Response(body[5:], 206, {"Content-Range": "bytes 5-9/10"})
            if len(seen) == 1
            else Response(body, 206, {"Content-Range": "bytes 0-9/10"})
        )

    assert transfer(spec, opener=open_)["bytes"] == len(body)
    assert (tmp_path / "asset.partial").read_bytes() == b"wrong56789"
    assert transfer(spec, opener=open_)["bytes"] == len(body)
    assert seen == ["bytes=5-9", "bytes=0-9"]
    assert (tmp_path / "asset.partial").read_bytes() == body


def test_unknown_supplier_digest_cannot_resume_untrusted_prefix(tmp_path):
    body = b"0123456789"
    spec = request(tmp_path, body, checksum=None)
    (tmp_path / "asset.partial").write_bytes(b"wrong")
    seen = []

    def open_(req, **kwargs):
        seen.append(req.get_header("Range"))
        return Response(body, 200)

    assert transfer(spec, opener=open_)["bytes"] == 10 and seen == [None]
    assert (tmp_path / "asset.partial").read_bytes() == body


def test_size_budget_and_retry_budget_are_finite(tmp_path):
    spec = request(tmp_path, b"123", maximum_bytes=2)
    with pytest.raises(ValueError, match="budget"):
        transfer(spec)
    calls = []

    def fail(*a, **k):
        calls.append(1)
        raise TimeoutError("controlled connection failure")

    with pytest.raises(TimeoutError):
        transfer(request(tmp_path, b"123"), opener=fail)
    assert len(calls) == 4
