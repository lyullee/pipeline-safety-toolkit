"""Independent US geometry coordinates, reference normalization and pressure support."""
import math
import pytest
from fluids.compressible import Spitzglass_low
from pipeline_integrity.low_pressure_gas import LowPressureSpitzglassRequest as Request
from pipeline_integrity.low_pressure_gas import evaluate_low_pressure_spitzglass as evaluate

BASE=dict(relative_density_to_air=.65,mean_temperature_k=300.,mean_compressibility=1.,length_m=150.,diameter_m=.1,
          inlet_pressure_pa_abs=102325.,outlet_pressure_pa_abs=101325.,standard_temperature_k=288.15,
          standard_pressure_pa_abs=101325.,empirical_efficiency=1.,ambient_pressure_pa_abs=101325.,
          low_pressure_uniform_state_domain_reviewed=True)


def calc(**changes):return evaluate(Request(**(BASE|changes)))


@pytest.mark.parametrize("diam_in,length_ft,head_in_water",[(3.,200.,3.),(5.,500.,5.),(7.,900.,10.)])
def test_reference_corrected_low_formula_matches_separate_us_geometry_coordinates(diam_in,length_ft,head_in_water):
    rho_water,g=1000.,9.80665
    drop=head_in_water*rho_water*g*.0254
    result=calc(diameter_m=diam_in*.0254,length_m=length_ft*.3048,
        inlet_pressure_pa_abs=BASE["ambient_pressure_pa_abs"]+drop)
    ts,ps,t=BASE["standard_temperature_k"],BASE["standard_pressure_pa_abs"],BASE["mean_temperature_k"]
    # Reference-corrected library variant has an explicit, state-dependent US prefactor.
    coefficient=125.106*(ts/ps)*math.sqrt(2*(ps+1210)*rho_water*g*.0254*.0254**5/(.3048*t))*3600/.3048**3
    scfh=coefficient*math.sqrt(head_in_water*diam_in**5/(
        BASE["relative_density_to_air"]*length_ft*(1+3.6/diam_in+.03*diam_in)))
    assert result.standard_volume_flow_m3_s==pytest.approx(scfh*.3048**3/3600,rel=3e-12)


def test_pressure_drop_and_length_similarities_preserve_declared_reference_state():
    a=calc();b=calc(inlet_pressure_pa_abs=105325.)
    assert b.standard_volume_flow_m3_s==pytest.approx(2*a.standard_volume_flow_m3_s,rel=3e-14)
    assert calc(length_m=4*BASE["length_m"]).standard_volume_flow_m3_s==pytest.approx(a.standard_volume_flow_m3_s/2,rel=3e-14)


def test_native_inverse_recovers_original_absolute_inlet_pressure():
    result=calc()
    p=Spitzglass_low(SG=BASE["relative_density_to_air"],Tavg=BASE["mean_temperature_k"],Zavg=1.,
        L=BASE["length_m"],D=BASE["diameter_m"],P1=None,P2=BASE["outlet_pressure_pa_abs"],
        Q=result.standard_volume_flow_m3_s,Ts=BASE["standard_temperature_k"],Ps=BASE["standard_pressure_pa_abs"],E=1.)
    assert p==pytest.approx(BASE["inlet_pressure_pa_abs"],rel=3e-14)


@pytest.mark.parametrize("changes",[dict(inlet_pressure_pa_abs=101325+50*1000*9.80665*.0254),dict(outlet_pressure_pa_abs=101000.)])
def test_source_domain_conflict_or_vacuum_is_not_silently_accepted(changes):
    result=calc(**changes)
    assert result.status=="out_of_scope" and result.standard_volume_flow_m3_s is None


def test_unreviewed_empirical_low_pressure_support_is_withheld():
    assert calc(low_pressure_uniform_state_domain_reviewed=False).status=="needs_data"


def test_non_directed_pressure_input_is_rejected():
    with pytest.raises(ValueError):calc(inlet_pressure_pa_abs=BASE["outlet_pressure_pa_abs"])
