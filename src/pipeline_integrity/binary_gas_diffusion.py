"""Reviewed dilute binary-gas kinetic diffusivity and molar-frame Fick flux.

Collision parameters and the collision integral are supplied at the declared
state. No species table, collision-integral correlation, dense-gas correction
or effective longitudinal mixing coefficient is inferred.
"""
import math
from .contracts import Contract,Name,Positive,digest

BOLTZMANN_J_K=1.380649e-23
AVOGADRO_PER_MOL=6.02214076e23

class BinaryGasDiffusivityRequest(Contract):
    id: Name
    species_1: Name
    species_2: Name
    temperature_k: Positive
    pressure_pa_abs: Positive
    species_1_molar_mass_kg_mol: Positive
    species_2_molar_mass_kg_mol: Positive
    species_1_collision_diameter_m: Positive
    species_2_collision_diameter_m: Positive
    species_1_well_depth_over_boltzmann_k: Positive
    species_2_well_depth_over_boltzmann_k: Positive
    supplied_diffusion_collision_integral: Positive
    collision_integral_and_species_state_evidence_id: Name
    dilute_binary_ideal_gas_lennard_jones_combining_rules_reviewed: bool = False


def evaluate_binary_gas_diffusivity(q: BinaryGasDiffusivityRequest):
    """First-approximation kinetic result, using arithmetic sigma/geometric depth.

    D = (3/16)/(n sigma² Omega) sqrt(2 k T/(pi reduced_mass)).
    The ideal dilute number density n=p/(kT) belongs to this closure only.
    Supplied Omega must apply at the returned reduced temperature; a positive
    number alone does not establish its experimental or correlation validity.
    """
    q=BinaryGasDiffusivityRequest.model_validate(q.model_dump())
    base=dict(request_digest=digest(q),field_or_material_calibration_qualified=False)
    if not q.dilute_binary_ideal_gas_lennard_jones_combining_rules_reviewed:return dict(status='needs_data',**base)
    sigma=q.species_1_collision_diameter_m/2+q.species_2_collision_diameter_m/2
    log_depth=(math.log(q.species_1_well_depth_over_boltzmann_k)+math.log(q.species_2_well_depth_over_boltzmann_k))/2
    # Reciprocal sum avoids overflowing the product of species masses.
    m1=q.species_1_molar_mass_kg_mol;m2=q.species_2_molar_mass_kg_mol
    small,large=sorted((m1,m2));mu=small/(1+small/large)/AVOGADRO_PER_MOL
    if mu<=0:raise ValueError('molecular reduced mass below finite support')
    log_d=(math.log(3/16)+1.5*math.log(BOLTZMANN_J_K*q.temperature_k)
        +.5*math.log(2/(math.pi*mu))-math.log(q.pressure_pa_abs)-2*math.log(sigma)-math.log(q.supplied_diffusion_collision_integral))
    d=math.exp(log_d);reduced_t=math.exp(math.log(q.temperature_k)-log_depth)
    if not all(math.isfinite(v) and v>0 for v in (sigma,d,reduced_t)):raise ValueError('binary diffusion observation exceeds finite support')
    return dict(status='computed',**base,values=dict(binary_molecular_diffusivity_m2_s=d,
        mixed_collision_diameter_m=sigma,reduced_temperature=reduced_t,
        supplied_diffusion_collision_integral=q.supplied_diffusion_collision_integral,
        molecular_reduced_mass_kg=mu,temperature_k=q.temperature_k,pressure_pa_abs=q.pressure_pa_abs))


class BinaryMolarFickRequest(Contract):
    id: Name
    supplied_binary_molecular_diffusivity_m2_s: Positive
    supplied_total_molar_concentration_mol_m3: Positive
    species_1_mole_fraction_gradient_per_m: tuple[float,float,float]
    same_state_coefficient_concentration_and_gradient_evidence_id: Name
    isothermal_isobaric_binary_molar_average_frame_no_cross_driving_reviewed: bool = False


def evaluate_binary_molar_fick(q: BinaryMolarFickRequest):
    """Diffusive molar flux relative to molar-average motion, not total flux.

    This local isothermal/isobaric binary constitutive observation omits bulk
    advection, thermodiffusion, pressure diffusion and multicomponent coupling.
    Concentration and D are explicitly supplied at this same state.
    """
    q=BinaryMolarFickRequest.model_validate(q.model_dump())
    base=dict(request_digest=digest(q),field_or_material_calibration_qualified=False)
    if not q.isothermal_isobaric_binary_molar_average_frame_no_cross_driving_reviewed:return dict(status='needs_data',**base)
    j=tuple(-q.supplied_total_molar_concentration_mol_m3*q.supplied_binary_molecular_diffusivity_m2_s*g for g in q.species_1_mole_fraction_gradient_per_m)
    if any(not math.isfinite(v) for v in j):raise ValueError('flux exceeds finite support')
    return dict(status='computed',**base,values=dict(species_1_relative_molar_flux_mol_m2_s=j,species_2_relative_molar_flux_mol_m2_s=tuple(-v for v in j),
        flux_frame='molar_average',total_species_flux_predicted=False))
