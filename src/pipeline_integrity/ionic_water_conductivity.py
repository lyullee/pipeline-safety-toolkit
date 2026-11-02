"""Conditional ionic-current observation from a reviewed water analysis.

Total dissolved mass alone does not determine charge carriers or their drift.
Mobilities describe electrical drift speed per electric-field magnitude at the
declared solution state; they are not diffusion coefficients or molar conductance.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field
from .contracts import Contract, Fraction, Name, Nonnegative, Positive

FARADAY_C_MOL = 96485.33212331002  # Exact SI elementary charge × Avogadro constant.


class DissolvedSpecies(Contract):
    species_id: Name
    dissolved_mass_fraction: Fraction
    molar_mass_kg_mol: Positive
    charge_number: int = Field(strict=True)
    electrical_mobility_m2_V_s: Nonnegative | None


class IonicWaterRequest(Contract):
    dissolved_solids_kg_m3: Nonnegative | None
    species: tuple[DissolvedSpecies, ...]
    temperature_K: Positive | None
    pressure_Pa_abs: Positive | None
    coefficient_temperature_K: Positive | None
    coefficient_pressure_Pa_abs: Positive | None
    analysis_id: Name | None
    transport_coefficient_id: Name | None
    ionic_analysis_and_transport_domain_reviewed: bool = False
    background_conductivity_S_m: Nonnegative | None
    electric_field_V_m: float | None = None


class IonicSpeciesObservation(Contract):
    species_id: Name
    concentration_mol_m3: Nonnegative
    mass_concentration_kg_m3: Nonnegative
    conductivity_S_m: Nonnegative


class IonicWaterResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    analysis_id: Name | None
    conductivity_S_m: Nonnegative | None = None
    resistivity_ohm_m: Positive | None = None
    net_charge_concentration_C_m3: float | None = None
    ionic_strength_mol_m3: Nonnegative | None = None
    dissolved_mass_closure_kg_m3: float | None = None
    current_density_A_m2: float | None = None
    joule_heating_W_m3: Nonnegative | None = None
    species: tuple[IonicSpeciesObservation, ...] = ()
    issues: tuple[str, ...] = ()
    TDS_only_or_universal_freshwater_fit_inferred: Literal[False] = False
    field_or_corrosion_protection_qualified: Literal[False] = False


def observe_ionic_water_conductivity(q: IonicWaterRequest) -> IonicWaterResult:
    """Sum charge fluxes with explicitly supplied, state-specific mobilities.

For each carrier, j_i/E = F |z_i| u_i c_i. Electroneutrality is a
precondition for this uniform bulk solution. Background conductivity must be
declared separately; no pure-water, dissociation or temperature law is inferred.
The small-field linear response and concentration applicability of the supplied
mobilities require review. This is not a universal dissolved-solids correlation.
"""
    q = IonicWaterRequest.model_validate(q.model_dump(mode='python'))
    base = {'analysis_id': q.analysis_id}
    required = (q.dissolved_solids_kg_m3, q.temperature_K, q.pressure_Pa_abs,
                q.coefficient_temperature_K, q.coefficient_pressure_Pa_abs,
                q.analysis_id, q.transport_coefficient_id, q.background_conductivity_S_m)
    if any(x is None for x in required) or not q.species or not q.ionic_analysis_and_transport_domain_reviewed:
        return IonicWaterResult(status='needs_data', **base, issues=('Composition, state-matched transport coefficients, explicit background and reviewed linear-response domain required; dissolved mass alone is insufficient.',))
    if not math.isclose(q.temperature_K, q.coefficient_temperature_K, rel_tol=1e-12) or not math.isclose(q.pressure_Pa_abs, q.coefficient_pressure_Pa_abs, rel_tol=1e-12):
        return IonicWaterResult(status='out_of_scope', **base, issues=('No unreviewed temperature or pressure correction of mobilities is performed.',))
    if len({s.species_id for s in q.species}) != len(q.species):
        raise ValueError('Dissolved species identities must be unique.')
    if not math.isclose(math.fsum(s.dissolved_mass_fraction for s in q.species), 1., rel_tol=0, abs_tol=1e-12):
        raise ValueError('Mass fractions must account for all declared dissolved solids; no implicit normalization.')
    active = [s for s in q.species if s.charge_number and s.dissolved_mass_fraction > 0 and q.dissolved_solids_kg_m3 > 0]
    if any(s.electrical_mobility_m2_V_s is None for s in active):
        return IonicWaterResult(status='needs_data', **base, issues=('Every active charged species needs a state-specific electrical mobility.',))
    concentrations = [q.dissolved_solids_kg_m3 * s.dissolved_mass_fraction / s.molar_mass_kg_mol for s in q.species]
    charge_mol = math.fsum(s.charge_number*c for s,c in zip(q.species, concentrations))
    charge_scale = math.fsum(abs(s.charge_number)*c for s,c in zip(q.species, concentrations))
    if abs(charge_mol) > max(1e-12, charge_scale*1e-10):
        return IonicWaterResult(status='out_of_scope', **base, net_charge_concentration_C_m3=charge_mol*FARADAY_C_MOL, issues=('Bulk solution must be electroneutral; missing counterions are not invented.',))
    rows = tuple(IonicSpeciesObservation(species_id=s.species_id, concentration_mol_m3=c,
        mass_concentration_kg_m3=c*s.molar_mass_kg_mol,
        conductivity_S_m=FARADAY_C_MOL*abs(s.charge_number)*c*(s.electrical_mobility_m2_V_s or 0.))
        for s,c in zip(q.species, concentrations))
    sigma = math.fsum([q.background_conductivity_S_m, *(s.conductivity_S_m for s in rows)])
    field = q.electric_field_V_m
    return IonicWaterResult(status='computed', **base, conductivity_S_m=sigma,
        resistivity_ohm_m=1/sigma if sigma else None, species=rows,
        net_charge_concentration_C_m3=charge_mol*FARADAY_C_MOL,
        ionic_strength_mol_m3=.5*math.fsum(s.charge_number**2*c for s,c in zip(q.species, concentrations)),
        dissolved_mass_closure_kg_m3=math.fsum(s.mass_concentration_kg_m3 for s in rows)-q.dissolved_solids_kg_m3,
        current_density_A_m2=None if field is None else sigma*field,
        joule_heating_W_m3=None if field is None else sigma*field*field,
        issues=() if sigma else ('Zero supplied conductivity has no finite resistivity; infinity is retained as unknown, not serialized.',))
