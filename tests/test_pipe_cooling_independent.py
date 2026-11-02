"""Independent ODE, integrated wall heat and declared area-basis observations."""
import math
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.pipe_cooling_profile import PipeCoolingProfileRequest as Request,evaluate_pipe_cooling_profile as evaluate


def request(**changes):
    args=dict(inlet_temperature_K=350.,ambient_temperature_K=290.,mass_flow_kg_s=2.,specific_heat_J_kg_K=2200.,
        length_m=800.,overall_U_W_m2_K=4.,U_reference_area_diameter_m=.5,
        observation_positions_m=(0.,200.,800.),constant_properties_uniform_ambient_and_U_area_basis_reviewed=True)
    args.update(changes);return Request(**args)


@pytest.mark.parametrize('inlet,ambient',[(350.,290.),(280.,310.),(300.,300.)])
def test_independent_plug_flow_ode_and_wall_heat_quadrature(inlet,ambient):
    q=request(inlet_temperature_K=inlet,ambient_temperature_K=ambient);r=evaluate(q)
    ua=q.overall_U_W_m2_K*math.pi*q.U_reference_area_diameter_m
    capacity=q.mass_flow_kg_s*q.specific_heat_J_kg_K
    ode=solve_ivp(lambda x,T:[-ua*(T[0]-ambient)/capacity],(0,q.length_m),[inlet],dense_output=True,rtol=1e-12,atol=1e-11)
    assert r.observed_temperatures_K==pytest.approx(tuple(ode.sol(x)[0] for x in q.observation_positions_m),abs=2e-10)
    wall_heat=quad(lambda x:ua*(ode.sol(x)[0]-ambient),0,q.length_m,epsabs=1e-5)[0]
    assert r.heat_loss_W==pytest.approx(wall_heat,abs=2e-6)
    assert r.observed_temperatures_K[0]==inlet


def test_series_subsegments_reuse_same_energy_law():
    first=evaluate(request(length_m=300.,observation_positions_m=()))
    second=evaluate(request(length_m=500.,inlet_temperature_K=first.outlet_temperature_K,observation_positions_m=()))
    whole=evaluate(request())
    assert second.outlet_temperature_K==pytest.approx(whole.outlet_temperature_K,abs=6e-14)
    assert first.heat_loss_W+second.heat_loss_W==pytest.approx(whole.heat_loss_W,abs=2e-10)


def test_area_basis_preserving_U_diameter_change():
    a=evaluate(request());b=evaluate(request(overall_U_W_m2_K=2.,U_reference_area_diameter_m=1.))
    assert a.outlet_temperature_K==b.outlet_temperature_K
    assert a.heat_loss_W==b.heat_loss_W


def test_zero_heat_transfer_and_duplicate_unordered_static_observations():
    q=request(overall_U_W_m2_K=0.,observation_positions_m=(800.,0.,800.));r=evaluate(q)
    assert r.observed_temperatures_K==(350.,350.,350.)
    assert r.heat_loss_W==0
    assert not r.joule_thomson_or_pressure_work_included


def test_dimensionless_exposure_conserved_when_mass_flow_and_U_scaled():
    a=evaluate(request());b=evaluate(request(mass_flow_kg_s=20.,overall_U_W_m2_K=40.))
    assert a.outlet_temperature_K==b.outlet_temperature_K
    assert b.heat_loss_W==pytest.approx(10*a.heat_loss_W)


def test_unknown_area_or_constitutive_review_withheld():
    assert evaluate(request(constant_properties_uniform_ambient_and_U_area_basis_reviewed=False)).status=='needs_data'


@pytest.mark.parametrize('key,value',[('mass_flow_kg_s',0.),('specific_heat_J_kg_K',0.),('U_reference_area_diameter_m',0.),('overall_U_W_m2_K',-1.),('observation_positions_m',(801.,))])
def test_invalid_input(key,value):
    with pytest.raises(ValueError):request(**{key:value})
