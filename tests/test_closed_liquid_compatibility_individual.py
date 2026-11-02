"""Independent constitutive mass-balance roots and reviewed thermoelastic reuse."""
from decimal import Decimal,localcontext
import math
import pytest
from pipeline_integrity.closed_liquid_compatibility import (
    TestPressureCoordinate as Pressure, TestLiquidState as LiquidState, TestPipeVolumeState as VolumeState,
    ClosedLiquidCompatibilityRequest as Request, CoolPropTestWaterProvider,
    ThermoelasticTestPipeVolume, evaluate_closed_liquid_compatibility as solve)
from pipeline_integrity.pipe_pressure_compliance import PressureDiameterRequest
from pipeline_integrity.pipe_thermoelastic_area import ThermoelasticAreaRequest,ClosedEndThermoelasticRequest
from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot

P0=2e6;T0=300.;RHO=1000.;KAPPA=5e-10;BETA=2e-4;CP=1e-10;BPIPE=3e-5

def pressure(p):return Pressure(value_Pa=p,reference='absolute')

def request(**updates):
    q=Request(asset_id='testpipe',clock_id='clockA',reference_clock_id='clockA',reference_time_s=10.,target_time_s=20.,
        reference_pressure=pressure(P0),pressure_bracket=(pressure(1e5),pressure(1e7)),
        reference_external_pressure_Pa_abs=1e5,target_external_pressure_Pa_abs=1e5,
        reference_water_temperature_K=T0,target_water_temperature_K=T0+2,
        reference_wall_temperature_K=T0,target_wall_temperature_K=T0+2,
        fluid_id='testliquid',property_basis_id='specific-volume-A',geometry_basis_id='affine-pipe-A',
        uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed=True,
        continuous_strictly_increasing_mass_pressure_on_bracket_reviewed=True)
    return q.model_copy(update=updates)


class Density:
    def __init__(self,linear_density=False):self.linear_density=linear_density
    def resolve_density(self,*,pressure_Pa_abs,water_temperature_K):
        dp=pressure_Pa_abs-P0;dt=water_temperature_K-T0
        factor=1+KAPPA*dp-BETA*dt if self.linear_density else 1-KAPPA*dp+BETA*dt
        rho=RHO*factor if self.linear_density else RHO/factor
        return LiquidState(status='computed',pressure_Pa_abs=pressure_Pa_abs,water_temperature_K=water_temperature_K,
            fluid_id='testliquid',property_basis_id='specific-volume-A',density_kg_m3=rho,phase='liquid',
            isothermal_compressibility_Pa_inv=KAPPA/factor)


class Volume:
    def __init__(self,scale=1.,cp=CP,beta=BPIPE):self.scale=scale;self.cp=cp;self.beta=beta
    def resolve_volume(self,*,pressure_Pa_abs,external_pressure_Pa_abs,wall_temperature_K):
        v=10*self.scale*(1+self.cp*((pressure_Pa_abs-P0)-(external_pressure_Pa_abs-1e5))+self.beta*(wall_temperature_K-T0))
        return VolumeState(status='computed',pressure_Pa_abs=pressure_Pa_abs,external_pressure_Pa_abs=external_pressure_Pa_abs,
            wall_temperature_K=wall_temperature_K,asset_id='testpipe',geometry_basis_id='affine-pipe-A',volume_m3=v)


def run(q,*,density=None,volume=None):return solve(q,density_provider=density or Density(),volume_provider=volume or Volume())


@pytest.mark.parametrize('dt',[-3.,0.,.1,2.,5.])
def test_affine_specific_volume_exact_thermal_pressure_reference(dt):
    r=run(request(target_water_temperature_K=T0+dt,target_wall_temperature_K=T0+dt))
    expected=(BETA-BPIPE)*dt/(KAPPA+CP)
    assert r.status=='computed'
    assert r.pressure_increment_Pa==pytest.approx(expected,abs=2e-4)
    assert r.mass_budget_residual_kg==pytest.approx(0.,abs=1e-7)
    assert not r.leak_or_test_acceptance_inferred


