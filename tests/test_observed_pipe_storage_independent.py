"""Analytic rho*A profiles, chain-rule rates and fixed-section balance closure."""
import math
import numpy as np
import pytest
from pipeline_integrity.contracts import RunContext,EvidenceRef
from pipeline_integrity.observed_pipe_storage import (ObservedPipeStation as Station,ObservedPipeProfile as Profile,
    ObservedPipeStorageRequest as Request,ObservedDensityState,ObservedAreaState,LinearizedObservedDensityProvider,
    CoolPropObservedDensityProvider,ThermoelasticObservedAreaProvider,evaluate_observed_pipe_storage,balance_observed_storage_window)
from pipeline_integrity.linearized_liquid_density import LinearizedLiquidDensityLaw
from pipeline_integrity.pipe_pressure_compliance import PressureDiameterRequest
from pipeline_integrity.pipe_thermoelastic_area import ThermoelasticAreaRequest,ClosedEndThermoelasticRequest
from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot

C=RunContext(asset_id='observed-section',scenario_id='authored-profile',clock_id='elapsed',origin='synthetic',evidence=(EvidenceRef(id='authored-profile',citation='Analytic synthetic state histories on a fixed three-metre control section',origin='synthetic'),))


def request(profiles,**kw):
    data=dict(asset_id=C.asset_id,clock_id=C.clock_id,stream_id='observed-inventory',fluid_id='caller-liquid',property_basis_id='authored-density',geometry_basis_id='authored-area',
        evidence_id='authored-profile',origin='synthetic',profiles=tuple(profiles),density_mode='observed',area_mode='observed',fixed_section_and_observed_interpolation_reviewed=True)
    data.update(kw);return Request(**data)


def analytic(time,rho0,area0,a,b,c,d,length=3.):
    return rho0*area0*(length+(a+c)*length**2/2+a*c*length**3/3+time*((b+d)*length+(a*d+b*c)*length**2/2)+b*d*time*time*length)


def analytic_rate(time,rho0,area0,a,b,c,d,length=3.):
    return rho0*area0*((b+d)*length+(a*d+b*c)*length**2/2+2*b*d*time*length)


def direct_profiles(count,rule='gauss2_linear_state'):
    values=[]
    for t in (0.,2.,5.):
        nodes=np.linspace(0,3,count if t!=2 else count+1) # Remeshing preserves fixed endpoints.
        values.append(Profile(time_s=t,stations=tuple(Station(position_m=float(x),density_kg_m3=800.*(1+.01*x+.001*t),area_m2=.02*(1+.02*x+.003*t),density_rate_kg_m3_s=.8,area_rate_m2_s=.00006) for x in nodes)))
    return request(values,spatial_quadrature=rule,rate_mode='supplied_material_rates')


@pytest.mark.parametrize('count',[2,5])
def test_gauss2_observed_linear_state_product_and_exact_time_window_changes(count):
    q=direct_profiles(count);r=evaluate_observed_pipe_storage(q,C)
    assert r.status=='computed' and r.check_integrity() and not r.future_field_predicted
    for frame,rate in zip(r.inventory_frames,r.rate_frames):
        assert frame.values['inventory_mass_kg']==pytest.approx(analytic(frame.time_s,800.,.02,.01,.001,.02,.003),rel=3e-16)
        assert rate.values['storage_rate_kg_s']==pytest.approx(analytic_rate(frame.time_s,800.,.02,.01,.001,.02,.003),rel=4e-16)
        assert frame.units=={'inventory_mass_kg':'kg'} and rate.units=={'storage_rate_kg_s':'kg/s'}
    for span in r.interval_storage_changes:
        delta=analytic(span['finish_s'],800.,.02,.01,.001,.02,.003)-analytic(span['start_s'],800.,.02,.01,.001,.02,.003)
        assert span['inventory_change_kg']==pytest.approx(delta,abs=2e-14)
        assert span['mean_storage_rate_kg_s']==pytest.approx(delta/(span['finish_s']-span['start_s']),abs=1e-14)
        assert span['integrated_sampled_storage_rate_kg']==pytest.approx(delta,abs=2e-14)
        assert abs(span['sampled_rate_vs_inventory_change_kg'])<2e-14


def test_trapezoid_mass_per_length_has_independent_second_order_spatial_convergence():
    errors=[]
    for n in (5,9):
        r=evaluate_observed_pipe_storage(direct_profiles(n,'trapezoid_mass_per_length'),C)
        errors.append(abs(r.inventory_frames[0].values['inventory_mass_kg']-analytic(0.,800.,.02,.01,.001,.02,.003)))
    assert errors[1]/errors[0]==pytest.approx(.25,rel=3e-11)


