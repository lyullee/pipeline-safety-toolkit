"""Uniform pressure ODE and separately authored column-weight observations."""
import math
from decimal import Decimal, localcontext

import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.hydrostatic_density_observer import (
    HydrostaticPressurePoint as Point, HydrostaticDensityObservationRequest as Request,
    observe_hydrostatic_density as observe,
)


def point(pressure,height,reference='absolute',**changes):
    return Point(**(dict(pressure_pa=pressure,upward_elevation_m=height,pressure_reference=reference,
        pressure_datum_id='same-pressure-zero',vertical_frame_id='upward-site-frame',state_clock_id='reviewed-static-state')|changes))


def request(first=None,second=None,**changes):
    return Request(**(dict(first=first or point(3e5,0.),second=second or point(3e5-900*9.80665*2,2.),
        uniform_stationary_single_liquid_column_reviewed=True,
        common_pressure_datum_and_vertical_frame_reviewed=True)|changes))


@pytest.mark.parametrize('density,gravity,start,finish',[(998.,9.80665,-4.,7.),(850.,9.81,30.,-20.),
    (1200.,1.62,-7.,12.),(650.,3.71,2.,.1)])
def test_two_observations_from_independent_pressure_ode_recover_density(density,gravity,start,finish):
    solution=solve_ivp(lambda z,p:[-density*gravity],(start,finish),[5e6],method='DOP853',rtol=1e-12,atol=1e-8)
    assert solution.success
    q=request(point(5e6,start),point(float(solution.y[0,-1]),finish),gravity_m_s2=gravity)
    r=observe(q);assert r.status=='computed'
    assert r.density_kg_m3==pytest.approx(density,rel=3e-13)
    assert abs(r.pressure_balance_residual_pa)<2e-9
    assert not r.stratified_profile_resolved and not r.field_or_device_accuracy_qualified
    reversed_result=observe(q.model_copy(update={'first':q.second,'second':q.first}))
    assert reversed_result.density_kg_m3==r.density_kg_m3


@pytest.mark.parametrize('mass,area,height,gravity',[(135.,.07,2.1,9.81),(300.,.2,2.,1.62),(.001,.001,.002,3.71)])
def test_independent_pressure_tractions_balance_column_weight(mass,area,height,gravity):
    top=8e5;bottom=top+mass*gravity/area
    r=observe(request(point(top,height),point(bottom,0.),gravity_m_s2=gravity))
    assert r.status=='computed'
    # The original800kPa/3.71Pa fixture is retained. Its input subtraction
    # floor exceeds the earlier1e-9 kg/m3 oracle budget; an exact Decimal
    # reference of the actual observed floats distinguishes arithmetic error
    # from information already lost when constructing those observations.
    with localcontext() as ctx:
        ctx.prec=70;D=Decimal.from_float
        represented_density=(D(bottom)-D(top))/(D(gravity)*D(height))
    assert r.density_kg_m3==pytest.approx(float(represented_density),rel=2e-14)
    true_density=mass/(area*height)
    original_tight_physical_budget_met=abs(r.density_kg_m3-true_density)<=2e-12*abs(true_density)
    if mass==.001:
        # Keep the original high-base physical claim failed/withheld: only
        # the exact represented-input inverse passes, not the pre-rounded truth.
        assert not original_tight_physical_budget_met
        assert r.pressure_subtraction_condition_number>4e5
    else:assert original_tight_physical_budget_met
    inferred_weight=r.density_kg_m3*(area*height)*gravity
    assert (bottom-top)*area==pytest.approx(inferred_weight,rel=2e-13)
    input_force_roundoff=.5*(math.ulp(bottom)+math.ulp(top))*area
    assert abs(inferred_weight-mass*gravity)<=input_force_roundoff+2e-12*abs(mass*gravity)
    # Same physical mass/area/height, independently represented at a small
    # common gauge zero: retain the original physical accuracy requirement.
    low_top=100.;low_bottom=low_top+mass*gravity/area
    low=observe(request(point(low_top,height,'common_gauge'),point(low_bottom,0.,'common_gauge'),gravity_m_s2=gravity))
    assert low.density_kg_m3==pytest.approx(mass/(area*height),rel=2e-12)
    assert low.pressure_subtraction_condition_number<r.pressure_subtraction_condition_number
    assert r.binary_representation_diagnostic_only and not r.uncertainty_computed
    assert r.pressure_input_half_ulp_sum_pa==.5*(math.ulp(bottom)+math.ulp(top))


@pytest.mark.parametrize('offset',[-1e6,0.,2e6])
def test_common_gauge_offset_preserves_density_without_inventing_absolute_datum(offset):
    q=request();baseline=observe(q)
    first=point(q.first.pressure_pa+offset,q.first.upward_elevation_m,'common_gauge')
    second=point(q.second.pressure_pa+offset,q.second.upward_elevation_m,'common_gauge')
    r=observe(request(first,second))
    assert r.status=='computed' and r.density_kg_m3==pytest.approx(baseline.density_kg_m3,rel=2e-14)
    assert r.absolute_pressure_or_phase_margin_inferred is False
    assert r.forward_owner.startswith('fluids.core.P_from_head')


