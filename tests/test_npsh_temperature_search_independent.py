"""Manufactured positive-liquid properties with competing T effects."""
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import quad
from scipy.optimize import brentq

from pipeline_integrity.npsh_temperature_search import SuctionTemperatureProperties as Properties, NPSHTemperatureSearchRequest as Request, search_minimum_available_npsh as search


def request(**changes):
    values=dict(temperature_partitions_K=(300.,340.),objective_partition_shapes=('unimodal_minimum',),
        objective_shape_reviewed_on_all_partitions=True,property_provider_basis_id='authored-liquid-property-curves',
        state_range_and_constant_density_per_temperature_reviewed=True,reservoir_suction_geometry_and_actual_flow_basis_reviewed=True,
        fixed_actual_volume_flow_m3_s=.0001,suction_diameter_m=.04,straight_suction_length_m=71.,absolute_roughness_m=0.,
        fixed_minor_loss_coefficient=.7,reservoir_surface_pressure_pa_abs=300000.,reservoir_surface_above_pump_m=1.2,
        include_evaluation_trace=True)
    values.update(changes);return Request(**values)


def manufactured(q,offset=3.7,density_slope=0.,kind='quadratic'):
    radius=q.suction_diameter_m/2
    # From pressure-force shear tau=G*r/2 and Newtonian no-slip velocity,
    # integrate the radial volume flow for unit gradient and unit viscosity.
    unit_flow=quad(lambda r:2*math.pi*r*quad(lambda s:s/2,r,radius,epsabs=1e-16)[0],0,radius,epsabs=1e-16)[0]
    B=q.straight_suction_length_m*q.fixed_actual_volume_flow_m3_s/unit_flow
    a=B*.00002-1.
    def raw(T):
        x=T-320.
        if kind=='double':
            mu=.5-.004*x-1e-7*(x*x-64.)**2
            pv=40000.+B*.004*x
        elif kind=='concave':
            mu=.3-.002*x
            pv=40000.+(B*.002+.8)*x+.4*x*x
        else:
            mu=.3-.002*x-.00002*x*x
            pv=40000.+(B*.002+2*a*offset)*x+x*x
        return 900.-density_slope*x,mu,pv
    def provider(T):
        rho,mu,pv=raw(T)
        return Properties(temperature_K=T,property_reference_pressure_pa_abs=q.reservoir_surface_pressure_pa_abs,
            density_kg_m3=rho,dynamic_viscosity_pa_s=mu,vapor_pressure_pa_abs=pv,phase='liquid',provider_basis_id=q.property_provider_basis_id)
    velocity=q.fixed_actual_volume_flow_m3_s/(math.pi*radius**2)
    def reference(T):
        rho,mu,pv=raw(T)
        return (q.reservoir_surface_pressure_pa_abs-pv-B*mu)/(rho*q.gravity_m_s2)+q.reservoir_surface_above_pump_m-q.fixed_minor_loss_coefficient*velocity**2/(2*q.gravity_m_s2)
    return provider,reference,B


@pytest.mark.parametrize('offset,expected',[(3.7,323.7),(-30.,300.),(30.,340.)])
def test_interior_and_both_endpoint_minima_from_independent_radial_pressure_balance(offset,expected):
    shape='unimodal_minimum' if 300<expected<340 else 'monotone'
    q=request(objective_partition_shapes=(shape,));provider,reference,_=manufactured(q,offset=offset)
    r=search(q,provider)
    assert r.status=='computed'
    assert r.minimum_temperature_K==pytest.approx(expected,abs=2e-3)
    assert r.minimum_available_npsh_m==pytest.approx(reference(expected),abs=2e-10)
    assert r.minimum_state.properties.temperature_K==r.minimum_temperature_K
    assert all(e.properties.density_kg_m3>0 and e.properties.dynamic_viscosity_pa_s>0 for e in r.evaluations)
    # Both source trends are preserved, including the interior manufactured case.
    assert provider(340.).vapor_pressure_pa_abs>provider(300.).vapor_pressure_pa_abs
    assert provider(340.).dynamic_viscosity_pa_s<provider(300.).dynamic_viscosity_pa_s
    assert not r.global_certificate_for_arbitrary_property_functions


def test_temperature_density_viscosity_and_vapor_pressure_all_reenter_the_budget():
    q=request();slope=.13;provider,reference,B=manufactured(q,density_slope=slope)
    a=B*.00002-1.;n1=-2*a*3.7;n0=q.reservoir_surface_pressure_pa_abs-40000.-B*.3
    derivative_numerator=lambda x:(n1+2*a*x)*(900.-slope*x)+slope*(n0+n1*x+a*x*x)
    expected=320.+brentq(derivative_numerator,-20.,20.,xtol=1e-12)
    r=search(q,provider);assert r.status=='computed'
    assert r.minimum_temperature_K==pytest.approx(expected,abs=2e-3)
    assert r.minimum_available_npsh_m==pytest.approx(reference(expected),abs=2e-10)
    assert r.minimum_state.properties.density_kg_m3!=900.


