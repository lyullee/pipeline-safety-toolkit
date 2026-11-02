"""Independent engineering-unit height-corrected relation and driving-head events."""
import pytest
from pipeline_integrity.gas_elevation_correction import ElevationCorrectedPanhandleRequest as Request
from pipeline_integrity.gas_elevation_correction import evaluate_elevation_corrected_panhandle as evaluate
from pipeline_integrity.models.hydraulics import GasCorrelation,gas_correlation

BASE=dict(relative_density_to_air=.65,mean_temperature_k=300.,mean_compressibility=.9,length_m=30000.,
    diameter_m=.35,inlet_pressure_pa_abs=7e6,outlet_pressure_pa_abs=5e6,standard_temperature_k=288.15,
    standard_pressure_pa_abs=101325.,empirical_efficiency=.93)


def calc(method,height,**changes):
    args=dict(flat_correlation=GasCorrelation(method=method,**BASE),outlet_minus_inlet_elevation_m=height,
              mean_pressure_pa_abs=6e6,steady_mean_state_height_approximation_reviewed=True)
    args.update(changes)
    return evaluate(Request(**args))


def uscs_reference(method,height,coefficient):
    p=BASE;psi=.45359237*9.80665/.0254**2
    a,b,c,d=(1.0788,.8539,.5394,2.6182) if method=="Panhandle_A" else (1.02,.961,.51,2.53)
    drop=(p["inlet_pressure_pa_abs"]/psi)**2-(p["outlet_pressure_pa_abs"]/psi)**2
    height_cost=.0375*p["relative_density_to_air"]*(height/.3048)*(6e6/psi)**2/(p["mean_temperature_k"]*1.8*p["mean_compressibility"])
    denom=p["relative_density_to_air"]**b*(p["length_m"]/1609.344)*(p["mean_temperature_k"]*1.8)*p["mean_compressibility"]
    q=coefficient*p["empirical_efficiency"]*((p["standard_temperature_k"]*1.8)/(p["standard_pressure_pa_abs"]/psi))**a
    q*=((drop-height_cost)/denom)**c*(p["diameter_m"]/.0254)**d
    return q*.3048**3/86400


@pytest.mark.parametrize("method,lo,hi",[("Panhandle_A",435.865,435.875),("Panhandle_B",736.5,737.5)])
@pytest.mark.parametrize("height",[-300.,0.,300.])
def test_corrected_flow_matches_independent_uscs_equation_and_coefficient_precision(method,lo,hi,height):
    result=calc(method,height)
    assert result.status=="computed"
    assert uscs_reference(method,height,lo)<=result.standard_volume_flow_m3_s<=uscs_reference(method,height,hi)


def test_zero_elevation_does_not_require_or_infer_a_mean_pressure():
    p=GasCorrelation(method="Panhandle_A",**BASE)
    result=calc("Panhandle_A",0.,mean_pressure_pa_abs=None)
    assert result.standard_volume_flow_m3_s==gas_correlation(p).values["standard_volume_flow_m3_s"]


@pytest.mark.parametrize("height,status",[(900.,"computed"),(1000.,"no_forward_solution")])
def test_balance_and_exhausted_forward_driving_head_are_preserved(height,status):
    p=GasCorrelation(method="Panhandle_B",**(BASE|dict(relative_density_to_air=1.,mean_temperature_k=300.,
        mean_compressibility=1.,inlet_pressure_pa_abs=2e5,outlet_pressure_pa_abs=1e5)))
    result=calc("Panhandle_B",height,flat_correlation=p,mean_pressure_pa_abs=1e5,elevation_coefficient_K_per_m=1.)
    assert result.status==status
    if status=="computed":assert result.standard_volume_flow_m3_s==0
    else:
        assert result.corrected_pressure_squared_drop_Pa2<0
        assert result.standard_volume_flow_m3_s is None


def test_unknown_mean_state_or_unreviewed_approximation_is_withheld():
    assert calc("Panhandle_A",100.,mean_pressure_pa_abs=None).status=="needs_data"
    assert calc("Panhandle_A",100.,steady_mean_state_height_approximation_reviewed=False).status=="needs_data"
