"""Real licensed-source replay; raw data is provided through an explicit HPC data map."""

import hashlib
from pathlib import Path

import pytest

from pipeline_integrity.potash_pipe_cp import (
    SOURCE_SHA256,
    read_potash_pipe_cp,
)


def test_supplier_pinned_pipe_source_replays_without_emitting_observation_rows():
    source = Path("data/raw/potash_cp/Current Density to Protect Carbon Steel in Saturated Brine.docx")
    if not source.is_file():
        pytest.skip("raw source is not configured; Nurion campaign supplies it via the verified data map")
    result = read_potash_pipe_cp(source)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    assert result["source_checksum_verified"] is True
    assert result["row_count"] == 20
    assert result["replicate_count_per_time"] == 3
    assert result["points"][0]["time_min"] == 0
    assert result["points"][-1]["time_min"] == 95
    assert all(len(row["replicate_current_density_mA_cm2"]) == 3 for row in result["points"])
    assert all(row["reported_statistics_recomputed"] is False for row in result["points"])
    assert result["field_transfer_or_cathodic_protection_qualification"] is False