def material_providers(kind):
    law=LinearizedLiquidDensityLaw(fluid_id='caller-liquid',property_basis_id='authored-density',reference_density_kg_m3=810.,reference_pressure_pa_abs=2e6,reference_temperature_K=300.,
        reference_isothermal_bulk_modulus_pa=1.8e9,reference_volumetric_thermal_expansion_K_inv=.0006,pressure_support_pa_abs=(5e5,5e6),temperature_support_K=(290.,320.),
        maximum_absolute_fractional_density_perturbation=.02,single_liquid_linearized_property_support_reviewed=True)
    p=PressureDiameterRequest(reference_internal_diameter_m=.4,wall_thickness_m=.012,young_modulus_pa=2e11,differential_pressure_increment_pa=0.,
        axial_condition='zero_axial_strain' if kind=='restrained' else 'zero_axial_stress',poisson_ratio=.28,maximum_absolute_hoop_strain=.02,thin_wall_linear_elastic_and_operating_stability_reviewed=True)
    curve=ExpansionCurve(id='alpha',material_id='authored-wall',knots=(ExpansionKnot(temperature_K=290.,tangent_expansion_per_K=1.2e-5),ExpansionKnot(temperature_K=320.,tangent_expansion_per_K=1.2e-5)),evidence_id='authored-profile',tangent_small_engineering_strain_basis_reviewed=True)
    base=ThermoelasticAreaRequest(pressure=p,reference_temperature_K=300.,temperature_K=300.,thermal_expansion_curve=curve,coefficient_basis='free_linear_engineering',maximum_absolute_total_hoop_strain=.02,uniform_temperature_isotropic_linear_thermoelasticity_reviewed=True)
    template=ClosedEndThermoelasticRequest(unconstrained_base=base,maximum_absolute_mechanical_axial_strain=.02,maximum_absolute_total_axial_strain=.02,closed_end_pressure_force_and_no_external_axial_restraint_reviewed=True) if kind=='closed' else base
    return LinearizedObservedDensityProvider(law),ThermoelasticObservedAreaProvider(template,'authored-area',reference_pressure_pa_abs=2e6,reference_external_pressure_pa_abs=1e5)


def state_profiles():
    result=[]
    for t in (0.,2.,5.):
        result.append(Profile(time_s=t,stations=tuple(Station(position_m=float(x),pressure_pa_abs=2e6+5e4*x+2e5*t,fluid_temperature_K=300.+.5*x+.2*t,
            external_pressure_pa_abs=1e5+1e4*t,wall_temperature_K=300.+.7*x+.3*t,pressure_rate_pa_s=2e5,fluid_temperature_rate_K_s=.2,
            external_pressure_rate_pa_s=1e4,wall_temperature_rate_K_s=.3) for x in np.linspace(0,3,5))))
    return result


@pytest.mark.parametrize('kind',['free','restrained','closed'])
def test_existing_density_and_thermoelastic_owners_match_independent_hooke_chain_rule(kind):
    density,area=material_providers(kind);q=request(state_profiles(),density_mode='provider',area_mode='provider',rate_mode='state_chain_rule',retain_state_receipts=True)
    r=evaluate_observed_pipe_storage(q,C,density_provider=density,area_provider=area)
    assert r.status=='computed' and r.check_integrity()
    nu=.28;compliance=.4/(.012*2e11)*(1-nu**2 if kind=='restrained' else 1-nu/2 if kind=='closed' else 1.)
    thermal=2*1.2e-5*(1+nu if kind=='restrained' else 1.)
    a=5e4/1.8e9-.0006*.5;b=2e5/1.8e9-.0006*.2
    c=compliance*5e4+thermal*.7;d=compliance*(2e5-1e4)+thermal*.3
    area0=math.pi*.4**2/4
    for frame,rate in zip(r.inventory_frames,r.rate_frames):
        assert frame.values['inventory_mass_kg']==pytest.approx(analytic(frame.time_s,810.,area0,a,b,c,d),rel=5e-16)
        assert rate.values['storage_rate_kg_s']==pytest.approx(analytic_rate(frame.time_s,810.,area0,a,b,c,d),rel=8e-15,abs=1e-14)
    assert r.profile_diagnostics[0]['retained_states']
    assert r.provider_identities['area']['configuration']['fixed_centerline_length_not_axially_expanded']
    change=r.interval_storage_changes[0]['inventory_change_kg']
    balance=balance_observed_storage_window(r,clock_id='elapsed',window_start_s=0.,window_finish_s=2.,inlet_masses_kg={'inlet':2.},outlet_masses_kg={'outlet':2.-change},common_window_mass_basis_and_boundary_reviewed=True)
    assert balance.status=='computed' and abs(balance.mass_residual_kg)<1e-13


