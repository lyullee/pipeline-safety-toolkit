"""Source parsing and corruption scenarios, pending execution on Nurion."""

import hashlib
from copy import deepcopy

import pytest

from pipeline_integrity import friction_data as module
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import catalog


@pytest.fixture
def source(tmp_path, monkeypatch):
    official = next(s for s in catalog() if s.id == module.DATASET_ID)
    assets = []
    for asset in official.assets:
        raw = (
            ("Hin,Hout,Q,nu\n" + "2,1,0.001,0.000001\n" * 6).encode()
            if asset.filename == "dataset.csv"
            else b"review fixture\n"
        )
        (tmp_path / asset.filename).write_bytes(raw)
        assets.append(
            asset.model_copy(
                update={"bytes": len(raw), "checksum": "sha256:" + hashlib.sha256(raw).hexdigest()}
            )
        )
    spec = official.model_copy(update={"assets": tuple(assets)})
    monkeypatch.setattr(module, "catalog", lambda: (spec,))
    return tmp_path


def test_source_does_not_invent_pressure_or_calibration(source):
    record = module.read_friction_observations(source)
    assert len(record["rows"]) == 6
    assert record["pressure_reference"] is None
    assert record["pressure_head_converted_to_Pa"] is False
    assert record["fitted_roughness"] is None
    assert record["clock"] is None
    assert module.verify_friction_observation_record(record, source_root=source)


def test_changed_raw_is_rejected_before_parse(source):
    p = source / "dataset.csv"
    raw = p.read_bytes()
    p.write_bytes(raw.replace(b"2,1", b"3,1", 1))
    with pytest.raises(ValueError, match="checksum"):
        module.read_friction_observations(source)


def test_resealed_derived_result_still_fails_native_replay(source):
    record = deepcopy(module.read_friction_observations(source))
    record["rows"][0]["inlet_pressure_head_m"] = 9.0
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="replay"):
        module.verify_friction_observation_record(record, source_root=source)


def test_catalog_version_is_bound_even_if_values_are_unchanged(source, monkeypatch):
    record = module.read_friction_observations(source)
    original = module.catalog()[0]
    changed = original.model_copy(update={"version": original.version + " amended"})
    monkeypatch.setattr(module, "catalog", lambda: (changed,))
    with pytest.raises(ValueError, match="replay"):
        module.verify_friction_observation_record(record, source_root=source)
