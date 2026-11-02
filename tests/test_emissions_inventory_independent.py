"""Independent ideal inventory, carrier mole balance and latent-energy limits."""
import math
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.calibrated_emissions import (FillingVaporRequest,evaluate_filling_vapor,TankLossEpisode,TankLossRequest,evaluate_tank_standing_working_loss,CascadeComponent,CascadeSourceRequest,evaluate_cascade_source)

@pytest.mark.parametrize('heat,supply',[(0.,.1),(2e4,.1),(2e5,.1)])
def test_filling_displaced_inventory_and_heat_limited_evaporation_ODE(heat,supply):
    fill=.01;density=2.;latent=2e5;duration=10.
    q=FillingVaporRequest(liquid_fill_rate_m3_s=fill,tank_capacity_m3=100.,displacement_vapor_density_kg_m3=density,displacement_volume_ratio=1.,capacity_generation_coefficient_kg_m3_s=0.,reference_capacity_m3=100.,additional_thermal_power_W=heat,latent_enthalpy_J_kg=latent,maximum_liquid_supply_kg_s=supply,calibration_id='zero-additional-capacity-source',vapor_state_id='prescribed-constant-density',calibrated_fill_and_capacity_domain_reviewed=True)
    r=evaluate_filling_vapor(q)
    # Follow pre-existing headspace mass and an independent heat-limited liquid
    # parcel budget. Displaced vapor does not consume evaporation latent heat.
    evaporation=min(heat/latent,supply)
    ode=solve_ivp(lambda t,y:[-fill*density,evaporation],(0,duration),[200.,0.],rtol=1e-12,atol=1e-12)
    displaced=200-ode.y[0,-1];generated=ode.y[1,-1]
    assert r['displacement_vapor_rate_kg_s']*duration==pytest.approx(displaced,rel=1e-11)
    assert r['generated_vapor_rate_kg_s']*duration==pytest.approx(generated,abs=1e-12)
    assert r['total_vent_vapor_rate_kg_s']*duration==pytest.approx(displaced+generated)
    assert generated*latent<=heat*duration+1e-8


def test_tank_breathing_partition_refines_to_independent_ideal_gas_inventory_loss():
    R=8.31446261815324;p=1e5;volume=30.;molar=.08;fraction=.1;start=290.;stop=330.;horizon=60.
    exact=fraction*molar*p*volume/R*(1/start-1/stop);errors=[]
    for n in (20,40,80):
        episodes=[]
        for i in range(n):
            lo=start+(stop-start)*i/n;hi=start+(stop-start)*(i+1)/n;mid=(lo+hi)/2
            # Fixed-volume, constant-pressure heating vents dVout=V*dT/T.
            # The original gas composition stays constant during well-mixed venting.
            exchange=volume*math.log(hi/lo)
            episodes.append(TankLossEpisode(roof='fixed',duration_s=horizon/n,vapor_concentration_kg_m3=fraction*molar*p/(R*mid),breathing_exchange_m3=exchange,liquid_density_kg_m3=700.,calibrated_geometry_environment_id='ideal-gas-breathing',roof_mechanisms_and_factor_applicability_reviewed=True))
        r=evaluate_tank_standing_working_loss(TankLossRequest(episodes=tuple(episodes),accounting_horizon_s=horizon,calendar_basis_id='explicit-60-second-window'))
        errors.append(abs(r['standing_loss_kg']-exact))
        assert r['working_loss_kg']==0
        assert r['total_loss_kg']==r['standing_loss_kg']
    assert errors[0]/errors[1]>3.9 and errors[1]/errors[2]>3.9
    assert errors[-1]/exact<1e-6

@pytest.mark.parametrize('air,heat,feed',[(.1,1e6,10.),(10.,1e3,10.),(10.,1e6,.001)])
def test_cascade_independent_carrier_moles_heat_and_finite_feed_limits(air,heat,feed):
    pressure=1e5;vapor_pressure=1e4;fuel_molar=.08;air_molar=.029;latent=3e5;duration=7.;splash=.05
    q=CascadeSourceRequest(components=(CascadeComponent(id='specified-pure-fuel',liquid_volume_fraction=1.,liquid_density_kg_m3=700.,molar_mass_kg_mol=fuel_molar,pure_vapor_pressure_Pa=vapor_pressure),),liquid_mass_rate_kg_s=feed,entrained_dry_air_mass_rate_kg_s=air,air_molar_mass_kg_mol=air_molar,total_pressure_Pa_abs=pressure,common_temperature_K=300.,available_net_evaporation_heat_W=heat,effective_latent_enthalpy_J_kg=latent,vapor_transfer_effectiveness=1.,splash_aerosol_mass_fraction=splash,calibration_id='ideal-carrier-capacity-limits',common_state_ideal_equilibrium_and_heat_basis_reviewed=True,prescribed_interface_composition_and_vapor_selectivity_reviewed=True)
    r=evaluate_cascade_source(q)
    dry_moles=air*duration/air_molar
    capacity_moles=dry_moles*vapor_pressure/(pressure-vapor_pressure)
    capacity_mass=capacity_moles*fuel_molar
    heat_mass=heat*duration/latent;available_liquid=feed*duration*(1-splash)
    expected=min(capacity_mass,heat_mass,available_liquid)
    assert r['vapor_mass_rate_kg_s']*duration==pytest.approx(expected,rel=1e-12)
    assert r['latent_heat_used_W']*duration==pytest.approx(expected*latent)
    assert abs(r['mass_balance_residual_kg_s'])<1e-12
    assert r['residual_liquid_mass_rate_kg_s']>=-1e-15
    assert r['available_evaporation_heat_not_used_W']>=-1e-9
