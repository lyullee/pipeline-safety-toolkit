"""Independent rigid-volume balances/ODE events and latent-heat control cases."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.thermo import ThermodynamicState
from pipeline_integrity.vacuum_water_evaporation import VacuumWaterEvaporationRequest as Request,evaluate_vacuum_water_evaporation as evaluate


class ManufacturedCoexistenceProvider:
    def __init__(self,rl=1000.,rv=10.,hl=1e5,hv=2.5e6,p=1000.,bad=None):
        self.rl=rl;self.rv=rv;self.hl=hl;self.hv=hv;self.p=p;self.bad=bad
    def resolve(self,r):
        liquid=r.inputs['Q']==0
        return ThermodynamicState(fluid='Water',backend='HEOS',pressure_pa_abs=self.p,
            temperature_k=r.inputs['T'],density_kg_m3=self.rl if liquid else self.rv,
            enthalpy_j_kg=self.hl if liquid else self.hv,entropy_j_kg_k=0 if liquid else (self.hv-self.hl)/r.inputs['T'],
            phase='liquid' if self.bad=='phase' and not liquid else 'twophase',
            viscosity_pa_s=None,cp_j_kg_k=None,cv_j_kg_k=None,conductivity_w_m_k=None,speed_of_sound_m_s=None,
            provider_version='manufactured-constitutive-state',provider_revision='different' if self.bad=='revision' and not liquid else 'fixed')


def request(**changes):
    d=dict(internal_volume_m3=1.,initial_free_water_mass_kg=20.,plateau_temperature_K=300.,external_pressure_Pa_abs=1e5,
        pump_speed_m3_s=.01,line_conductances_m3_s=(),thermal_boundary='prescribed_isothermal_reservoir',
        pure_water_uniform_saturation_and_constant_coefficients_reviewed=True,prescribed_thermal_boundary_and_pump_control_reviewed=True,
        elapsed_times_s=(0.,10.,100.,1000.),clock_id='local-clock',start_time_s=-20.)
    d.update(changes);return Request(**d)


@pytest.mark.parametrize('rv',[1.,10.,100.])
def test_independent_mass_volume_linear_system_and_ode_depletion_event(rv):
    p=ManufacturedCoexistenceProvider(rv=rv);q=request();r=evaluate(q,property_provider=p)
    # Derive rates from total-mass and rigid-volume equations, not the adapter's
    # explicit phase-fraction correction. The supplied pump extraction is known.
    mdot=p.rv*q.pump_speed_m3_s
    rates=np.linalg.solve(np.array([[1.,1.],[1/p.rl,1/p.rv]]),[-mdot,0.])
    ul=p.hl-p.p/p.rl;uv=p.hv-p.p/p.rv
    power=float(np.dot([ul,uv],rates)+mdot*p.hv)
    assert r.liquid_evaporation_rate_kg_s==pytest.approx(-rates[0],rel=2e-14)
    assert r.required_latent_heat_power_W==pytest.approx(power,rel=2e-14)
    def depleted(t,y):return y[0]
    depleted.terminal=True;depleted.direction=-1
    ode=solve_ivp(lambda t,y:rates,(0,5000),[q.initial_free_water_mass_kg,p.rv*(q.internal_volume_m3-q.initial_free_water_mass_kg/p.rl)],
        events=depleted,rtol=1e-10,atol=1e-11,max_step=3.)
    assert len(ode.t_events[0])==1
    assert r.time_to_free_water_depletion_s==pytest.approx(ode.t_events[0][0],rel=2e-12)
    assert ode.y_events[0][0,1]==pytest.approx(p.rv*q.internal_volume_m3,rel=2e-12)


@pytest.mark.parametrize('heat',[0.,12000.,1200000.])
def test_finite_net_heat_throttles_pump_without_inventing_thermal_transport(heat):
    r=evaluate(request(thermal_boundary='finite_net_heat_with_pump_throttle',available_net_evaporation_heat_W=heat),property_provider=ManufacturedCoexistenceProvider())
    assert r.required_latent_heat_power_W<=heat*(1+1e-14)
    assert r.controlled_effective_speed_m3_s<=r.pump_capacity_effective_speed_m3_s
    if heat==0:assert r.depletion_status=='unreachable' and r.liquid_evaporation_rate_kg_s==0
    else:
        assert r.time_to_free_water_depletion_s*r.required_latent_heat_power_W==pytest.approx(20*2.4e6)
        if heat==12000:assert r.limiting_capacity=='net_heat'


@pytest.mark.parametrize('times',[(0.,1.,150.),(0.,198.,198.000001),(1000.,10.,0.)])
def test_exact_open_mass_and_energy_budgets_with_finite_vapor_storage(times):
    r=evaluate(request(elapsed_times_s=times),property_provider=ManufacturedCoexistenceProvider())
    for p in r.points:
        assert abs(p.mass_budget_residual_kg)<2e-13
        assert abs(p.energy_budget_residual_J)<3e-8
        assert p.plateau_removed_vapor_kg==pytest.approx(.99*p.plateau_evaporated_liquid_kg)
        assert p.vapor_mass_at_plateau_endpoint_kg==pytest.approx(9.8+.01*p.plateau_evaporated_liquid_kg)
        assert p.free_water_remaining_kg>=0
    assert not r.final_drying_or_field_acceptance_predicted


def test_event_stop_does_not_extend_constant_pressure_plateau_after_depletion():
    r=evaluate(request(elapsed_times_s=(197.,198.,199.)),property_provider=ManufacturedCoexistenceProvider())
    assert r.time_to_free_water_depletion_s==pytest.approx(198.)
    assert [p.phase_status for p in r.points]==['liquid_present','at_depletion','post_depletion_not_predicted']
    assert r.points[-1].plateau_elapsed_time_s==pytest.approx(198)
    assert r.points[-1].plateau_removed_vapor_kg==r.points[-2].plateau_removed_vapor_kg


@pytest.mark.parametrize('speed,lines',[(0.,()),(.01,(0.,)),(.01,(.02,.04))])
def test_existing_series_conductance_and_zero_isolation(speed,lines):
    r=evaluate(request(pump_speed_m3_s=speed,line_conductances_m3_s=lines),property_provider=ManufacturedCoexistenceProvider())
    expected=0 if speed==0 or 0 in lines else 1/(1/speed+sum(1/c for c in lines))
    assert r.pump_capacity_effective_speed_m3_s==pytest.approx(expected)
    if expected==0:assert r.depletion_status=='unreachable'


@pytest.mark.parametrize('rv',[.1,.01,.001])
def test_source_removal_only_time_is_recovered_in_dilute_vapor_limit(rv):
    r=evaluate(request(),property_provider=ManufacturedCoexistenceProvider(rv=rv))
    assert r.time_to_free_water_depletion_s/r.low_vapor_density_limit_time_at_pump_capacity_s==pytest.approx(1-rv/1000,rel=2e-14)
    assert abs(1-r.time_to_free_water_depletion_s/r.low_vapor_density_limit_time_at_pump_capacity_s)<=rv/1000+1e-15


def test_geometry_scaling_clock_and_zero_initial_inventory():
    p=ManufacturedCoexistenceProvider();a=evaluate(request(elapsed_times_s=(20.,)),property_provider=p)
    b=evaluate(request(internal_volume_m3=10,initial_free_water_mass_kg=200,pump_speed_m3_s=.1,elapsed_times_s=(20.,),start_time_s=100),property_provider=p)
    assert b.time_to_free_water_depletion_s==pytest.approx(a.time_to_free_water_depletion_s)
    assert b.points[0].plateau_evaporated_liquid_kg==pytest.approx(10*a.points[0].plateau_evaporated_liquid_kg)
    assert b.points[0].time_s-a.points[0].time_s==120
    zero=evaluate(request(initial_free_water_mass_kg=0,elapsed_times_s=(0.,1.)),property_provider=p)
    assert zero.depletion_status=='at_start' and zero.time_to_free_water_depletion_s==0
    assert zero.points[1].phase_status=='post_depletion_not_predicted'


@pytest.mark.parametrize('changes',[{'initial_free_water_mass_kg':None},{'plateau_temperature_K':None},
    {'thermal_boundary':None},{'thermal_boundary':'finite_net_heat_with_pump_throttle'},
    {'pump_speed_m3_s':None},{'line_conductances_m3_s':None},
    {'pure_water_uniform_saturation_and_constant_coefficients_reviewed':False},
    {'prescribed_thermal_boundary_and_pump_control_reviewed':False}])
def test_unknown_or_unreviewed_input_is_not_zero(changes):
    assert evaluate(request(**changes),property_provider=ManufacturedCoexistenceProvider()).status=='needs_data'


@pytest.mark.parametrize('changes',[{'plateau_temperature_K':270.},{'plateau_temperature_K':650.},{'external_pressure_Pa_abs':500.}])
def test_ice_critical_and_nonvacuum_boundaries(changes):
    assert evaluate(request(**changes),property_provider=ManufacturedCoexistenceProvider()).status=='out_of_scope'


def test_liquid_filled_chamber_and_incompatible_thermal_boundary_rejected():
    with pytest.raises(ValueError,match='vapor space'):evaluate(request(initial_free_water_mass_kg=1000),property_provider=ManufacturedCoexistenceProvider())
    with pytest.raises(ValueError,match='finite heat'):evaluate(request(available_net_evaporation_heat_W=10),property_provider=ManufacturedCoexistenceProvider())


@pytest.mark.parametrize('bad',['phase','revision'])
def test_mismatched_saturation_provider_endpoints_rejected(bad):
    with pytest.raises(ValueError):evaluate(request(),property_provider=ManufacturedCoexistenceProvider(bad=bad))


@pytest.mark.parametrize('temperature',[280.,300.,350.])
def test_native_water_plateau_independent_rigid_volume_energy_and_mass(temperature):
    r=evaluate(request(plateau_temperature_K=temperature,elapsed_times_s=(0.,10.,100.)))
    assert r.status=='computed'
    for p in r.points:
        vapor_volume=1-p.free_water_remaining_kg/r.liquid_density_kg_m3
        assert p.vapor_mass_at_plateau_endpoint_kg==pytest.approx(r.vapor_density_kg_m3*vapor_volume,rel=2e-13)
        assert abs(p.mass_budget_residual_kg)<1e-12
        assert abs(p.energy_budget_residual_J)<1e-6
    assert r.total_latent_heat_to_depletion_J==pytest.approx(20*r.latent_enthalpy_J_kg)


def test_common_enthalpy_reference_shift_preserves_time_heat_and_open_energy_balance():
    q=request(elapsed_times_s=(0.,50.,198.));a=evaluate(q,property_provider=ManufacturedCoexistenceProvider())
    shift=-3e6
    b=evaluate(q,property_provider=ManufacturedCoexistenceProvider(hl=1e5+shift,hv=2.5e6+shift))
    assert b.time_to_free_water_depletion_s==a.time_to_free_water_depletion_s
    assert b.required_latent_heat_power_W==a.required_latent_heat_power_W
    for x,y in zip(a.points,b.points):
        assert abs(y.energy_budget_residual_J)<5e-8
        assert y.outflow_enthalpy_J-x.outflow_enthalpy_J==pytest.approx(shift*x.plateau_removed_vapor_kg,abs=1e-7)
        assert y.latent_heat_supplied_J==x.latent_heat_supplied_J
