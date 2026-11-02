"""Independent pressure-work budget, source unit precision and efficiency boundaries."""
import pytest
from pipeline_integrity.pump_pressure_duty import PumpPressureDutyRequest as Request
from pipeline_integrity.pump_pressure_duty import evaluate_pump_pressure_duty as evaluate


def calc(**changes):
    p=dict(volume_flow_m3_s=.03,differential_pressure_pa=5e5,pump_efficiency=.8,upstream_drive_efficiency=.97,
        upstream_drive_boundary="mechanical_transmission_input",incompressible_liquid_duty_and_efficiency_basis_reviewed=True)
    p.update(changes)
    return evaluate(Request(**p))


@pytest.mark.parametrize("pressure_psi,rate_bpd",[(150.,1000.),(500.,8000.),(1000.,24000.)])
def test_pressure_volume_power_matches_independent_us_units_and_source_rounded_factor(pressure_psi,rate_bpd):
    psi=.45359237*9.80665/.0254**2;barrel=42*231*.0254**3;hp=550*.3048*.45359237*9.80665
    pressure,flow=pressure_psi*psi,rate_bpd*barrel/86400
    out=calc(volume_flow_m3_s=flow,differential_pressure_pa=pressure)
    assert out.upstream_drive_input_power_w==pytest.approx(pressure*flow/(.8*.97),rel=3e-15)
    factor=(out.upstream_drive_input_power_w/hp)/(pressure_psi*rate_bpd/1000)
    assert .0215<=factor<=.0225


@pytest.mark.parametrize("eta_p,eta_drive",[(.6,.8),(1.,1.)])
def test_energy_losses_close_the_declared_two_efficiency_stage_budget(eta_p,eta_drive):
    out=calc(pump_efficiency=eta_p,upstream_drive_efficiency=eta_drive)
    assert out.upstream_drive_input_power_w>=out.pump_shaft_power_w>=out.hydraulic_power_w
    assert out.hydraulic_power_w+out.pump_loss_w+out.upstream_drive_loss_w==pytest.approx(out.upstream_drive_input_power_w,rel=3e-15)


def test_pressure_and_flow_similarity_does_not_require_an_inferred_fluid_density():
    a,b=calc(),calc(volume_flow_m3_s=.06,differential_pressure_pa=1.5e6)
    assert b.upstream_drive_input_power_w==pytest.approx(6*a.upstream_drive_input_power_w,rel=3e-15)


def test_unreviewed_differential_pressure_or_efficiency_basis_is_withheld():
    assert calc(incompressible_liquid_duty_and_efficiency_basis_reviewed=False).status=="needs_data"


@pytest.mark.parametrize("field",["volume_flow_m3_s","differential_pressure_pa"])
def test_zero_duty_preserves_zero_power(field):
    assert calc(**{field:0.}).upstream_drive_input_power_w==0


@pytest.mark.parametrize("changes",[dict(pump_efficiency=0.),dict(upstream_drive_efficiency=1.1),dict(differential_pressure_pa=-1.)])
def test_invalid_efficiency_or_pressure_is_rejected(changes):
    with pytest.raises(ValueError):calc(**changes)
