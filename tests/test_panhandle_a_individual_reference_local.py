"""Independent standard-volume correlation reference in two engineering unit bases."""
import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX=RunContext(asset_id="flat-gas-segment",scenario_id="authored-correlation-reference",
               clock_id="steady",origin="synthetic")
BASE=dict(method="Panhandle_A",relative_density_to_air=0.65,mean_temperature_k=295.0,
    mean_compressibility=0.92,length_m=24000.0,diameter_m=0.3,
    inlet_pressure_pa_abs=8e6,outlet_pressure_pa_abs=5.5e6,
    standard_temperature_k=288.15,standard_pressure_pa_abs=101325.0,
    empirical_efficiency=0.93)


def flow(p):
    return evaluate("hydraulics.gas_correlation",p,CTX).values["standard_volume_flow_m3_s"]


def engineering_si_reference(p):
    # kPa, km, mm and m3/day coordinates are independent of the library's base-SI form.
    drop=(p["inlet_pressure_pa_abs"]/1000)**2-(p["outlet_pressure_pa_abs"]/1000)**2
    denominator=(p["relative_density_to_air"]**0.8539*p["mean_temperature_k"]
                 *(p["length_m"]/1000)*p["mean_compressibility"])
    return (4.5965e-3*p["empirical_efficiency"]
        *(p["standard_temperature_k"]/(p["standard_pressure_pa_abs"]/1000))**1.0788
        *(drop/denominator)**0.5394*(p["diameter_m"]*1000)**2.6182/86400)


def uscs_reference(p,coefficient):
    psi=0.45359237*9.80665/(0.0254**2)
    drop=(p["inlet_pressure_pa_abs"]/psi)**2-(p["outlet_pressure_pa_abs"]/psi)**2
    denominator=(p["relative_density_to_air"]**0.8539*(p["mean_temperature_k"]*1.8)
                 *(p["length_m"]/1609.344)*p["mean_compressibility"])
    scfd=(coefficient*p["empirical_efficiency"]
        *((p["standard_temperature_k"]*1.8)/(p["standard_pressure_pa_abs"]/psi))**1.0788
        *(drop/denominator)**0.5394*(p["diameter_m"]/0.0254)**2.6182)
    return scfd*0.3048**3/86400


@pytest.mark.parametrize("updates",[{},dict(diameter_m=0.45,length_m=76000.0,
    inlet_pressure_pa_abs=9e6,outlet_pressure_pa_abs=3e6),
    dict(relative_density_to_air=0.72,mean_temperature_k=310.0,
         standard_temperature_k=293.15,standard_pressure_pa_abs=100000.0,
         mean_compressibility=0.85,empirical_efficiency=0.87)])
def test_panhandle_a_matches_independent_unit_forms_and_published_precision(updates):
    p=BASE|updates;q=flow(p)
    assert q==pytest.approx(engineering_si_reference(p),rel=2e-12)
    # The USCS coefficient is specified to two decimal places; compare its
    # half-last-place interval instead of requiring falsely exact unit equality.
    assert uscs_reference(p,435.865) <= q <= uscs_reference(p,435.875)


def test_flow_reference_basis_and_pressure_scaling_follow_declared_exponents():
    q=flow(BASE)
    assert flow(BASE|dict(inlet_pressure_pa_abs=1.5*BASE["inlet_pressure_pa_abs"],
        outlet_pressure_pa_abs=1.5*BASE["outlet_pressure_pa_abs"])) / q == pytest.approx(1.5**1.0788,rel=2e-12)
    assert flow(BASE|dict(standard_pressure_pa_abs=2*BASE["standard_pressure_pa_abs"])) / q == pytest.approx(2**-1.0788,rel=2e-12)


@pytest.mark.parametrize("updates",[dict(outlet_pressure_pa_abs=8e6),dict(empirical_efficiency=1.1)])
def test_non_directed_flow_and_invalid_efficiency_are_rejected(updates):
    with pytest.raises(ValueError):flow(BASE|updates)
