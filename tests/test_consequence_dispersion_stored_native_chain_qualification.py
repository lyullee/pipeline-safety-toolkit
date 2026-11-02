"""Audit a prior native consequence run against current exact source pins.

This is a bounded replay audit, not a new solver run.  It accepts the stored
chain only when the consequence adapter, all external Python source trees,
backend package trees, runtime versions, and result artifact match their
retained digests.  Physical checks independently integrate the provider's
piecewise-constant release rates, compare coarse/refined source horizons,
check the plane energy residual, and reconcile the cloud ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from importlib.metadata import version
from pathlib import Path

import pytest

from pipeline_integrity import consequence_exchange


ROOT = Path(__file__).resolve().parents[1]
CHAIN = ROOT / "artifacts" / "consequences" / "native_chain.json"
RESULT = ROOT / "research" / "consequence_results.json"


def _path(variable):
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f"external source root is not set: {variable}")
    path = Path(value).resolve()
    if not path.exists():
        pytest.skip(f"external source root is unavailable: {variable}")
    return path


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source_tree_sha(root):
    sources = sorted((root / "src").rglob("*.py"))
    if not sources:
        raise AssertionError(f"no source files under {root / 'src'}")
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.relative_to(root).as_posix().encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest(), len(sources)


def _left_hold_mass(packet):
    rows = packet["steps"]
    duration = float(packet["duration_s"])
    total = 0.0
    for index, row in enumerate(rows):
        end = float(rows[index + 1]["time_s"]) if index + 1 < len(rows) else duration
        start = float(row["time_s"])
        assert end > start
        total += float(row["mass_flow_kg_s"]) * (end - start)
    return total


def test_stored_native_dispersion_chain_matches_pins_and_independent_physics():
    prism_src = _path("PHYSICS_PRISM_SOURCE")
    lh2dt_root = _path("PHYSICS_LH2DT_ROOT")
    slabx_root = _path("PHYSICS_SLABX_ROOT")
    slabx_lh2_root = _path("PHYSICS_SLABX_LH2_ROOT")
    assert CHAIN.is_file() and RESULT.is_file()
    chain = json.loads(CHAIN.read_text(encoding="utf-8"))
    result = json.loads(RESULT.read_text(encoding="utf-8"))

    # Bind this retained output to the current catalog integration code and
    # to the exact output file produced by the prior native chain run.
    assert _sha(ROOT / "src/pipeline_integrity/consequence_exchange.py") == result[
        "implementation_sha256"
    ]
    assert _sha(CHAIN) == result["local_native_record_sha256"]
    assert chain["conditional"][0]["status"] == "computed_diagnostic"
    assert chain["conditional"][0]["physical_validation_claim"] is False
    assert chain["refinement"]["status"] == "computed_diagnostic"
    assert chain["refinement"]["physical_validation_claim"] is False

    # Verify current external source trees against the provenance captured by
    # the native run, not merely against package metadata or repository names.
    for name, root in (
        ("PRISM", prism_src.parent),
        ("lh2dt_models", lh2dt_root),
        ("slabx", slabx_root),
    ):
        current_hash, current_files = _source_tree_sha(root)
        assert current_hash == result["sources"][name]["source_tree_sha256"]
        assert current_files == result["sources"][name]["source_tree_files"]

    slabx_commit = subprocess.run(
        ["git", "-C", str(slabx_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    slabx_lh2_commit = subprocess.run(
        ["git", "-C", str(slabx_lh2_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert slabx_commit == result["sources"]["slabx"]["commit"]
    assert slabx_lh2_commit == result["sources"]["slabx_lh2_commit"]

    # Independently re-create the isolated worker's four package tree pins.
    from lh2_tno.backend import tree_digest

    package_roots = {
        "prism_qra": prism_src / "prism_qra",
        "prism_lh2_models": prism_src / "prism_lh2_models",
        "slabx": slabx_root / "src" / "slabx",
        "slabx_lh2": slabx_lh2_root / "slabx_lh2",
    }
    pinned = {row["name"]: row["source_sha256"] for row in result["backend_packages"]}
    assert set(package_roots) == set(pinned)
    assert {name: tree_digest(path) for name, path in package_roots.items()} == pinned
    for name, expected in result["required_versions"].items():
        assert version(name) == expected

    # Independent zero-order-hold integrals must reproduce both native release
    # mass totals.  The two source discretizations then provide a refinement
    # check before the cloud backend receives the boundary.
    coarse = chain["source"]["native_packet"]
    fine = chain["refined_source"]["native_packet"]
    coarse_integral = _left_hold_mass(coarse)
    fine_integral = _left_hold_mass(fine)
    assert coarse_integral == pytest.approx(coarse["cumulative_mass_out_kg"], rel=1e-10, abs=1e-14)
    assert fine_integral == pytest.approx(fine["cumulative_mass_out_kg"], rel=1e-10, abs=1e-14)
    assert len(coarse["steps"]) == 20 and len(fine["steps"]) == 40
    assert coarse["duration_s"] == fine["duration_s"] == 1.0
    assert abs(coarse_integral - fine_integral) / fine_integral < 0.01

    provider_ledger = coarse["provider_mass_balance"]
    independently_closed_mass = (
        provider_ledger["initial_mass_kg"]
        - provider_ledger["final_mass_kg"]
        - coarse_integral
    )
    assert independently_closed_mass == pytest.approx(0.0, abs=1e-12)
    assert provider_ledger["derived_accounting_residual_kg"] == pytest.approx(
        independently_closed_mass, abs=1e-14
    )

    # The native chain's independently retained plane audit and end-to-end
    # mass closure must both meet the stricter limits used by the client.
    computed = chain["conditional"][0]
    plane_residual = computed["plane_evidence"]["generation_audit"][
        "max_energy_residual_j_per_kg"
    ]
    assert abs(plane_residual) <= 1e-6
    data = computed["native_result"]["data"]
    assert data["full_chain_executed"] is True
    assert data["physical_validation_claim"] is False
    ledger = data["mass_ledger"]
    assert ledger["released_kg"] == pytest.approx(coarse_integral, rel=1e-8, abs=1e-14)
    assert ledger["end_to_end_residual_kg"] == pytest.approx(0.0, abs=1e-12)
    receptor = next(row for row in data["receptors"] if row["id"] == "near")
    assert 0.0 <= receptor["harm_given_observed_release_low_high"][0]
    assert receptor["harm_given_observed_release_low_high"][0] <= receptor[
        "harm_given_observed_release_low_high"
    ][1] <= 1.0
