"""Neutral composition and supplied ideal-gas caloric properties, without viscosity inputs."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive
from .volume_weighting import normalized_weighted_sum

R_UNIVERSAL_J_MOL_K=6.02214076e23*1.380649e-23


class GasCaloricComponent(Contract):
    id: Name
    fraction: Nonnegative
    molar_mass_kg_mol: Positive | None
    ideal_gas_molar_cp_J_mol_K: Positive | None = None
    heat_capacity_temperature_K: Positive | None = None


class GasCaloricMixtureRequest(Contract):
    mode: Literal['molar_mass_only','ideal_gas_caloric']
    fraction_basis: Literal['mole','mass']
    components: tuple[GasCaloricComponent,...] = Field(min_length=1)
    property_basis_id: Name
    temperature_K: Positive
    nonreacting_component_composition_ledger_reviewed: bool = False
    ideal_gas_molar_heat_capacity_additivity_reviewed: bool = False

    @model_validator(mode='after')
    def fractions(self):
        if len({c.id for c in self.components})!=len(self.components):raise ValueError('component identities must be unique')
        total=math.fsum(c.fraction for c in self.components)
        if not math.isclose(total,1.,rel_tol=0.,abs_tol=1e-12):
            raise ValueError('declared fractions must sum to one; arbitrary weights are not silently normalized')
        return self


class GasCaloricMixtureResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    property_basis_id: Name
    mixture_molar_mass_kg_mol: Positive | None = None
    mole_fractions: dict[str,float] | None = None
    mass_fractions: dict[str,float] | None = None
    ideal_gas_molar_cp_J_mol_K: Positive | None = None
    ideal_gas_molar_cv_J_mol_K: Positive | None = None
    ideal_gas_mass_cp_J_kg_K: Positive | None = None
    ideal_gas_mass_cv_J_kg_K: Positive | None = None
    ideal_gas_heat_capacity_ratio: Positive | None = None
    supplied_fraction_sum: Positive | None = None
    issues: tuple[str,...] = ()
    real_gas_excess_or_species_property_inferred: Literal[False] = False


def evaluate_gas_caloric_mixture(q: GasCaloricMixtureRequest) -> GasCaloricMixtureResult:
    """Use a composition ledger and the existing common weighted-sum primitive.

    Molar mass is a composition result, independent of a viscosity correlation.
    Caloric additivity applies only to the reviewed ideal-gas molar Cp values at
    the common temperature. No molecular Cp, air composition, reaction, phase
    split, real-gas excess term or temperature curve is guessed.
    """
    if not isinstance(q,GasCaloricMixtureRequest):raise TypeError('GasCaloricMixtureRequest required')
    q=GasCaloricMixtureRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_basis_id=q.property_basis_id)
    active=[c for c in q.components if c.fraction>0]
    if not q.nonreacting_component_composition_ledger_reviewed or any(c.molar_mass_kg_mol is None for c in active):
        return GasCaloricMixtureResult(status='needs_data',**base,issues=('all active species molar masses and composition-ledger review required',))
    total=math.fsum(c.fraction for c in active)
    raw={c.id:(c.fraction if q.fraction_basis=='mole' else c.fraction/c.molar_mass_kg_mol) for c in active}
    amount=math.fsum(raw.values())
    if not math.isfinite(amount) or amount<=0:raise ValueError('composition amount exceeds finite support')
    mole={key:value/amount for key,value in raw.items()}
    molar_mass=normalized_weighted_sum({c.id:c.molar_mass_kg_mol for c in active},mole)
    if not math.isfinite(molar_mass) or molar_mass<=0:raise ValueError('positive mixture molar mass exceeds finite support')
    mass={c.id:mole[c.id]*c.molar_mass_kg_mol/molar_mass for c in active}
    common=dict(mixture_molar_mass_kg_mol=molar_mass,
        mole_fractions={c.id:mole.get(c.id,0.) for c in q.components},
        mass_fractions={c.id:mass.get(c.id,0.) for c in q.components},supplied_fraction_sum=total)
    if q.mode=='molar_mass_only':return GasCaloricMixtureResult(status='computed',**base,**common)
    if (not q.ideal_gas_molar_heat_capacity_additivity_reviewed
        or any(c.ideal_gas_molar_cp_J_mol_K is None or c.heat_capacity_temperature_K is None for c in active)):
        return GasCaloricMixtureResult(status='needs_data',**base,**common,
            issues=('complete ideal-gas molar Cp values at common temperature and additivity review required',))
    if any(not math.isclose(c.heat_capacity_temperature_K,q.temperature_K,rel_tol=1e-12,abs_tol=1e-10) for c in active):
        raise ValueError('component heat capacities refer to different temperatures')
    if any(c.ideal_gas_molar_cp_J_mol_K<=R_UNIVERSAL_J_MOL_K for c in active):
        return GasCaloricMixtureResult(status='out_of_scope',**base,**common,
            issues=('ideal-gas species must have positive molar Cv=Cp-R on this stable caloric basis',))
    cp=normalized_weighted_sum({c.id:c.ideal_gas_molar_cp_J_mol_K for c in active},mole)
    cv=cp-R_UNIVERSAL_J_MOL_K
    if cv<=0:raise ValueError('positive ideal-gas mixture Cv is below finite numerical support')
    mass_cp=cp/molar_mass;mass_cv=cv/molar_mass;ratio=cp/cv
    if not all(math.isfinite(v) and v>0 for v in (molar_mass,cp,cv,mass_cp,mass_cv,ratio)):
        raise ValueError('positive caloric property exceeds finite numerical support')
    return GasCaloricMixtureResult(status='computed',**base,**common,ideal_gas_molar_cp_J_mol_K=cp,
        ideal_gas_molar_cv_J_mol_K=cv,ideal_gas_mass_cp_J_kg_K=mass_cp,
        ideal_gas_mass_cv_J_kg_K=mass_cv,ideal_gas_heat_capacity_ratio=ratio)


class GasCaloricAmountComponent(Contract):
    id: Name
    input_quantity: Nonnegative
    molar_mass_kg_mol: Positive | None
    ideal_gas_molar_cp_J_mol_K: Positive | None = None
    heat_capacity_temperature_K: Positive | None = None


class GasCaloricAmountsRequest(Contract):
    mode: Literal['molar_mass_only','ideal_gas_caloric']
    quantity_basis: Literal['molar_amount_mol','mass_amount_kg','molar_flow_mol_s','mass_flow_kg_s']
    components: tuple[GasCaloricAmountComponent,...] = Field(min_length=1)
    common_clock_id: Name
    property_basis_id: Name
    temperature_K: Positive
    nonreacting_component_composition_ledger_reviewed: bool = False
    ideal_gas_molar_heat_capacity_additivity_reviewed: bool = False


class GasCaloricAmountsResult(Contract):
    common_clock_id: Name
    quantity_basis: Literal['molar_amount_mol','mass_amount_kg','molar_flow_mol_s','mass_flow_kg_s']
    total_supplied_quantity: Positive
    mixture: GasCaloricMixtureResult


def gas_caloric_mixture_from_amounts(q: GasCaloricAmountsRequest) -> GasCaloricAmountsResult:
    """Normalize explicitly labeled absolute component amounts or same-clock rates.

    The fraction API deliberately rejects arbitrary weights. This adapter labels
    their physical mass/mole and amount/rate meaning, retains the total quantity
    and shared clock, then calls that same composition/caloric implementation.
    """
    if not isinstance(q,GasCaloricAmountsRequest):raise TypeError('GasCaloricAmountsRequest required')
    q=GasCaloricAmountsRequest.model_validate(q.model_dump(mode='python'))
    total=math.fsum(c.input_quantity for c in q.components)
    if not math.isfinite(total) or total<=0:raise ValueError('positive finite total component quantity required')
    result=evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(mode=q.mode,
        fraction_basis='mass' if q.quantity_basis.startswith('mass') else 'mole',
        components=tuple(GasCaloricComponent(id=c.id,fraction=c.input_quantity/total,
            molar_mass_kg_mol=c.molar_mass_kg_mol,ideal_gas_molar_cp_J_mol_K=c.ideal_gas_molar_cp_J_mol_K,
            heat_capacity_temperature_K=c.heat_capacity_temperature_K) for c in q.components),
        temperature_K=q.temperature_K,property_basis_id=q.property_basis_id,
        nonreacting_component_composition_ledger_reviewed=q.nonreacting_component_composition_ledger_reviewed,
        ideal_gas_molar_heat_capacity_additivity_reviewed=q.ideal_gas_molar_heat_capacity_additivity_reviewed))
    return GasCaloricAmountsResult(common_clock_id=q.common_clock_id,quantity_basis=q.quantity_basis,
        total_supplied_quantity=total,mixture=result)