@pytest.mark.parametrize('added',[-10.,0.,10.])
def test_signed_mass_change_and_distinct_wall_water_temperatures(added):
    q=request(net_added_liquid_mass_kg=added,target_water_temperature_K=301.,target_wall_temperature_K=299.)
    r=run(q);fraction=added/(RHO*10)
    expected=((1+fraction)*(1+BETA)-1+BPIPE)/(CP+(1+fraction)*KAPPA)
    assert r.status=='computed'
    assert r.pressure_Pa_abs==pytest.approx(P0+expected,abs=3e-4)
    assert r.target_mass_kg==r.reference_mass_kg+added


def test_external_pressure_increment_is_not_an_internal_absolute_pressure_increment():
    q=request(target_external_pressure_Pa_abs=1.1e6,target_water_temperature_K=T0,target_wall_temperature_K=T0)
    r=run(q);expected=CP*1e6/(CP+KAPPA)
    assert r.pressure_increment_Pa==pytest.approx(expected,abs=1e-4)
    assert r.differential_pressure_increment_Pa==pytest.approx(expected-1e6)


@pytest.mark.parametrize('dt,added',[(-2.,0.),(0.,10.),(3.,-5.)])
def test_affine_density_volume_product_has_independent_quadratic_root(dt,added):
    q=request(target_water_temperature_K=T0+dt,target_wall_temperature_K=T0+dt,net_added_liquid_mass_kg=added)
    r=run(q,density=Density(linear_density=True))
    with localcontext() as ctx:
        ctx.prec=60;k=Decimal(str(KAPPA));c=Decimal(str(CP));bf=Decimal(str(BETA));bp=Decimal(str(BPIPE));t=Decimal(str(dt));delta=Decimal(str(added))/Decimal('10000')
        a=k*c;b=k*(1+bp*t)+c*(1-bf*t);constant=(1-bf*t)*(1+bp*t)-(1+delta)
        expected=float(-2*constant/(b+(b*b-4*a*constant).sqrt()))
    assert r.pressure_increment_Pa==pytest.approx(expected,abs=3e-4)


@pytest.mark.parametrize('scale',[.001,2.,1000.])
def test_volume_and_added_mass_extensive_scaling(scale):
    base=run(request(net_added_liquid_mass_kg=2.))
    r=run(request(net_added_liquid_mass_kg=2*scale),volume=Volume(scale))
    assert r.pressure_Pa_abs==pytest.approx(base.pressure_Pa_abs,abs=2e-4)
    assert r.reference_mass_kg==pytest.approx(base.reference_mass_kg*scale)


def test_explicit_gauge_conversion_matches_absolute_coordinates():
    def gauge(p):return Pressure(value_Pa=p-101325,reference='gauge',ambient_pressure_Pa_abs=101325)
    a=run(request());b=run(request(reference_pressure=gauge(P0),pressure_bracket=(gauge(1e5),gauge(1e7))))
    assert b.pressure_Pa_abs==a.pressure_Pa_abs


def test_clock_coordinates_are_preserved_without_affecting_quasistatic_state():
    a=run(request());b=run(request(reference_time_s=100.,target_time_s=200.))
    assert a.pressure_Pa_abs==b.pressure_Pa_abs
    assert b.clock_id=='clockA' and (b.reference_time_s,b.target_time_s)==(100.,200.)


@pytest.mark.parametrize('field,value',[('reference_clock_id','other'),('net_added_liquid_mass_kg',-10000.),
    ('absolute_pressure_tolerance_Pa',0.),('relative_pressure_tolerance',1e-20)])
def test_invalid_request_revalidated(field,value):
    with pytest.raises(ValueError):run(request(**{field:value}))


@pytest.mark.parametrize('spec',[dict(value_Pa=0.,reference='gauge'),dict(value_Pa=100.,reference='absolute',ambient_pressure_Pa_abs=1e5),dict(value_Pa=-2e5,reference='gauge',ambient_pressure_Pa_abs=1e5)])
def test_gauge_reference_failures(spec):
    with pytest.raises(ValueError):Pressure(**spec)


