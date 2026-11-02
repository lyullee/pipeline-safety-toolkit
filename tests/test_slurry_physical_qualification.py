import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import slurry_transport as st
from pipeline_integrity.datasets import file_digest

_spec = importlib.util.spec_from_file_location(
    "slurry_example", Path(__file__).parents[1] / "examples" / "slurry_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def run(**changes):
    q, c = _example.make_case(reviewed=True, **changes)
    result = st.evaluate_slurry_transport(q, c)
    assert result["status"] == "computed_diagnostic", result["notes"]
    return q, c, result


def test_bingham_flow_and_pressure_close_independent_mass_and_solids_ledgers():
    q, c, r = run()
    v, p = r["values"], q.properties
    # Independent Buckingham-Reiner closed form for n=1 Bingham laminar flow.
    tau = v["wall_shear_stress_Pa"]
    a = p.yield_stress_Pa / tau
    radius = q.inner_diameter_m / 2
    q_oracle = math.pi * radius**3 * tau / (4 * p.consistency_Pa_s_n) * (1 - 4*a/3 + a**4/3)
    assert q_oracle == pytest.approx(q.volume_flow_m3_s, rel=2e-7, abs=1e-10)
    assert v["mass_flow_kg_s"] == pytest.approx(p.mixture_density_kg_m3*q.volume_flow_m3_s)
    assert v["solid_volume_flow_m3_s"] + v["carrier_volume_flow_m3_s"] == pytest.approx(q.volume_flow_m3_s)
    assert p.mixture_density_kg_m3 == pytest.approx((1-p.solid_volume_fraction)*p.carrier_density_kg_m3 + p.solid_volume_fraction*p.particle_density_kg_m3)
    assert v["pressure_work_balance_residual_W"] == pytest.approx(0.0, abs=v["pressure_work_balance_limit_W"])
    assert st.verify_slurry_transport_record(r, request=q, context=c)["valid"]


def test_signed_flow_reversal_preserves_positive_dissipation_and_signed_solid_flux():
    q, _, r = run(volume_flow_m3_s=-0.0001)
    v = r["values"]
    assert v["mass_flow_kg_s"] < 0 and v["solid_volume_flow_m3_s"] < 0
    assert v["carrier_volume_flow_m3_s"] < 0
    assert v["signed_friction_pressure_drop_Pa"] < 0
    assert v["dissipated_power_W"] > 0
    assert v["pressure_work_balance_residual_W"] == pytest.approx(0.0, abs=v["pressure_work_balance_limit_W"])


def test_zero_flow_has_zero_component_flux_and_zero_dissipation():
    _, _, r = run(volume_flow_m3_s=0.0)
    v = r["values"]
    assert v["mass_flow_kg_s"] == v["solid_volume_flow_m3_s"] == v["carrier_volume_flow_m3_s"] == 0.0
    assert v["dissipated_power_W"] == 0.0
    assert v["pressure_prediction_unique"] is False


def test_unsupported_concentration_and_density_mismatch_are_withheld():
    q, c = _example.make_case(reviewed=True)
    p = q.properties.model_copy(update={"solid_volume_fraction": 0.25})
    q, c = _example.make_case(reviewed=True, properties=p)
    out = st.evaluate_slurry_transport(q, c)
    assert out["status"] == "out_of_scope" and out["values"] is None
    assert "concentration" in out["notes"][0]
    q, c = _example.make_case(reviewed=True)
    p = q.properties.model_copy(update={"mixture_density_kg_m3": 1200.0})
    q, c = _example.make_case(reviewed=True, properties=p)
    out = st.evaluate_slurry_transport(q, c)
    assert out["status"] == "out_of_scope" and out["values"] is None
    assert "density ledger" in out["notes"][0]


def test_source_pin_matches_executed_slurry_implementation():
    expected = _example.make_case(reviewed=True)[0].owner_pins["implementation_sha256"]
    assert file_digest(Path(st.__file__)) == expected