def test_partitioned_multiple_minima_and_concave_endpoint():
    q=request(temperature_partitions_K=(300.,320.,340.),objective_partition_shapes=('unimodal_minimum','unimodal_minimum'))
    provider,reference,_=manufactured(q,kind='double');r=search(q,provider)
    assert r.status=='computed'
    assert min(abs(r.minimum_temperature_K-t) for t in (312.,328.))<2e-3
    assert r.minimum_available_npsh_m==pytest.approx(reference(312.),abs=2e-10)
    assert any(abs(e.properties.temperature_K-312.)<2e-3 for e in r.evaluations)
    assert any(abs(e.properties.temperature_K-328.)<2e-3 for e in r.evaluations)
    q=request(objective_partition_shapes=('concave',));provider,reference,_=manufactured(q,kind='concave')
    r=search(q,provider);assert r.status=='computed' and r.minimum_temperature_K==340.
    assert r.minimum_available_npsh_m==pytest.approx(reference(340.),abs=2e-11)


def test_refinement_preserves_coarse_partition_and_independent_minimum():
    errors=[]
    for tolerance in (1.,.05,1e-5):
        q=request(temperature_partitions_K=(300.,320.),temperature_absolute_tolerance_K=tolerance)
        provider,reference,_=manufactured(q,kind='double');r=search(q,provider)
        assert r.status=='computed';errors.append(abs(r.minimum_temperature_K-312.))
        assert r.minimum_available_npsh_m>=reference(312.)-2e-11
    assert errors[-1]<2e-3 and errors[-1]<=errors[0]+1e-5


def test_provider_is_actually_called_at_trial_temperatures_and_not_replaced_by_worst_state():
    q=request(include_evaluation_trace=False);base,_,_=manufactured(q);calls=[]
    def provider(T):calls.append(T);return base(T)
    r=search(q,provider)
    assert r.status=='computed' and len(calls)==r.property_evaluation_count and len(calls)>3
    assert any(300<t<340 for t in calls) and calls[:2]==[300.,340.]
    assert r.evaluations==()


def test_vendor_comparison_does_not_modify_available_head_or_infer_acceptance():
    q=request();provider,_,_=manufactured(q);base=search(q,provider)
    unresolved=search(q.model_copy(update={'vendor_required_npsh_m':40.}),provider)
    assert unresolved.status=='computed' and unresolved.vendor_comparison_status=='needs_data'
    assert unresolved.minimum_available_npsh_m==base.minimum_available_npsh_m
    reviewed=search(q.model_copy(update=dict(vendor_required_npsh_m=40.,additional_required_margin_m=1.,
        vendor_requirement_and_margin_basis_id='constant-reviewed-vendor-requirement',vendor_constant_requirement_over_temperature_reviewed=True)),provider)
    assert reviewed.vendor_comparison_status=='computed'
    assert reviewed.residual_against_supplied_additional_margin_m==pytest.approx(base.minimum_available_npsh_m-41.)
    assert not reviewed.cavitation_phase_change_or_vendor_acceptance_qualified


@pytest.mark.parametrize('field',['objective_shape_reviewed_on_all_partitions','state_range_and_constant_density_per_temperature_reviewed',
    'reservoir_suction_geometry_and_actual_flow_basis_reviewed'])
def test_unreviewed_domain_is_not_searched(field):
    def unavailable(T):raise AssertionError('not called')
    assert search(request(**{field:False}),unavailable).status=='needs_data'


@pytest.mark.parametrize('kind',['unknown','exception','bad_temperature','bad_pressure','bad_basis','gas','saturated'])
def test_unknown_or_invalid_trial_is_not_skipped(kind):
    q=request();base,_,_=manufactured(q)
    def provider(T):
        if T==300.:
            if kind=='unknown':return None
            if kind=='exception':raise RuntimeError('unavailable property data')
            changes={'bad_temperature':dict(temperature_K=T+1),'bad_pressure':dict(property_reference_pressure_pa_abs=1e5),
                'bad_basis':dict(provider_basis_id='different-fluid'),'gas':dict(phase='gas'),
                'saturated':dict(vapor_pressure_pa_abs=q.reservoir_surface_pressure_pa_abs)}[kind]
            return base(T).model_copy(update=changes)
        return base(T)
    r=search(q,provider)
    assert r.status==('needs_data' if kind in ('unknown','exception') else 'out_of_scope')
    assert r.minimum_temperature_K is None and r.minimum_available_npsh_m is None


def test_transition_nonconvergence_and_invalid_search_inputs():
    q=request(maximum_iterations_per_partition=1);provider,_,_=manufactured(q)
    assert search(q,provider).status=='out_of_scope'
    q=request();base,_,_=manufactured(q)
    def transitional(T):return base(T).model_copy(update={'dynamic_viscosity_pa_s':4*q.fixed_actual_volume_flow_m3_s*900/(math.pi*q.suction_diameter_m*3000)})
    assert search(q,transitional).status=='out_of_scope'
    with pytest.raises(ValidationError):request(temperature_partitions_K=(340.,300.))
    with pytest.raises(ValidationError):request(objective_partition_shapes=('monotone','monotone'))
    with pytest.raises(ValidationError):request(temperature_absolute_tolerance_K=40.)


def test_request_replay_and_unknown_shape():
    q=request();provider,_,_=manufactured(q);saved=q.model_dump(mode='python')
    assert search(Request.model_validate_json(q.model_dump_json()),provider)==search(q,provider)
    assert q.model_dump(mode='python')==saved
    assert search(q.model_copy(update={'objective_partition_shapes':None}),provider).status=='needs_data'