def test_no_bracket_does_not_expand_bounds():
    r=run(request(pressure_bracket=(pressure(1e5),pressure(2e5))))
    assert r.status=='no_bracket' and r.pressure_Pa_abs is None


def test_reversed_bracket_rejected():
    with pytest.raises(ValueError):run(request(pressure_bracket=(pressure(1e7),pressure(1e5))))


def test_solver_nonconvergence_not_reported_as_computed():
    r=run(request(maximum_iterations=1),density=Density(linear_density=True))
    assert r.status=='not_converged' and r.pressure_Pa_abs is None


@pytest.mark.parametrize('flag',['uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed','continuous_strictly_increasing_mass_pressure_on_bracket_reviewed'])
def test_unknown_review_withheld(flag):
    r=run(request(**{flag:False}))
    assert r.status=='needs_data' and r.reference_mass_kg is None


def test_missing_provider_withheld():
    assert solve(request()).status=='needs_data'


@pytest.mark.parametrize('phase',['gas','twophase','unknown'])
def test_gas_or_unresolved_phase_withheld(phase):
    class Bad(Density):
        def resolve_density(self,**kw):return super().resolve_density(**kw).model_copy(update={'phase':phase})
    r=run(request(),density=Bad());assert r.status=='out_of_scope' and r.pressure_Pa_abs is None


def test_unknown_density_preserved():
    class Unknown(Density):
        def resolve_density(self,**kw):return super().resolve_density(**kw).model_copy(update={'density_kg_m3':None})
    assert run(request(),density=Unknown()).status=='needs_data'


@pytest.mark.parametrize('field,value',[('property_basis_id','wrong'),('water_temperature_K',999.)])
def test_density_context_mismatch_rejected(field,value):
    class Bad(Density):
        def resolve_density(self,**kw):return super().resolve_density(**kw).model_copy(update={field:value})
    with pytest.raises(ValueError):run(request(),density=Bad())


def test_pipe_asset_context_mismatch_rejected():
    class Bad(Volume):
        def resolve_volume(self,**kw):return super().resolve_volume(**kw).model_copy(update={'asset_id':'other'})
    with pytest.raises(ValueError):run(request(),volume=Bad())


def make_thermoelastic_volume(condition='closed_end',limit=.01):
    curve=ExpansionCurve(id='alpha',material_id='steel',evidence_id='caller curve',
        knots=(ExpansionKnot(temperature_K=270.,tangent_expansion_per_K=12e-6),ExpansionKnot(temperature_K=380.,tangent_expansion_per_K=12e-6)),
        tangent_small_engineering_strain_basis_reviewed=True)
    mech=PressureDiameterRequest(reference_internal_diameter_m=.2,wall_thickness_m=.005,young_modulus_pa=2e11,
        differential_pressure_increment_pa=0.,axial_condition='zero_axial_strain' if condition=='zero_axial_strain' else 'zero_axial_stress',
        poisson_ratio=.3,maximum_absolute_hoop_strain=limit,thin_wall_linear_elastic_and_operating_stability_reviewed=True)
    base=ThermoelasticAreaRequest(pressure=mech,reference_temperature_K=T0,temperature_K=T0,
        thermal_expansion_curve=curve,coefficient_basis='free_linear_engineering',maximum_absolute_total_hoop_strain=limit,
        uniform_temperature_isotropic_linear_thermoelasticity_reviewed=True)
    template=ClosedEndThermoelasticRequest(unconstrained_base=base,
        maximum_absolute_mechanical_axial_strain=limit,maximum_absolute_total_axial_strain=limit,
        closed_end_pressure_force_and_no_external_axial_restraint_reviewed=True) if condition=='closed_end' else base
    return ThermoelasticTestPipeVolume(template=template,geometry_reference_volume_m3=10.,anchor_internal_pressure_Pa_abs=P0,
        anchor_external_pressure_Pa_abs=1e5,asset_id='testpipe',geometry_basis_id='affine-pipe-A',
        maximum_absolute_mechanical_axial_strain=None if condition=='closed_end' else limit,
        maximum_absolute_total_axial_strain=None if condition=='closed_end' else limit)


