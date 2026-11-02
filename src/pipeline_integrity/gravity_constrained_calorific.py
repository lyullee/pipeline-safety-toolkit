"""Identifiable two-fuel/inert gravity observation, not a universal gas fit."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract, Fraction, Name, Nonnegative, Positive
from .gas_combustion_reference import ComponentCombustionDatum
from .gas_caloric_mixture import GasCaloricComponent
from .gas_state_observer import GasStateObservationRequest, observe_gas_state
from .volume_weighting import normalized_weighted_sum


class GravityCalorificRequest(Contract):
    gas_specific_gravity_to_air: Positive | None
    dry_nitrogen_mole_fraction: Fraction | None
    air_molar_mass_kg_mol: Positive | None
    nitrogen_data: ComponentCombustionDatum
    fuel_data: tuple[ComponentCombustionDatum, ...] = Field(min_length=2,max_length=2)
    gravity_measurement_basis: Literal['dry_gas','wet_gas']
    gravity_measurement_water_mole_fraction: float | None = Field(ge=0,lt=1)
    reference_water_mole_fraction: float | None = Field(ge=0,lt=1)
    water_molar_mass_kg_mol: Positive | None
    reference_temperature_K: Positive | None
    reference_pressure_Pa_abs: Positive | None
    combustion_reference_id: Name
    calorific_basis: Literal['gross_liquid_product_water','net_vapor_product_water']
    two_known_fuels_and_nitrogen_are_complete_dry_composition_reviewed: bool = False
    same_PT_ideal_gas_gravity_and_single_gas_reference_reviewed: bool = False
    supplied_combustion_reference_and_water_basis_reviewed: bool = False


class GravityCalorificResult(Contract):
    status: Literal['computed','needs_data','not_identifiable','out_of_scope']
    calorific_basis: Literal['gross_liquid_product_water','net_vapor_product_water']
    combustion_reference_id: Name
    reference_temperature_K: Positive | None
    reference_pressure_Pa_abs: Positive | None
    reference_water_mole_fraction: float | None
    dry_mole_fractions: dict[str,float] | None = None
    dry_molar_mass_kg_mol: Positive | None = None
    reference_density_kg_m3: Positive | None = None
    dry_molar_combustion_energy_J_mol: Nonnegative | None = None
    reference_molar_combustion_energy_J_mol: Nonnegative | None = None
    reference_mass_combustion_energy_J_kg: Nonnegative | None = None
    reference_volumetric_combustion_energy_J_m3: Nonnegative | None = None
    gravity_mass_residual_kg_mol: float | None = None
    issues: tuple[str,...] = ()
    arbitrary_gas_or_legacy_gravity_fit_inferred: Literal[False] = False
    field_or_standard_qualified: Literal[False] = False


def observe_gravity_constrained_calorific(q:GravityCalorificRequest)->GravityCalorificResult:
    """Infer a constrained binary fuel split and observe its calorific property.

