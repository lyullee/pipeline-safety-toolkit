"""Independent molecular-speed quadrature, unit conversion and Fick face budgets."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.binary_gas_diffusion import BinaryGasDiffusivityRequest as Request,evaluate_binary_gas_diffusivity as evaluate,BinaryMolarFickRequest,evaluate_binary_molar_fick


def request(**kw):
    d=dict(id='authored',species_1='synthetic-A',species_2='synthetic-B',temperature_k=360.,pressure_pa_abs=160000.,
        species_1_molar_mass_kg_mol=.022,species_2_molar_mass_kg_mol=.041,
        species_1_collision_diameter_m=3.3e-10,species_2_collision_diameter_m=4.2e-10,
        species_1_well_depth_over_boltzmann_k=140.,species_2_well_depth_over_boltzmann_k=220.,
        supplied_diffusion_collision_integral=1.3,collision_integral_and_species_state_evidence_id='authored-point-coefficient',
        dilute_binary_ideal_gas_lennard_jones_combining_rules_reviewed=True)
    d.update(kw);return Request(**d)

@pytest.mark.parametrize('temp,p,m1,m2',[(360.,160000.,.022,.041),(200.,90000.,.004,.08),(800.,300000.,.06,.06)])
def test_relative_maxwell_speed_quadrature(temp,p,m1,m2):
    q=request(temperature_k=temp,pressure_pa_abs=p,species_1_molar_mass_kg_mol=m1,species_2_molar_mass_kg_mol=m2)
    mu=(m1/6.02214076e23)*(m2/(m1+m2));k=1.380649e-23
    speed_scale=math.sqrt(2*k*temp/mu)
    # Dimensionless relative Maxwell distribution, independent numerical integral.
    mean=speed_scale*quad(lambda z:4/math.sqrt(math.pi)*z**3*math.exp(-z*z),0,12,epsabs=1e-12)[0]
    n=p/(k*temp);sigma=(q.species_1_collision_diameter_m+q.species_2_collision_diameter_m)/2
    expected=3*mean/(32*n*sigma*sigma*q.supplied_diffusion_collision_integral)
    assert evaluate(q)['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(expected,rel=2e-13)

@pytest.mark.parametrize('m1,m2',[(.022,.041),(.004,.1)])
def test_species_interchange(m1,m2):
    q=request(species_1_molar_mass_kg_mol=m1,species_2_molar_mass_kg_mol=m2)
    swap=q.model_dump()
    for name in ('species','species_molar_mass_kg_mol','species_collision_diameter_m','species_well_depth_over_boltzmann_k'):
        pass
    swap.update(species_1=q.species_2,species_2=q.species_1,species_1_molar_mass_kg_mol=m2,species_2_molar_mass_kg_mol=m1,
        species_1_collision_diameter_m=q.species_2_collision_diameter_m,species_2_collision_diameter_m=q.species_1_collision_diameter_m,
        species_1_well_depth_over_boltzmann_k=q.species_2_well_depth_over_boltzmann_k,species_2_well_depth_over_boltzmann_k=q.species_1_well_depth_over_boltzmann_k)
    assert evaluate(q)['values']==pytest.approx(evaluate(Request(**swap))['values'])

@pytest.mark.parametrize('multiplier',[.5,3.])
def test_pressure_and_temperature_conditioned_on_same_supplied_omega(multiplier):
    q=request();d=evaluate(q)['values']['binary_molecular_diffusivity_m2_s']
    assert evaluate(request(pressure_pa_abs=q.pressure_pa_abs*multiplier))['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(d/multiplier)
    # This is conditional algebra at fixed supplied Omega, not a prediction of its T dependence.
    assert evaluate(request(temperature_k=q.temperature_k*multiplier))['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(d*multiplier**1.5)


def test_collision_diameter_scaling():
    q=request();d=evaluate(q)['values']['binary_molecular_diffusivity_m2_s']
    assert evaluate(request(species_1_collision_diameter_m=2*q.species_1_collision_diameter_m,species_2_collision_diameter_m=2*q.species_2_collision_diameter_m))['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(d/4)


def test_omega_scaling():
    q=request();d=evaluate(q)['values']['binary_molecular_diffusivity_m2_s']
    assert evaluate(request(supplied_diffusion_collision_integral=2*q.supplied_diffusion_collision_integral))['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(d/2)


def test_reduced_temperature_geometric_mixing():
    q=request();assert evaluate(q)['values']['reduced_temperature']==pytest.approx(q.temperature_k/math.sqrt(140*220))

@pytest.mark.parametrize('temp,pressure',[(300.,101325.),(700.,202650.)])
def test_independent_legacy_unit_conversion_rounded_coefficient(temp,pressure):
    q=request(temperature_k=temp,pressure_pa_abs=pressure)
    sigma_angstrom=(q.species_1_collision_diameter_m+q.species_2_collision_diameter_m)/2/1e-10
    m1=q.species_1_molar_mass_kg_mol*1000;m2=q.species_2_molar_mass_kg_mol*1000
    cm2_s=.001858*temp**1.5*math.sqrt(1/m1+1/m2)/(pressure/101325*sigma_angstrom**2*q.supplied_diffusion_collision_integral)
    # Rounded engineering prefactor is an approximate unit-conversion comparison only.
    assert evaluate(q)['values']['binary_molecular_diffusivity_m2_s']==pytest.approx(cm2_s*1e-4,rel=7e-4)

@pytest.mark.parametrize('left,right',[(.1,.2),(.7,.3)])
def test_fick_relative_face_transfer_and_binary_mole_budget(left,right):
    length=.4;area=.03;c=42.;diff=2e-5;time=5.
    q=BinaryMolarFickRequest(id='authored',supplied_binary_molecular_diffusivity_m2_s=diff,supplied_total_molar_concentration_mol_m3=c,
        species_1_mole_fraction_gradient_per_m=((right-left)/length,0,0),same_state_coefficient_concentration_and_gradient_evidence_id='authored-local',
        isothermal_isobaric_binary_molar_average_frame_no_cross_driving_reviewed=True)
    v=evaluate_binary_molar_fick(q)['values'];j=v['species_1_relative_molar_flux_mol_m2_s'][0]
    # Concentration difference over a planar layer yields the independent face transfer.
    transfer=-diff*(c*right-c*left)/length*area*time
    assert j*area*time==pytest.approx(transfer)
    assert j+v['species_2_relative_molar_flux_mol_m2_s'][0]==0
    assert not v['total_species_flux_predicted']


def test_unknown_review_and_missing_coefficient_are_not_zero():
    assert evaluate(request(dilute_binary_ideal_gas_lennard_jones_combining_rules_reviewed=False))['status']=='needs_data'
    with pytest.raises(ValueError):request(supplied_diffusion_collision_integral=0)