@pytest.mark.parametrize('condition,compliance,beta',[('closed_end',1.9e-10,36e-6),('zero_axial_stress',1.7e-10,36e-6),('zero_axial_strain',1.82e-10,31.2e-6)])
def test_reused_thermoelastic_volume_matches_independent_constitutive_root(condition,compliance,beta):
    r=run(request(),volume=make_thermoelastic_volume(condition))
    expected=(BETA-beta)*2/(KAPPA+compliance)
    assert r.status=='computed' and r.pressure_increment_Pa==pytest.approx(expected,abs=3e-4)
    assert r.target_pipe_state.geometry_receipt['status']=='computed'


def test_pressure_or_total_strain_support_is_preserved_at_bracket():
    r=run(request(),volume=make_thermoelastic_volume(limit=1e-6))
    assert r.status=='out_of_scope' and r.pressure_Pa_abs is None


def test_missing_axial_support_in_free_stress_volume_is_unknown():
    volume=make_thermoelastic_volume('zero_axial_stress').model_copy(update={'maximum_absolute_total_axial_strain':None})
    assert run(request(),volume=volume).status=='needs_data'


@pytest.mark.parametrize('tw,ts',[(301.,301.),(303.,299.),(299.,300.)])
def test_actual_compressed_water_eos_mass_budget_with_distinct_temperatures(tw,ts):
    from CoolProp.CoolProp import PropsSI
    q=request(fluid_id='Water',property_basis_id='CoolProp::HEOS::Water',target_water_temperature_K=tw,target_wall_temperature_K=ts)
    r=run(q,density=CoolPropTestWaterProvider())
    assert r.status=='computed'
    reference_mass=PropsSI('Dmass','P',P0,'T',T0,'HEOS::Water')*10.
    final_density=PropsSI('Dmass','P',r.pressure_Pa_abs,'T',tw,'HEOS::Water')
    final_volume=10*(1+CP*(r.pressure_Pa_abs-P0)+BPIPE*(ts-T0))
    assert final_density*final_volume==pytest.approx(reference_mass,abs=2e-6)
    assert r.target_liquid_state.phase=='liquid'


@pytest.mark.parametrize('temperature',[300.,320.,350.])
def test_isothermal_density_derivative_not_acoustic_adiabatic_compressibility(temperature):
    from CoolProp.CoolProp import PropsSI
    state=CoolPropTestWaterProvider().resolve_density(pressure_Pa_abs=P0,water_temperature_K=temperature)
    h=10000.;high=PropsSI('Dmass','P',P0+h,'T',temperature,'HEOS::Water');low=PropsSI('Dmass','P',P0-h,'T',temperature,'HEOS::Water')
    fd=(high-low)/(2*h)/state.density_kg_m3
    assert state.isothermal_compressibility_Pa_inv==pytest.approx(fd,rel=2e-5)
    sound=PropsSI('A','P',P0,'T',temperature,'HEOS::Water')
    acoustic=1/(state.density_kg_m3*sound**2)
    assert state.isothermal_compressibility_Pa_inv>acoustic*(1+1e-5)


def test_actual_water_gas_phase_is_not_a_liquid_compliance():
    q=request(fluid_id='Water',property_basis_id='CoolProp::HEOS::Water',reference_water_temperature_K=600.)
    r=run(q,density=CoolPropTestWaterProvider())
    assert r.status=='out_of_scope' and r.pressure_Pa_abs is None


def test_closed_end_axial_supports_cannot_be_silently_overridden_outside_owner():
    volume=make_thermoelastic_volume('closed_end').model_copy(update={'maximum_absolute_total_axial_strain':1e-8})
    with pytest.raises(ValueError):run(request(),volume=volume)