Same-PT ideal-gas density ratio fixes molar mass. Known dry nitrogen leaves
two fuel fractions with two linear constraints. An arbitrary gas has no such
unique inverse and must not use this branch. Source/reference water fractions
are supplied, never inferred as saturated from an unspecified humidity state.
Component released heats must share the named HHV/LHV combustion convention.
Existing composition, weighted-property and ideal-state owners are reused.
"""
    q=GravityCalorificRequest.model_validate(q.model_dump(mode='python'))
    def result(**values):
        return GravityCalorificResult(calorific_basis=q.calorific_basis,
            combustion_reference_id=q.combustion_reference_id,
            reference_temperature_K=q.reference_temperature_K,
            reference_pressure_Pa_abs=q.reference_pressure_Pa_abs,
            reference_water_mole_fraction=q.reference_water_mole_fraction,**values)
    required=(q.gas_specific_gravity_to_air,q.dry_nitrogen_mole_fraction,q.air_molar_mass_kg_mol,
        q.gravity_measurement_water_mole_fraction,q.reference_water_mole_fraction,q.reference_temperature_K,q.reference_pressure_Pa_abs)
    data=(*q.fuel_data,q.nitrogen_data)
    if any(x is None for x in required) or not all((q.two_known_fuels_and_nitrogen_are_complete_dry_composition_reviewed,q.same_PT_ideal_gas_gravity_and_single_gas_reference_reviewed,q.supplied_combustion_reference_and_water_basis_reviewed)):
        return result(status='needs_data',issues=('Gravity and nitrogen alone cannot identify arbitrary gas; explicit complete two-fuel class, ideal-state/reference and combustion-property reviews required.',))
    if any(d.molar_mass_kg_mol is None or d.released_combustion_energy_J_mol is None for d in data):
        return result(status='needs_data',issues=('All identified fuel/inert component molar masses and same-reference released heats required.',))
    if len({d.fluid for d in data})!=3:raise ValueError('Two fuel and one inert identities must be distinct.')
    if any(d.combustion_reference_id!=q.combustion_reference_id for d in data):raise ValueError('Combustion references differ.')
    if q.nitrogen_data.released_combustion_energy_J_mol!=0:raise ValueError('Declared nitrogen inert must have explicit zero released combustion energy.')
    yg=q.gravity_measurement_water_mole_fraction;yr=q.reference_water_mole_fraction
    if q.gravity_measurement_basis=='dry_gas' and yg!=0:raise ValueError('Dry gravity basis requires zero water fraction.')
    if (yg>0 or yr>0) and q.water_molar_mass_kg_mol is None:
        return result(status='needs_data',issues=('Explicit water molar mass required for wet density/energy mass basis.',))
    mw=q.water_molar_mass_kg_mol or 0.
    dry_mass=(q.gas_specific_gravity_to_air*q.air_molar_mass_kg_mol-yg*mw)/(1-yg)
    a,b=q.fuel_data;nitrogen=q.nitrogen_data;yn=q.dry_nitrogen_mole_fraction
    if a.molar_mass_kg_mol==b.molar_mass_kg_mol:
        return result(status='not_identifiable',issues=('Equal fuel molar masses cannot be separated by a gravity observation.',))
    remainder=1-yn
    if remainder==0:
        if not math.isclose(dry_mass,nitrogen.molar_mass_kg_mol,rel_tol=1e-12):
            return result(status='out_of_scope',issues=('Pure inert gravity is inconsistent with supplied molar mass.',))
        fa=fb=0.
    else:
        fa=(dry_mass-yn*nitrogen.molar_mass_kg_mol-remainder*b.molar_mass_kg_mol)/(a.molar_mass_kg_mol-b.molar_mass_kg_mol)
        fb=remainder-fa
        if fa<0 or fb<0:
            return result(status='out_of_scope',issues=('Observed gravity lies outside the constrained component convex interval; no fractions clipped.',))
    fractions={a.fluid:fa,b.fluid:fb,nitrogen.fluid:yn}
    components=tuple(GasCaloricComponent(id=d.fluid,fraction=fractions[d.fluid]*(1-yr),molar_mass_kg_mol=d.molar_mass_kg_mol) for d in data if fractions[d.fluid]>0)
    if yr>0:components=(*components,GasCaloricComponent(id='Water',fraction=yr,molar_mass_kg_mol=mw))
    state=observe_gas_state(GasStateObservationRequest(model='ideal',coordinate='pressure_temperature',components=components,fraction_basis='mole',temperature_K=q.reference_temperature_K,pressure_Pa_abs=q.reference_pressure_Pa_abs,composition_and_gas_property_domain_reviewed=True,property_basis_id=q.combustion_reference_id))
    if state.status!='computed':return result(status=state.status,issues=state.issues)
    heat=normalized_weighted_sum({d.fluid:d.released_combustion_energy_J_mol for d in data},fractions)
    refheat=(1-yr)*heat;md=normalized_weighted_sum({d.fluid:d.molar_mass_kg_mol for d in data},fractions)
    massheat=refheat/state.mixture_molar_mass_kg_mol
    return result(status='computed',dry_mole_fractions=fractions,dry_molar_mass_kg_mol=md,
        reference_density_kg_m3=state.density_kg_m3,dry_molar_combustion_energy_J_mol=heat,
        reference_molar_combustion_energy_J_mol=refheat,reference_mass_combustion_energy_J_kg=massheat,
        reference_volumetric_combustion_energy_J_m3=massheat*state.density_kg_m3,
        gravity_mass_residual_kg_mol=md-dry_mass)
