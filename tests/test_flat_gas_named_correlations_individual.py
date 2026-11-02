"""Separate engineering-unit references for two flat-segment gas correlations."""
import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CONTEXT=RunContext(asset_id="flat_segment",scenario_id="independent_named_reference",
                   clock_id="steady",origin="synthetic")
BASE=dict(relative_density_to_air=.65,mean_temperature_k=295.,mean_compressibility=.92,
          length_m=24000.,diameter_m=.3,inlet_pressure_pa_abs=8e6,outlet_pressure_pa_abs=5.5e6,
          standard_temperature_k=288.15,standard_pressure_pa_abs=101325.,empirical_efficiency=.93)
POWERS={"Weymouth":(1.,1.,.5,2.667,.0037435),
        "Panhandle_B":(1.02,.961,.51,2.53,.01002)}


def flow(method,p):
    return evaluate("hydraulics.gas_correlation",dict(p,method=method),CONTEXT).values["standard_volume_flow_m3_s"]


def engineering_reference(method,p):
    state_exp,sg_exp,drop_exp,diameter_exp,factor=POWERS[method]
    pressure_square_drop=(p["inlet_pressure_pa_abs"]/1000)**2-(p["outlet_pressure_pa_abs"]/1000)**2
    denominator=p["relative_density_to_air"]**sg_exp*p["mean_temperature_k"]*(p["length_m"]/1000)*p["mean_compressibility"]
    return (factor*p["empirical_efficiency"]
            *(p["standard_temperature_k"]/(p["standard_pressure_pa_abs"]/1000))**state_exp
            *(pressure_square_drop/denominator)**drop_exp
            *(p["diameter_m"]*1000)**diameter_exp/86400)


@pytest.mark.parametrize("method", POWERS)
@pytest.mark.parametrize("changes", [{},dict(diameter_m=.45,length_m=76000.,outlet_pressure_pa_abs=3e6),
    dict(relative_density_to_air=.72,mean_temperature_k=310.,standard_temperature_k=293.15,
         standard_pressure_pa_abs=100000.,mean_compressibility=.85,empirical_efficiency=.87)])
def test_named_flat_formula_matches_separate_kpa_km_mm_day_coordinates(method,changes):
    p=BASE|changes
    assert flow(method,p)==pytest.approx(engineering_reference(method,p),rel=2e-12)


@pytest.mark.parametrize("method", POWERS)
def test_declared_pressure_reference_and_efficiency_scaling(method):
    q=flow(method,BASE)
    a,_,c,_,_=POWERS[method]
    scaled=BASE|dict(inlet_pressure_pa_abs=1.3*BASE["inlet_pressure_pa_abs"],
                    outlet_pressure_pa_abs=1.3*BASE["outlet_pressure_pa_abs"])
    assert flow(method,scaled)/q==pytest.approx(1.3**(2*c),rel=2e-12)
    assert flow(method,BASE|dict(standard_pressure_pa_abs=2*BASE["standard_pressure_pa_abs"]))/q==pytest.approx(2**-a,rel=2e-12)
    assert flow(method,BASE|dict(empirical_efficiency=.465))/q==pytest.approx(.5,rel=2e-12)


@pytest.mark.parametrize("method", POWERS)
@pytest.mark.parametrize("changes", [dict(outlet_pressure_pa_abs=8e6),dict(empirical_efficiency=1.1)])
def test_invalid_direction_or_efficiency_is_rejected(method,changes):
    with pytest.raises(ValueError):
        flow(method,BASE|changes)
