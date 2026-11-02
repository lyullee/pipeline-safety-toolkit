"""Independent Poiseuille and Darcy/Colebrook energy-to-diameter references."""
import math
import pytest
from scipy.optimize import brentq
from pipeline_integrity.suction_diameter_inverse import SuctionDiameterRequest as Request,evaluate_suction_diameter as evaluate

def request(**kw):
    d=dict(id='authored',volume_flow_m3_s=.001,straight_length_m=50.,absolute_roughness_m=0.,fixed_minor_loss_coefficient=0.,
        density_kg_m3=1000.,dynamic_viscosity_pa_s=1.,vapor_pressure_pa_abs=2000.,reservoir_surface_pressure_pa_abs=200000.,
        reservoir_surface_above_pump_m=-1.,vendor_required_npsh_m=2.,additional_required_margin_m=1.,gravity_m_s2=10.,
        diameter_bracket_m=(.02,.2),fluid_state_basis_id='supplied-authored-state',hydraulic_and_vendor_basis_id='authored-loss-vendor',
        friction_method='Colebrook',prescribed_state_fixed_flow_reservoir_total_npsh_and_geometry_reviewed=True)
    d.update(kw);return Request(**d)

@pytest.mark.parametrize('flow,mu,length',[(.001,1.,50.),(.002,.3,90.),(.0005,3.,20.)])
def test_poiseuille_pressure_work_exact_diameter(flow,mu,length):
    q=request(volume_flow_m3_s=flow,dynamic_viscosity_pa_s=mu,straight_length_m=length)
    required_loss=(q.reservoir_surface_pressure_pa_abs-q.vapor_pressure_pa_abs)/(q.density_kg_m3*q.gravity_m_s2)+q.reservoir_surface_above_pump_m-q.vendor_required_npsh_m-q.additional_required_margin_m
    expected=(128*mu*length*flow/(math.pi*q.density_kg_m3*q.gravity_m_s2*required_loss))**.25
    a=evaluate(q);assert a.status=='computed'
    assert a.values['equality_diameter_m']==pytest.approx(expected,abs=2e-10)
    assert abs(a.values['residual_against_vendor_plus_margin_m'])<2e-6
    assert a.values['suction_loss_receipt']['reynolds']<2040

@pytest.mark.parametrize('diameter,roughness',[(.08,1e-5),(.2,2e-4),(.5,1e-3)])
def test_independent_turbulent_friction_and_control_volume_head(diameter,roughness):
    flow=.05;nu=1e-6;rho=900.;g=9.81;length=80.;minor=2.
    v=4*flow/(math.pi*diameter**2);re=v*diameter/nu
    # Independent inverse-sqrt-factor Colebrook equation, rather than owner API.
    inv=brentq(lambda z:z+2*math.log10(roughness/(3.7*diameter)+2.51*z/re),1.,30.,xtol=1e-13)
    loss=(length/diameter/inv**2+minor)*v*v/(2*g)
    pressure=5000.+rho*g*(3.+1.+2.+loss)
    q=request(volume_flow_m3_s=flow,dynamic_viscosity_pa_s=rho*nu,density_kg_m3=rho,straight_length_m=length,
        absolute_roughness_m=roughness,fixed_minor_loss_coefficient=minor,gravity_m_s2=g,
        vapor_pressure_pa_abs=5000.,reservoir_surface_pressure_pa_abs=pressure,reservoir_surface_above_pump_m=-2.,
        vendor_required_npsh_m=3.,diameter_bracket_m=(.75*diameter,1.25*diameter))
    a=evaluate(q);assert a.status=='computed';x=a.values
    assert x['equality_diameter_m']==pytest.approx(diameter,abs=2e-10)
    assert x['suction_loss_receipt']['darcy_friction_factor']==pytest.approx(1/inv**2,rel=1e-8)
    assert x['available_npsh_m']==pytest.approx(4.,abs=1e-5)

def test_common_absolute_pressure_offset_preserves_diameter():
    q=request();a=evaluate(q).values;b=evaluate(q.model_copy(update=dict(vapor_pressure_pa_abs=52000.,reservoir_surface_pressure_pa_abs=250000.))).values
    assert a['equality_diameter_m']==pytest.approx(b['equality_diameter_m'],abs=1e-14)

def test_total_npsh_and_inlet_static_pressure_have_distinct_velocity_head():
    q=request();v=evaluate(q).values;rho=q.density_kg_m3;g=q.gravity_m_s2
    assert (v['inferred_pump_inlet_static_pressure_pa_abs']-q.vapor_pressure_pa_abs)/(rho*g)+v['suction_velocity_head_m']==pytest.approx(v['available_npsh_m'],abs=1e-12)
    assert v['available_npsh_m']==pytest.approx(q.vendor_required_npsh_m+q.additional_required_margin_m,abs=1e-6)

def test_equality_at_supported_bracket_boundary():
    q=request();target=.06;hf=128*q.dynamic_viscosity_pa_s*q.straight_length_m*q.volume_flow_m3_s/(math.pi*q.density_kg_m3*q.gravity_m_s2*target**4)
    pressure=q.vapor_pressure_pa_abs+q.density_kg_m3*q.gravity_m_s2*(q.vendor_required_npsh_m+q.additional_required_margin_m-q.reservoir_surface_above_pump_m+hf)
    a=evaluate(q.model_copy(update=dict(reservoir_surface_pressure_pa_abs=pressure,diameter_bracket_m=(target,.2))))
    assert a.status=='computed';assert a.values['equality_diameter_m']==pytest.approx(target,abs=2e-10)

def test_outside_bracket_is_not_an_extrapolated_diameter():
    assert evaluate(request(diameter_bracket_m=(.15,.2))).status=='out_of_scope'

def test_transition_spanning_bracket_withheld():
    assert evaluate(request(dynamic_viscosity_pa_s=.01)).status=='out_of_scope'

def test_bad_roughness_or_unreviewed_basis_withheld():
    assert evaluate(request(absolute_roughness_m=.01)).status=='out_of_scope'
    assert evaluate(request(prescribed_state_fixed_flow_reservoir_total_npsh_and_geometry_reviewed=False)).status=='needs_data'

def test_flow_increase_requires_larger_supported_diameter():
    a=evaluate(request()).values['equality_diameter_m'];b=evaluate(request(volume_flow_m3_s=.002)).values['equality_diameter_m']
    assert b/a==pytest.approx(2**.25,rel=1e-8)

def test_unrepresentable_property_ratio_and_unresolved_bracket_rejected():
    with pytest.raises(ValueError,match='viscosity'):
        evaluate(request(density_kg_m3=1e300,dynamic_viscosity_pa_s=1e-300))
    with pytest.raises(ValueError,match='tolerance'):
        request(diameter_absolute_tolerance_m=1.)
