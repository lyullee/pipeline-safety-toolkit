"""Large archive integration; the HPC controller supplies the reviewed shared root."""

import os
import shutil
from pathlib import Path

from pipeline_integrity.leak_multisensor_data import inventory_leak_multisensor_archive


def test_shared_hpc_archive_inventory_when_private_fixture_is_configured():
    configured = os.environ.get("PIPELINE_HPC_RAW_ROOT")
    if configured is None:
        # Ordinary source-only runs do not claim raw-data execution. The frozen HPC data fixture
        # makes this environment variable mandatory for a targeted raw-data campaign.
        assert os.environ.get("PIPELINE_HPC_CAMPAIGN_KIND") != "targeted", (
            "targeted leak archive campaign requires its reviewed shared data map"
        )
        return
    root = Path(configured).resolve(strict=True)
    archive = (
        root
        / "leak_multisensor_transients"
        / "Dataset of Leak Simulations in Experimental Testbed Water Network.7z"
    )
    tar = shutil.which("tar")
    assert tar is not None
    record = inventory_leak_multisensor_archive(
        archive,
        tar_executable=Path(tar),
        byte_budget=1024 * 1024,
        timeout_s=300,
    )
    assert record["archive"] == {
        "filename": "Dataset of Leak Simulations in Experimental Testbed Water Network.7z",
        "bytes": 717637500,
        "sha256": "c6d4a68901a145155a58ccb691769753cb7c0960416a375acac12128bedfd881",
    }
    assert record["families"] == {
        "accelerometer": {"files": 80, "bytes": 1848001201, "suffix": ".csv"},
        "dynamic_pressure": {"files": 80, "bytes": 1883226891, "suffix": ".csv"},
        "hydrophone": {"files": 122, "bytes": 150702480, "suffix": ".raw"},
    }
