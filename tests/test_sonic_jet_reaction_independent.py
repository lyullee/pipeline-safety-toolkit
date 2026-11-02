"""Maximum mass-flux/parcel-impulse references for ideal sonic jet momentum."""
import math
import pytest
from scipy.optimize import minimize_scalar
from pipeline_integrity.sonic_jet_reaction import SonicJetReactionRequest,observe_sonic_jet_reaction
from pipeline_integrity.ideal_gas_opening_observer import IdealOpeningGas


def request(gamma=1.4,t=360.,**changes):
    gas=IdealOpeningGas(composition_id='declared-dry',molar_mass_kg_mol=.028,
        heat_capacity_ratio=gamma,property_basis_id='SI-ideal',dry_calorically_perfect_gas_and_molar_mass_reviewed=True)
    data=dict(gas=gas,outward_mass_flow_kg_s=2.3,temperature_K=t,temperature_definition='upstream_stagnation',
        outward_unit_direction=(1.,0.,0.),coordinate_frame_id='lab',asset_id='test',clock_id='steady',observation_time_s=0.,
        reviewed_sonic_exit_and_temperature_definition=True,evidence_ids=('perfect-gas-adiabatic-nozzle',))
    data.update(changes);return SonicJetReactionRequest(**data)


@pytest.mark.parametrize('gamma',[1.15,1.4,5/3])
@pytest.mark.parametrize('t0',[290.,650.])
def test_critical_flux_extremum_energy_and_parcel_impulse(gamma,t0):
    q=request(gamma,t0);r=observe_sonic_jet_reaction(q).values
    # Maximize the independently constructed isentropic stream flux. Temperature
    # and speed follow ds=0 and h+u²/2=constant, without a sonic-ratio formula.
    R=8.31446261815324/.028;cp=R*gamma/(gamma-1);p0=650000.
    def state(ratio):
        t=t0*math.exp((gamma-1)/gamma*math.log(ratio))
        speed=math.sqrt(2*cp*(t0-t));rho=p0*ratio/(R*t)
        return t,speed,rho
    fit=minimize_scalar(lambda ratio:-state(ratio)[1]*state(ratio)[2],bounds=(.03,.99),method='bounded',options={'xatol':1e-13})
    tr,u,rho=state(fit.x)
    assert r['sonic_static_temperature_K']==pytest.approx(tr,rel=3e-8)
    assert r['sonic_exit_velocity_m_s'][0]==pytest.approx(u,rel=3e-8)
    # Collected parcels transfer their exiting momentum to a stationary collector.
    duration=.037;parcel_mass=q.outward_mass_flow_kg_s*duration
    assert r['momentum_reaction_contribution_N'][0]*duration==pytest.approx(-parcel_mass*u,rel=3e-8)
    assert cp*t0==pytest.approx(cp*r['sonic_static_temperature_K']+r['sonic_exit_velocity_m_s'][0]**2/2,rel=2e-15)
    assert r['pressure_reaction_contribution_N'] is None and not r['whole_hardware_force_identified']


def test_static_temperature_convention_and_general_frame_rotation():
    q=request();a=observe_sonic_jet_reaction(q).values
    axis=(.6,0.,.8)
    b=observe_sonic_jet_reaction(request(t=a['sonic_static_temperature_K'],temperature_definition='throat_static',outward_unit_direction=axis)).values
    norm=-a['momentum_reaction_contribution_N'][0]
    assert b['momentum_reaction_contribution_N']==pytest.approx(tuple(-norm*x for x in axis))
    assert b['upstream_stagnation_temperature_K']==pytest.approx(q.temperature_K)


def test_pressure_thrust_uses_only_supplied_actual_exit_area_and_gauge_difference():
    q=request(actual_exit_area_m2=.004,exit_pressure_Pa_abs=190000.,ambient_pressure_Pa_abs=100000.)
    r=observe_sonic_jet_reaction(q).values
    assert r['pressure_reaction_contribution_N']==pytest.approx((-360.,0.,0.))
    assert r['combined_open_port_contribution_N'][0]==pytest.approx(r['momentum_reaction_contribution_N'][0]-360.)
    shifted=q.model_copy(update={'exit_pressure_Pa_abs':290000.,'ambient_pressure_Pa_abs':200000.})
    assert observe_sonic_jet_reaction(shifted).values['combined_open_port_contribution_N']==r['combined_open_port_contribution_N']
    assert not r['whole_hardware_force_identified']


def test_zero_mass_has_zero_momentum_but_may_have_supplied_static_pressure_thrust():
    r=observe_sonic_jet_reaction(request(outward_mass_flow_kg_s=0.,actual_exit_area_m2=.004,
        exit_pressure_Pa_abs=190000.,ambient_pressure_Pa_abs=100000.)).values
    assert r['momentum_reaction_contribution_N']==[0.,0.,0.]
    assert r['combined_open_port_contribution_N']==[-360.,0.,0.]
    assert not r['sonic_capacity_or_applicability_predicted']


@pytest.mark.parametrize('changes',[{'outward_mass_flow_kg_s':None},{'temperature_K':None},
    {'reviewed_sonic_exit_and_temperature_definition':False}])
def test_unknown_data_or_sonic_review_not_inferred(changes):
    assert observe_sonic_jet_reaction(request(**changes)).status=='needs_data'


@pytest.mark.parametrize('changes',[{'outward_unit_direction':(2.,0.,0.)},
    {'exit_pressure_Pa_abs':110000.},{'outward_mass_flow_kg_s':-1.}])
def test_inconsistent_direction_partial_pressure_and_inward_mass_rejected(changes):
    with pytest.raises(ValueError):observe_sonic_jet_reaction(request(**changes))


def test_missing_gas_identity_parameters_and_nonfinite_values_are_not_filled():
    q=request();gas=q.gas.model_copy(update={'molar_mass_kg_mol':None})
    assert observe_sonic_jet_reaction(q.model_copy(update={'gas':gas})).status=='needs_data'
    with pytest.raises(ValueError):observe_sonic_jet_reaction(q.model_copy(update={'temperature_K':float('nan')}))
