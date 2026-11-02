"""Analytical pressure fields, dissipation and static-bound observations."""
import math
import pytest
from pipeline_integrity.axial_contact_friction import AxialContactFrictionRequest,integrate_axial_contact_friction


def request(**changes):
    d=dict(asset_id='pipe',clock_id='snapshot',contact_pressure_basis_id='prescribed-contact',
        outside_diameter_m=.5,contact_length_m=12.,supplied_friction_coefficient=.3,
        coefficient_basis='kinetic',mode='sliding',signed_axial_sliding_direction=1,
        known_normal_pressure_full_cylindrical_surface_coulomb_and_no_adhesion_reviewed=True)
    d.update(changes);return AxialContactFrictionRequest(**d)


class Pressure:
    def __init__(self,fn):self.fn=fn
    def normal_pressure_Pa(self,x,theta):return self.fn(x,theta)


@pytest.mark.parametrize('p,length,diameter',[(10000.,12.,.5),(0.,1.,.02),(210.,.1,3.)])
def test_uniform_pressure_scalar_load_does_not_cancel_with_radial_normal(p,length,diameter):
    q=request(contact_length_m=length,outside_diameter_m=diameter)
    r=integrate_axial_contact_friction(q,contact_pressure_provider=Pressure(lambda x,t:p))
    normal=p*math.pi*diameter*length
    assert r.status=='computed'
    assert r.integrated_normal_pressure_load_N==pytest.approx(normal,rel=2e-13)
    assert r.signed_axial_drag_force_N==pytest.approx(-.3*normal)
    assert r.mean_axial_resistance_N_m==pytest.approx(.3*p*math.pi*diameter)


@pytest.mark.parametrize('a,b',[(.6,.7),(-.7,-.2)])
def test_independent_separable_axial_angular_pressure_integral(a,b):
    q=request();p0=1700.
    r=integrate_axial_contact_friction(q,contact_pressure_provider=Pressure(lambda x,t:p0*(1+a*x/12.)*(1+b*math.cos(t))))
    expected=p0*(12.+a*12./2.)*(2*math.pi)*.25
    assert r.status=='computed' and r.integrated_normal_pressure_load_N==pytest.approx(expected,rel=3e-13)


def test_partial_open_contact_has_no_invented_tensile_pressure():
    q=request(angle_breakpoints_rad=(math.pi/2,3*math.pi/2))
    r=integrate_axial_contact_friction(q,contact_pressure_provider=Pressure(lambda x,t:2000.*max(0.,math.cos(t))))
    assert r.status=='computed'
    assert r.integrated_normal_pressure_load_N==pytest.approx(2000.*2*.25*12.,rel=3e-13)


@pytest.mark.parametrize('direction',[-1,1])
def test_sliding_drag_opposes_motion_and_work_dissipates(direction):
    q=request(signed_axial_sliding_direction=direction,uniform_axial_slip_speed_m_s=.04)
    r=integrate_axial_contact_friction(q,contact_pressure_provider=Pressure(lambda x,t:3000.))
    duration=19.;displacement=direction*.04*duration
    work=r.signed_axial_drag_force_N*displacement
    assert work<=0 and r.friction_dissipation_W==pytest.approx(-work/duration)
    assert r.static_axial_force_interval_N is None


def test_static_capacity_is_interval_not_actual_signed_force():
    q=request(mode='static_bound',coefficient_basis='static',signed_axial_sliding_direction=None)
    r=integrate_axial_contact_friction(q,contact_pressure_provider=Pressure(lambda x,t:2000.))
    assert r.status=='computed' and r.signed_axial_drag_force_N is None and r.friction_dissipation_W is None
    assert r.static_axial_force_interval_N==(-r.axial_friction_capacity_N,r.axial_friction_capacity_N)


@pytest.mark.parametrize('scale',[.03,7.])
def test_section_contact_area_similarity(scale):
    a=integrate_axial_contact_friction(request(),contact_pressure_provider=Pressure(lambda x,t:1000.))
    b=integrate_axial_contact_friction(request(outside_diameter_m=.5*scale,contact_length_m=12*scale),contact_pressure_provider=Pressure(lambda x,t:1000.))
    assert b.axial_friction_capacity_N==pytest.approx(a.axial_friction_capacity_N*scale**2)


def test_zero_coefficient_is_known_zero_not_missing():
    r=integrate_axial_contact_friction(request(supplied_friction_coefficient=0),contact_pressure_provider=Pressure(lambda x,t:2000.))
    assert r.status=='computed' and r.axial_friction_capacity_N==0


@pytest.mark.parametrize('change',[dict(supplied_friction_coefficient=None),dict(signed_axial_sliding_direction=None),
    dict(known_normal_pressure_full_cylindrical_surface_coulomb_and_no_adhesion_reviewed=False)])
def test_unknown_law_or_direction_does_not_query_field(change):
    class Never:
        def normal_pressure_Pa(self,x,t):raise AssertionError('unknown boundary must not be evaluated')
    r=integrate_axial_contact_friction(request(**change),contact_pressure_provider=Never())
    assert r.status=='needs_data' and r.axial_friction_capacity_N is None


def test_missing_contact_provider_not_an_overburden_guess():
    r=integrate_axial_contact_friction(request(),contact_pressure_provider=None)
    assert r.status=='needs_data'


@pytest.mark.parametrize('pressure',[-1.,float('nan')])
def test_noncompressive_or_unknown_contact_pressure_rejected(pressure):
    with pytest.raises(ValueError):integrate_axial_contact_friction(request(),contact_pressure_provider=Pressure(lambda x,t:pressure))


@pytest.mark.parametrize('change',[dict(coefficient_basis='static'),dict(angle_breakpoints_rad=(1.,1.)),
    dict(axial_breakpoints_m=(12.,)),dict(mode='static_bound',coefficient_basis='static')])
def test_mode_basis_or_support_mismatch_rejected(change):
    with pytest.raises(ValueError):request(**change)


def test_underresolved_pressure_field_is_not_qualified_result():
    r=integrate_axial_contact_friction(request(maximum_subdivisions=1),
        contact_pressure_provider=Pressure(lambda x,t:1000.*(1+.2*math.cos(63*t))))
    assert r.status=='not_converged' and r.axial_friction_capacity_N is None
