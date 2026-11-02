"""Water-equilibrium observation from supplied same-state fugacity closures.

Gas fugacity coefficients and condensed-water fugacity belong to one reviewed
equilibrium state. They can come from an EOS/phase-equilibrium provider; using
them here does not infer the coefficients from pressure or a gas label.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, json_value
from .gas_caloric_mixture import (
    GasCaloricComponent, GasCaloricMixtureRequest, evaluate_gas_caloric_mixture,
)


class SaturatedGasFugacityRequest(Contract):
    temperature_K: Positive
    pressure_Pa_abs: Positive
    dry_components: tuple[GasCaloricComponent, ...] = Field(min_length=1)
    water_component_id: Name = 'water'
    water_molar_mass_kg_mol: Positive
    dry_molar_quantity: Positive
    quantity_basis: Literal['molar_amount_mol', 'molar_flow_mol_s']
    clock_id: Name
    equilibrium_state_id: Name
    condensed_reference_phase: Literal['liquid_water', 'ice']
    condensed_water_fugacity_Pa: Positive
    gas_water_fugacity_coefficient: Positive
    coefficient_at_final_equilibrium_composition_reviewed: Literal[True]
    common_state_condensed_and_gas_fugacity_reference_reviewed: Literal[True]

    @model_validator(mode='after')
    def dry_species(self):
        if self.water_component_id in {c.id for c in self.dry_components}:
            raise ValueError('dry carrier must exclude the separately identified water component')
        # Validate the composition contract without running a property model.
        GasCaloricMixtureRequest(mode='molar_mass_only', fraction_basis='mole',
            components=self.dry_components, property_basis_id=self.equilibrium_state_id,
            temperature_K=self.temperature_K,
            nonreacting_component_composition_ledger_reviewed=True)
        water_fraction = (self.condensed_water_fugacity_Pa/self.pressure_Pa_abs)/self.gas_water_fugacity_coefficient
        if not math.isfinite(water_fraction) or not 0 < water_fraction < 1:
            raise ValueError('saturated dry-carrier fugacity coordinates require water mole fraction strictly between zero and one')
        return self


def observe_saturated_gas_fugacity(q: SaturatedGasFugacityRequest) -> dict:
    """Solve the reviewed water fugacity equality and preserve dry constituents.

    The supplied gas coefficient must be evaluated at the final composition;
    callers iterating an EOS flash must iterate that closure upstream. No
    ideal-gas density, partial-pressure saturation law or heat capacity is
    substituted for the nonideal equilibrium coordinates.
    """
    q = SaturatedGasFugacityRequest.model_validate(q.model_dump(mode='python'))
    dry = evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(
        mode='molar_mass_only', fraction_basis='mole', components=q.dry_components,
        property_basis_id=q.equilibrium_state_id, temperature_K=q.temperature_K,
        nonreacting_component_composition_ledger_reviewed=True))
    if dry.status != 'computed':
        return dict(status=dry.status, issues=dry.issues)
    y = (q.condensed_water_fugacity_Pa/q.pressure_Pa_abs)/q.gas_water_fugacity_coefficient
    if not math.isfinite(y) or not 0 < y < 1:
        raise ValueError('reviewed saturated dry-carrier state requires water mole fraction strictly between zero and one')
    dry_quantity = q.dry_molar_quantity
    water_quantity = dry_quantity*y/(1-y)
    wet_quantity = dry_quantity+water_quantity
    components = tuple(c.model_copy(update={'fraction': dry.mole_fractions[c.id]*(1-y)}) for c in q.dry_components)
    components += (GasCaloricComponent(id=q.water_component_id, fraction=y,
                                      molar_mass_kg_mol=q.water_molar_mass_kg_mol),)
    wet = evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(
        mode='molar_mass_only', fraction_basis='mole', components=components,
        property_basis_id=q.equilibrium_state_id, temperature_K=q.temperature_K,
        nonreacting_component_composition_ledger_reviewed=True))
    if wet.status != 'computed':
        return dict(status=wet.status, issues=wet.issues)
    dry_mass = dry_quantity*dry.mixture_molar_mass_kg_mol
    water_mass = water_quantity*q.water_molar_mass_kg_mol
    wet_mass = wet_quantity*wet.mixture_molar_mass_kg_mol
    return json_value(dict(status='computed', water_mole_fraction=y,
                gas_water_fugacity_Pa=y*q.gas_water_fugacity_coefficient*q.pressure_Pa_abs,
                equilibrium_fugacity_residual_Pa=y*q.gas_water_fugacity_coefficient*q.pressure_Pa_abs-q.condensed_water_fugacity_Pa,
                dry_molar_quantity=dry_quantity, water_molar_quantity=water_quantity,
                wet_molar_quantity=wet_quantity, dry_mass_quantity=dry_mass,
                water_mass_quantity=water_mass, wet_mass_quantity=wet_mass,
                mass_quantity_unit='kg/s' if q.quantity_basis=='molar_flow_mol_s' else 'kg',
                molar_quantity_unit='mol/s' if q.quantity_basis=='molar_flow_mol_s' else 'mol',
                mass_balance_residual=math.fsum((wet_mass,-dry_mass,-water_mass)),
                wet_composition=wet.model_dump(mode='json'),
                equilibrium_state_id=q.equilibrium_state_id, clock_id=q.clock_id,
                temperature_K=q.temperature_K, pressure_Pa_abs=q.pressure_Pa_abs,
                condensed_reference_phase=q.condensed_reference_phase))
