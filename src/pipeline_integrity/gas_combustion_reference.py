"""Supplied combustion-energy properties on explicit gas reference volumes.

This adds an energy observation to the existing dry-species conservation owner.
Combustion heat is supplied independently of sensible thermodynamic EOS enthalpy.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive,RunContext
from .gas_basis import GasBasisRequest,evaluate_gas_basis
from .gas_caloric_mixture import GasCaloricComponent,GasCaloricMixtureRequest,evaluate_gas_caloric_mixture
from .volume_weighting import normalized_weighted_sum


class ComponentCombustionDatum(Contract):
    fluid: Name
    molar_mass_kg_mol: Positive | None
    released_combustion_energy_J_mol: Nonnegative | None
    combustion_reference_id: Name


class GasCombustionReferenceRequest(Contract):
    gas_basis: GasBasisRequest
    component_data: tuple[ComponentCombustionDatum,...] = Field(min_length=1,max_length=64)
    calorific_basis: Literal['gross_liquid_product_water','net_vapor_product_water']
    combustion_reference_id: Name
    combustion_reference_temperature_K: Positive
    combustion_reference_pressure_Pa_abs: Positive
    vapor_water_molar_mass_kg_mol: Positive | None = None
    supplied_component_combustion_data_additivity_and_reference_reviewed: bool = False

    @model_validator(mode='after')
    def component_reference(self):
        if len({d.fluid for d in self.component_data})!=len(self.component_data):
            raise ValueError('combustion component data identities must be unique')
        if {d.fluid for d in self.component_data}!={d.fluid for d in self.gas_basis.dry_components}:
            raise ValueError('every dry-gas component needs its own combustion datum; no hidden fuel/inert classification')
        if any(d.combustion_reference_id!=self.combustion_reference_id for d in self.component_data):
            raise ValueError('component combustion energies must share the declared reference and product-water convention')
        return self


class GasCombustionReferenceResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    calorific_basis: Literal['gross_liquid_product_water','net_vapor_product_water']
    combustion_reference_id: Name
    gas_basis_receipt: dict | None = None
    dry_molar_combustion_energy_J_mol: Nonnegative | None = None
    dry_mass_combustion_energy_J_kg: Nonnegative | None = None
    source_molar_combustion_energy_J_mol: Nonnegative | None = None
    target_molar_combustion_energy_J_mol: Nonnegative | None = None
    source_mass_combustion_energy_J_kg: Nonnegative | None = None
    target_mass_combustion_energy_J_kg: Nonnegative | None = None
    source_volumetric_combustion_energy_J_m3: Nonnegative | None = None
    target_volumetric_combustion_energy_J_m3: Nonnegative | None = None
    source_combustion_energy_quantity: Nonnegative | None = None
    target_combustion_energy_quantity: Nonnegative | None = None
    energy_quantity_unit: Literal['J','W'] | None = None
    energy_reference_conversion_residual: float | None = None
    source_mass_quantity: Nonnegative | None = None
    target_mass_quantity: Nonnegative | None = None
    water_mass_to_gas_quantity: float | None = None
    mass_quantity_unit: Literal['kg','kg/s'] | None = None
    mass_reference_conversion_residual: float | None = None
    issues: tuple[str,...] = ()
    sensible_heat_or_initial_water_condensation_energy_inferred: Literal[False] = False


def observe_gas_combustion_reference(q:GasCombustionReferenceRequest,context:RunContext)->GasCombustionReferenceResult:
    """Compare dry/wet energy density without changing the dry fuel inventory.

    Each datum is positive released heat at the common combustion reference;
    an inert component requires an explicit zero. Water already in the gas is
    a diluent, with no extra latent or sensible heat credit. Product-water phase
    labels HHV versus LHV; neither is inferred or converted from the other.
    Volume-reference PT/Z changes do not assert a physical heating/drying path.
    """
    q=GasCombustionReferenceRequest.model_validate(q.model_dump(mode='python'))
    common=dict(calorific_basis=q.calorific_basis,combustion_reference_id=q.combustion_reference_id)
    if not q.supplied_component_combustion_data_additivity_and_reference_reviewed:
        return GasCombustionReferenceResult(status='needs_data',issues=('same-reference component combustion-energy additivity requires review',),**common)
    data={d.fluid:d for d in q.component_data}
    if any(d.released_combustion_energy_J_mol is None or d.molar_mass_kg_mol is None for d in q.component_data):
        return GasCombustionReferenceResult(status='needs_data',issues=('all dry constituents need explicit kg/mol and released combustion J/mol, including zero for inerts',),**common)
    ys,yt=q.gas_basis.source.water_vapor_mole_fraction,q.gas_basis.target.water_vapor_mole_fraction
    if (ys>0 or yt>0) and q.vapor_water_molar_mass_kg_mol is None:
        return GasCombustionReferenceResult(status='needs_data',issues=('wet mass basis needs an explicit vapor-water kg/mol value',),**common)
    ledger=evaluate_gas_basis(q.gas_basis,context)
    if ledger['status']!='computed':
        return GasCombustionReferenceResult(status=ledger['status'],issues=tuple(ledger['issues']),gas_basis_receipt=ledger,**common)
    mixture=evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(mode='molar_mass_only',fraction_basis='mole',
        components=tuple(GasCaloricComponent(id=c.fluid,fraction=c.mole_fraction,molar_mass_kg_mol=data[c.fluid].molar_mass_kg_mol)
            for c in q.gas_basis.dry_components),temperature_K=q.combustion_reference_temperature_K,
        property_basis_id=q.combustion_reference_id,nonreacting_component_composition_ledger_reviewed=True))
    if mixture.status!='computed':
        return GasCombustionReferenceResult(status=mixture.status,issues=mixture.issues,gas_basis_receipt=ledger,**common)
    heat=normalized_weighted_sum({k:d.released_combustion_energy_J_mol for k,d in data.items()},mixture.mole_fractions)
    md=mixture.mixture_molar_mass_kg_mol;mw=q.vapor_water_molar_mass_kg_mol or 0.
    hs,ht=(1-ys)*heat,(1-yt)*heat
    ms,mt=(1-ys)*md+ys*mw,(1-yt)*md+yt*mw
    values,moles=ledger['values'],ledger['molar_ledger']
    source_density=values['source_molar_density_mol_m3'];target_density=values['target_molar_density_mol_m3']
    es,et=source_density*hs,target_density*ht
    source_energy=es*q.gas_basis.source_value;target_energy=et*values['target_value']
    source_mass=moles['source_total_molar_quantity']*ms
    target_mass=moles['target_total_molar_quantity']*mt
    water_mass=moles['net_water_to_gas_quantity']*mw
    energy_residual=target_energy-source_energy;mass_residual=target_mass-source_mass-water_mass
    if not math.isclose(source_energy,target_energy,rel_tol=2e-12,abs_tol=1e-12):
        raise ValueError('dry combustion-energy reference ledger does not close')
    if abs(mass_residual)>1e-12+2e-12*max(source_mass,target_mass):
        raise ValueError('wet/dry water-transfer mass ledger does not close')
    return GasCombustionReferenceResult(status='computed',gas_basis_receipt=ledger,
        dry_molar_combustion_energy_J_mol=heat,dry_mass_combustion_energy_J_kg=heat/md,
        source_molar_combustion_energy_J_mol=hs,target_molar_combustion_energy_J_mol=ht,
        source_mass_combustion_energy_J_kg=hs/ms,target_mass_combustion_energy_J_kg=ht/mt,
        source_volumetric_combustion_energy_J_m3=es,target_volumetric_combustion_energy_J_m3=et,
        source_combustion_energy_quantity=source_energy,target_combustion_energy_quantity=target_energy,
        energy_quantity_unit='J' if q.gas_basis.quantity_kind=='volume' else 'W',
        energy_reference_conversion_residual=energy_residual,
        source_mass_quantity=source_mass,target_mass_quantity=target_mass,water_mass_to_gas_quantity=water_mass,
        mass_quantity_unit='kg' if q.gas_basis.quantity_kind=='volume' else 'kg/s',
        mass_reference_conversion_residual=mass_residual,**common)