def test_absolute_forward_owner_receives_the_actual_inferred_density_and_pressure(monkeypatch):
    from pipeline_integrity.models import hydraulics
    actual=hydraulics.hydrostatic;seen=[]
    def capture(q):seen.append(q);return actual(q)
    monkeypatch.setattr(hydraulics,'hydrostatic',capture)
    q=request();r=observe(q)
    assert len(seen)==1
    assert seen[0].density_kg_m3==r.density_kg_m3
    assert seen[0].inlet_pressure_pa_abs==q.first.pressure_pa
    assert seen[0].outlet_minus_inlet_elevation_m==2.
    assert r.pressure_balance_residual_pa==0.


def test_common_gauge_forward_leaf_uses_pressure_difference_and_actual_density(monkeypatch):
    import fluids.core
    actual=fluids.core.P_from_head;seen=[]
    def capture(head,rho,g):seen.append((head,rho,g));return actual(head,rho,g)
    monkeypatch.setattr(fluids.core,'P_from_head',capture)
    r=observe(request(point(-20000.,0.,'common_gauge'),point(-37652.,2.,'common_gauge'),gravity_m_s2=9.80665))
    assert r.status=='computed' and seen==[(2.,r.density_kg_m3,9.80665)]


@pytest.mark.parametrize('field,value',[('pressure_reference','common_gauge'),('pressure_datum_id','other-zero'),
    ('vertical_frame_id','other-height-frame'),('state_clock_id','another-state')])
def test_incompatible_reference_frame_or_state_is_not_reconciled_silently(field,value):
    q=request();q=q.model_copy(update={'second':q.second.model_copy(update={field:value})})
    r=observe(q)
    assert r.status=='needs_data' and r.density_kg_m3 is None


@pytest.mark.parametrize('field',['pressure_pa','upward_elevation_m'])
@pytest.mark.parametrize('side',['first','second'])
def test_unknown_measurement_is_not_a_zero_density(field,side):
    q=request();q=q.model_copy(update={side:getattr(q,side).model_copy(update={field:None})})
    assert observe(q).status=='needs_data' and observe(q).density_kg_m3 is None


def test_unreviewed_stratification_and_datum_are_withheld():
    q=request()
    for change in ({'uniform_stationary_single_liquid_column_reviewed':False},
                   {'common_pressure_datum_and_vertical_frame_reviewed':False}):
        r=observe(q.model_copy(update=change))
        assert r.status=='needs_data' and r.density_kg_m3 is None
    assert observe(q).uniform_density_assumed and not observe(q).stratified_profile_resolved


@pytest.mark.parametrize('p2,status',[(3e5,'unidentifiable'),(2e5,'out_of_scope')])
def test_identical_heights_preserve_nonidentifiability_or_static_contradiction(p2,status):
    r=observe(request(second=point(p2,0.)))
    assert r.status==status and r.density_kg_m3 is None


@pytest.mark.parametrize('pressure,height',[(3e5,2.),(3.2e5,2.),(2.8e5,-2.)])
def test_pressure_height_sign_is_not_repaired_using_absolute_values(pressure,height):
    r=observe(request(second=point(pressure,height)))
    assert r.status=='out_of_scope' and r.density_kg_m3 is None


def test_density_and_separation_support_are_explicit_caller_conditions():
    q=request()
    assert observe(q.model_copy(update={'density_support_kg_m3':(800.,1000.)})).status=='computed'
    assert observe(q.model_copy(update={'density_support_kg_m3':(950.,1050.)})).status=='out_of_scope'
    assert observe(q.model_copy(update={'minimum_reviewed_vertical_separation_m':3.})).status=='out_of_scope'


def test_vertical_datum_translation_and_gravity_pressure_scaling_preserve_density():
    q=request();base=observe(q)
    a=q.first.model_copy(update={'upward_elevation_m':100.})
    b=q.second.model_copy(update={'upward_elevation_m':102.})
    assert observe(q.model_copy(update={'first':a,'second':b})).density_kg_m3==base.density_kg_m3
    scaled=request(point(6e5,0.),point(6e5-2*(3e5-q.second.pressure_pa),2.),gravity_m_s2=2*q.gravity_m_s2)
    assert observe(scaled).density_kg_m3==base.density_kg_m3


@pytest.mark.parametrize('field,value',[('pressure_pa',float('nan')),('upward_elevation_m',math.inf),('pressure_pa',True),('pressure_pa',0.)])
def test_invalid_absolute_observations_are_revalidated(field,value):
    q=request()
    with pytest.raises(ValueError):observe(q.model_copy(update={'first':q.first.model_copy(update={field:value})}))


def test_invalid_support_and_unrepresentable_difference_are_not_finite_results():
    with pytest.raises(ValueError):request(density_support_kg_m3=(1000.,900.))
    with pytest.raises(ValueError):request(gravity_m_s2=0.)
    q=request(point(1e308,0.,'common_gauge'),point(-1e308,1.,'common_gauge'))
    r=observe(q);assert r.status=='out_of_scope' and r.density_kg_m3 is None