class ConstantArea:
    def resolve_area(self,*,position_m,pressure_pa_abs,external_pressure_pa_abs,wall_temperature_K):
        return ObservedAreaState(status='computed',pressure_pa_abs=pressure_pa_abs,external_pressure_pa_abs=external_pressure_pa_abs,wall_temperature_K=wall_temperature_K,
            geometry_basis_id='authored-area',area_m2=.02,area_pressure_derivative_m2_pa=0.,area_external_pressure_derivative_m2_pa=0.,area_wall_temperature_derivative_m2_K=0.)


def test_coolprop_density_owner_and_optional_derivatives_agree_with_independent_time_difference():
    from CoolProp.CoolProp import PropsSI
    profiles=tuple(Profile(time_s=t,stations=tuple(Station(position_m=x,pressure_pa_abs=1e5+2e5*t,fluid_temperature_K=300.+.3*t,external_pressure_pa_abs=1e5,wall_temperature_K=300.,
        pressure_rate_pa_s=2e5,fluid_temperature_rate_K_s=.3,external_pressure_rate_pa_s=0.,wall_temperature_rate_K_s=0.) for x in (0.,3.))) for t in (0.,1.))
    q=request(profiles,fluid_id='Water',property_basis_id='HEOS-Water',density_mode='provider',area_mode='provider',rate_mode='state_chain_rule')
    r=evaluate_observed_pipe_storage(q,C,density_provider=CoolPropObservedDensityProvider('Water','HEOS-Water'),area_provider=ConstantArea())
    assert r.status=='computed'
    rho=lambda time:PropsSI('Dmass','P',1e5+2e5*time,'T',300.+.3*time,'Water')
    for frame,rate in zip(r.inventory_frames,r.rate_frames):
        t=frame.time_s;h=.01
        assert frame.values['inventory_mass_kg']==pytest.approx(.06*rho(t),rel=2e-15)
        assert rate.values['storage_rate_kg_s']==pytest.approx(.06*(rho(t+h)-rho(t-h))/(2*h),rel=3e-7,abs=1e-10)


def test_free_area_coefficient_basis_preserves_both_inventory_and_chain_rule_tangent():
    density,area=material_providers('free');q=request(state_profiles(),density_mode='provider',area_mode='provider',rate_mode='state_chain_rule')
    ordinary=evaluate_observed_pipe_storage(q,C,density_provider=density,area_provider=area)
    knots=tuple(k.model_copy(update={'tangent_expansion_per_K':2*k.tangent_expansion_per_K}) for k in area.template.thermal_expansion_curve.knots)
    template=area.template.model_copy(update={'coefficient_basis':'free_area_engineering','thermal_expansion_curve':area.template.thermal_expansion_curve.model_copy(update={'knots':knots})})
    alternate=ThermoelasticObservedAreaProvider(template,'authored-area',reference_pressure_pa_abs=2e6,reference_external_pressure_pa_abs=1e5)
    equivalent=evaluate_observed_pipe_storage(q,C,density_provider=density,area_provider=alternate)
    assert equivalent.inventory_frames==ordinary.inventory_frames
    assert equivalent.rate_frames==ordinary.rate_frames


def test_existing_owner_temperature_support_rejection_propagates_without_partial_mass():
    density,area=material_providers('free');data=request(state_profiles(),density_mode='provider',area_mode='provider').model_dump(mode='python')
    for station in data['profiles'][0]['stations']:station['wall_temperature_K']=350.
    r=evaluate_observed_pipe_storage(Request.model_validate(data),C,density_provider=density,area_provider=area)
    assert r.status=='out_of_scope' and r.inventory_frames[0].values['inventory_mass_kg'] is None
    assert r.inventory_frames[1].valid['inventory_mass_kg']


