"""Neutral-geometry liquid Cv definition compared through an existing Kv API."""
import math
import pytest
from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX=RunContext(asset_id="authored-valve",scenario_id="neutral-liquid-Cv",clock_id="steady",origin="synthetic")
REFERENCE_WATER_DENSITY=999.10329075702327
US_GALLON=231*.0254**3
PSI_PA=.45359237*9.80665/.0254**2


def run(flow,drop,sg=1.0):
    return evaluate("equipment.control_valve_liquid",dict(density_kg_m3=sg*REFERENCE_WATER_DENSITY,
        vapour_pressure_pa_abs=2000.0,critical_pressure_pa_abs=22.06e6,viscosity_pa_s=.001,
        inlet_pressure_pa_abs=500000.0,outlet_pressure_pa_abs=500000.0-drop,
        volume_flow_m3_s=flow,inlet_diameter_m=.1,outlet_diameter_m=.1,valve_diameter_m=.1,
        pressure_recovery_factor=.9,valve_style_factor=.46),CTX)


@pytest.mark.parametrize("flow,drop,sg",[(.01,1000.0,1.0),(.02,4000.0,1.05),(.006,2000.0,.8)])
def test_us_capacity_definition_and_exact_unit_transform_match_actual_kv(flow,drop,sg):
    result=run(flow,drop,sg)
    assert result.status=="computed"
    assert result.values["diagnostics"]["FP"] is None
    assert result.values["diagnostics"]["FR"] is None
    required_cv=(flow*60/US_GALLON)*math.sqrt(sg/(drop/PSI_PA))
    # Derived solely from gpm/psi versus m3/hour/bar coefficient definitions.
    cv_per_kv=(1/(60*US_GALLON))*math.sqrt(PSI_PA/100000)
    assert result.values["kv"]*cv_per_kv==pytest.approx(required_cv,rel=2e-8)


def test_neutral_unchecked_flow_pressure_similarity_preserves_capacity():
    a=run(.01,1000.0).values["kv"]
    b=run(.02,4000.0).values["kv"]
    assert b==pytest.approx(a,rel=2e-8)
