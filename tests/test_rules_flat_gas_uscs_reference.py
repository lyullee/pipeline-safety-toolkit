"""USCS rounded-coefficient interval check, independent of base-SI prefactors."""
import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CONTEXT=RunContext(asset_id="flat_uscs_reference",scenario_id="authored",clock_id="steady",origin="synthetic")
BASE=dict(relative_density_to_air=.66,mean_temperature_k=300.,mean_compressibility=.9,length_m=20000.,
    diameter_m=.35,inlet_pressure_pa_abs=7e6,outlet_pressure_pa_abs=5e6,standard_temperature_k=288.15,
    standard_pressure_pa_abs=101325.,empirical_efficiency=.92)


def uscs(p,method,coefficient):
    psi=.45359237*9.80665/.0254**2
    state_exp,sg_exp,drop_exp,diam_exp=(1.,1.,.5,2.667) if method=="Weymouth" else (1.02,.961,.51,2.53)
    pressure_drop=(p["inlet_pressure_pa_abs"]/psi)**2-(p["outlet_pressure_pa_abs"]/psi)**2
    denominator=p["relative_density_to_air"]**sg_exp*(p["mean_temperature_k"]*1.8)*(p["length_m"]/1609.344)*p["mean_compressibility"]
    q=coefficient*p["empirical_efficiency"]*((p["standard_temperature_k"]*1.8)/(p["standard_pressure_pa_abs"]/psi))**state_exp
    q*=(pressure_drop/denominator)**drop_exp*(p["diameter_m"]/.0254)**diam_exp
    return q*.3048**3/86400


@pytest.mark.parametrize("method,lo,hi",[("Weymouth",433.45,433.55),("Panhandle_B",736.5,737.5)])
@pytest.mark.parametrize("changes",[{},dict(diameter_m=.5,length_m=50000.),dict(mean_compressibility=.8,
    mean_temperature_k=320.,standard_temperature_k=293.15,standard_pressure_pa_abs=100000.)])
def test_flat_uscs_relation_matches_rounded_leading_coefficient_interval(method,lo,hi,changes):
    p=BASE|changes
    q=evaluate("hydraulics.gas_correlation",dict(p,method=method),CONTEXT).values["standard_volume_flow_m3_s"]
    assert uscs(p,method,lo)<=q<=uscs(p,method,hi)