@pytest.mark.parametrize('missing',['density','quality','temperature','wall','area','review'])
def test_missing_observed_or_owner_states_never_become_zero_inventory(missing):
    q=direct_profiles(2);density=area=None
    if missing in ('temperature','wall'):
        density,area=material_providers('free');q=request(state_profiles(),density_mode='provider',area_mode='provider')
    data=q.model_dump(mode='python')
    point=data['profiles'][0]['stations'][0]
    if missing=='density':point['density_kg_m3']=None
    elif missing=='quality':point['valid']=False
    elif missing=='temperature':point['fluid_temperature_K']=None
    elif missing=='wall':point['wall_temperature_K']=None
    elif missing=='area':point['area_m2']=None
    else:data['fixed_section_and_observed_interpolation_reviewed']=False
    r=evaluate_observed_pipe_storage(Request.model_validate(data),C,density_provider=density,area_provider=area)
    assert r.status=='needs_data' and not r.inventory_frames[0].valid['inventory_mass_kg']
    assert r.inventory_frames[0].values['inventory_mass_kg'] is None
    balance=balance_observed_storage_window(r,clock_id='elapsed',window_start_s=0.,window_finish_s=2.,inlet_masses_kg={},outlet_masses_kg={},common_window_mass_basis_and_boundary_reviewed=True)
    assert balance.status=='needs_data'


def test_missing_chain_rule_rates_preserve_usable_inventory_without_inventing_derivative():
    density,area=material_providers('free');data=request(state_profiles(),density_mode='provider',area_mode='provider',rate_mode='state_chain_rule').model_dump(mode='python')
    data['profiles'][0]['stations'][0]['wall_temperature_rate_K_s']=None
    r=evaluate_observed_pipe_storage(Request.model_validate(data),C,density_provider=density,area_provider=area)
    assert r.status=='needs_data' and r.inventory_frames[0].valid['inventory_mass_kg']
    assert not r.rate_frames[0].valid['storage_rate_kg_s']


def test_inconsistent_supplied_rates_remain_visible_without_adjusting_observed_inventory():
    q=direct_profiles(2);data=q.model_dump(mode='python')
    for profile in data['profiles']:
        for station in profile['stations']:station['density_rate_kg_m3_s']+=.1
    ordinary=evaluate_observed_pipe_storage(q,C);changed=evaluate_observed_pipe_storage(Request.model_validate(data),C)
    assert changed.inventory_frames==ordinary.inventory_frames
    assert changed.interval_storage_changes[0]['sampled_rate_vs_inventory_change_kg']<-.01
    assert changed.status=='computed' and changed.check_integrity()


@pytest.mark.parametrize('failure',['moving_bounds','time','space','units','clock','tamper'])
def test_coordinate_units_clock_and_receipt_contracts(failure):
    q=direct_profiles(2);data=q.model_dump(mode='python')
    with pytest.raises(ValueError):
        if failure=='moving_bounds':data['profiles'][1]['stations'][-1]['position_m']=4.;Request.model_validate(data)
        elif failure=='time':data['profiles'][1]['time_s']=0.;Request.model_validate(data)
        elif failure=='space':data['profiles'][0]['stations'][-1]['position_m']=0.;Request.model_validate(data)
        elif failure=='units':data['profiles'][0]['stations'][0]['pressure_kpa']=100.;Request.model_validate(data)
        else:
            r=evaluate_observed_pipe_storage(q,C)
            if failure=='tamper':r.inventory_frames[0].values['inventory_mass_kg']+=1.
            balance_observed_storage_window(r,clock_id='different' if failure=='clock' else 'elapsed',window_start_s=0.,window_finish_s=2.,inlet_masses_kg={},outlet_masses_kg={})


def test_forbidden_phase_and_changed_provider_identity_are_not_silently_accepted():
    class Gas:
        def resolve_density(self,*,position_m,pressure_pa_abs,temperature_K):return ObservedDensityState(status='computed',pressure_pa_abs=pressure_pa_abs,temperature_K=temperature_K,fluid_id='caller-liquid',property_basis_id='authored-density',density_kg_m3=1.,phase='gas')
    profiles=tuple(Profile(time_s=t,stations=(Station(position_m=0.,pressure_pa_abs=1e5,fluid_temperature_K=300.,area_m2=.02),Station(position_m=3.,pressure_pa_abs=1e5,fluid_temperature_K=300.,area_m2=.02))) for t in (0.,1.))
    q=request(profiles,density_mode='provider')
    r=evaluate_observed_pipe_storage(q,C,density_provider=Gas())
    assert r.status=='out_of_scope' and r.inventory_frames[0].values['inventory_mass_kg'] is None
    class Wrong(Gas):
        def resolve_density(self,**kw):return super().resolve_density(**kw).model_copy(update={'fluid_id':'wrong-fluid'})
    with pytest.raises(ValueError):evaluate_observed_pipe_storage(q,C,density_provider=Wrong())
