"""Exercise public-archive exclusions using generated synthetic archive members."""

import io
import json
import tarfile
import tempfile
import zipfile
from pathlib import Path

from audit_release import audit


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="pipeline-release-boundary-") as temporary:
        root = Path(temporary)
        cases = (
            ("ordinary", "pipeline_integrity/example.py", b"value = 1\n", True),
            ("synthetic", "tests/data/mechanics_input_baselines_223.json", b'{"cases": []}', True),
            ("raw", "data/raw/observation.json", b"{}", False),
            ("unreviewed-fixture", "tests/data/observations.json", b"{}", False),
            ("fixture-directory-as-file", "tests/data", b"not a directory", False),
            ("document", "docs/source.pdf", b"generated sentinel", False),
            ("private-path", "docs/local.txt", ("C:" + "/Users/" + "example/private").encode(), False),
            ("token", "example.txt", ("ghp_" + "x" * 36).encode(), False),
            ("traversal", "../outside.txt", b"no", False),
        )
        for label, member, payload, accepted in cases:
            path = root / (label + ".tar.gz")
            with tarfile.open(path, "w:gz") as archive:
                entry = tarfile.TarInfo("release/" + member)
                entry.size = len(payload)
                archive.addfile(entry, io.BytesIO(payload))
            actual = audit(path)["passed"]
            if actual is not accepted:
                raise AssertionError("Unexpected archive boundary result: " + label)
            results.append({"case": label, "passed": True})
        path = root / "duplicate.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("pipeline_integrity/x.py", b"1")
            archive.writestr("pipeline_integrity/x.py", b"2")
        assert not audit(path)["passed"]
        results.append({"case": "duplicate-wheel-member", "passed": True})
        path = root / "link.whl"
        with zipfile.ZipFile(path, "w") as archive:
            link = zipfile.ZipInfo("pipeline_integrity/link.py")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, b"../outside.py")
        assert not audit(path)["passed"]
        results.append({"case": "wheel-symbolic-link", "passed": True})
    print(json.dumps({"checks": len(results), "passed": len(results), "results": results}))


if __name__ == "__main__":
    main()
