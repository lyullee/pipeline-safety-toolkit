"""Explicit scalar screening coordinates with caller-supplied physical bases."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract,Name,Nonnegative,Positive
from .wind_screening import WindLoadInput,evaluate_wind_load


class KineticPressureScaleRequest(Contract):
    mass_density_kg_m3: Positive | None = None
    speed_change_magnitude_m_s: Nonnegative | None = None
    uniform_density_kinetic_scale_reviewed: bool = False


class KineticPressureScaleResult(Contract):
    status: Literal['computed','needs_data']
    kinetic_pressure_scale_pa: Nonnegative | None = None
    issues: tuple[str,...] = ()
    transient_pressure_history_inferred: Literal[False] = False


def evaluate_kinetic_pressure_scale(request: KineticPressureScaleRequest) -> KineticPressureScaleResult:
    """Evaluate the unsigned kinetic-energy pressure scale, not a valve history."""
    if not isinstance(request,KineticPressureScaleRequest):raise TypeError('KineticPressureScaleRequest required')
    q=KineticPressureScaleRequest.model_validate(request.model_dump(mode='python'))
    issues=[]
    if q.mass_density_kg_m3 is None:issues.append('missing mass density')
    if q.speed_change_magnitude_m_s is None:issues.append('missing velocity-change magnitude')
    if not q.uniform_density_kinetic_scale_reviewed:issues.append('kinetic pressure-scale basis requires review')
    if issues:return KineticPressureScaleResult(status='needs_data',issues=tuple(issues))
    # Reuse only the universal kinetic term already used by wind loading.
    # Zero drag/area select no aerodynamic force or empirical shape relation.
    result=evaluate_wind_load(WindLoadInput(air_density_kg_m3=q.mass_density_kg_m3,
        wind_speed_m_s=q.speed_change_magnitude_m_s,drag_coefficient=0.,projected_area_m2=0.))
    return KineticPressureScaleResult(status='computed',kinetic_pressure_scale_pa=result.dynamic_pressure_Pa)


class MassDampingIndexRequest(Contract):
    span_mass_kg_m: Nonnegative | None = None
    supplied_air_damping_parameter: Nonnegative | None = None
    damping_parameter_definition: Name | None = None
    surrounding_density_kg_m3: Positive | None = None
    outside_diameter_m: Positive | None = None
    supplied_comparison_threshold: Nonnegative | None = None
    parameter_and_mass_basis_reviewed: bool = False


class MassDampingIndexResult(Contract):
    status: Literal['computed','needs_data']
    damping_parameter_definition: Name | None
    mass_damping_index: Nonnegative | None = None
    above_supplied_threshold: bool | None = None
    issues: tuple[str,...] = ()
    vibration_amplitude_or_stability_qualified: Literal[False] = False


def evaluate_mass_damping_index(request: MassDampingIndexRequest) -> MassDampingIndexResult:
    """Return a declared mass/damping index without choosing damping conventions.

    The supplied parameter is used directly. No damping-ratio/log-decrement
    conversion, added mass, vibration amplitude or empirical threshold is chosen.
    A caller threshold, when present, produces only a strict scalar comparison.
    """
    if not isinstance(request,MassDampingIndexRequest):raise TypeError('MassDampingIndexRequest required')
    q=MassDampingIndexRequest.model_validate(request.model_dump(mode='python'))
    names=('span_mass_kg_m','supplied_air_damping_parameter','damping_parameter_definition',
           'surrounding_density_kg_m3','outside_diameter_m')
    issues=['missing '+k for k in names if getattr(q,k) is None]
    if not q.parameter_and_mass_basis_reviewed:issues.append('mass and damping-parameter basis requires review')
    if issues:return MassDampingIndexResult(status='needs_data',damping_parameter_definition=q.damping_parameter_definition,issues=tuple(issues))
    displaced_mass_scale=q.surrounding_density_kg_m3*q.outside_diameter_m*q.outside_diameter_m
    if not math.isfinite(displaced_mass_scale) or displaced_mass_scale<=0:
        raise ValueError('surrounding mass scale is outside positive finite support')
    index=2*q.span_mass_kg_m/displaced_mass_scale*q.supplied_air_damping_parameter
    if not math.isfinite(index):raise ValueError('mass-damping index exceeds finite support')
    above=None if q.supplied_comparison_threshold is None else index>q.supplied_comparison_threshold
    return MassDampingIndexResult(status='computed',damping_parameter_definition=q.damping_parameter_definition,
        mass_damping_index=index,above_supplied_threshold=above)


class EquivalentMassScaledDistanceRequest(Contract):
    input_basis: Literal['supplied_equivalent_mass','available_energy']
    distance_m: Nonnegative | None = None
    equivalent_mass_kg: Nonnegative | None = None
    available_energy_j: Nonnegative | None = None
    reference_specific_energy_j_kg: Positive | None = None
    reference_energy_basis_id: Name | None = None
    equivalence_and_unit_basis_reviewed: bool = False


class EquivalentMassScaledDistanceResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    reference_energy_basis_id: Name | None
    equivalent_mass_kg: Nonnegative | None = None
    scaled_distance_m_kg_neg_third: Nonnegative | None = None
    issues: tuple[str,...] = ()
    blast_damage_or_overpressure_inferred: Literal[False] = False


def evaluate_equivalent_mass_scaled_distance(request: EquivalentMassScaledDistanceRequest) -> EquivalentMassScaledDistanceResult:
    """Convert a declared reference-energy equivalent to a cube-root coordinate.

    The coordinate has units m kg^(-1/3). Available energy must already have
    the caller's reviewed mechanical/equivalence meaning. No explosive specific
    energy, conversion efficiency, damage table or blast model is selected.
    """
    if not isinstance(request,EquivalentMassScaledDistanceRequest):raise TypeError('EquivalentMassScaledDistanceRequest required')
    q=EquivalentMassScaledDistanceRequest.model_validate(request.model_dump(mode='python'))
    needed=['distance_m','reference_energy_basis_id']
    needed+=['equivalent_mass_kg'] if q.input_basis=='supplied_equivalent_mass' else ['available_energy_j','reference_specific_energy_j_kg']
    issues=['missing '+k for k in needed if getattr(q,k) is None]
    if not q.equivalence_and_unit_basis_reviewed:issues.append('energy equivalence and unit basis require review')
    base=dict(reference_energy_basis_id=q.reference_energy_basis_id)
    if issues:return EquivalentMassScaledDistanceResult(status='needs_data',issues=tuple(issues),**base)
    mass=q.equivalent_mass_kg if q.input_basis=='supplied_equivalent_mass' else q.available_energy_j/q.reference_specific_energy_j_kg
    if not math.isfinite(mass):raise ValueError('equivalent mass exceeds finite support')
    if q.input_basis=='available_energy' and q.available_energy_j>0 and mass==0:
        raise ValueError('positive energy-to-mass conversion is below numerical support')
    if mass==0:
        return EquivalentMassScaledDistanceResult(status='out_of_scope',equivalent_mass_kg=0.,**base,
            issues=('zero equivalent mass has no finite cube-root scaled-distance coordinate',))
    scaled=q.distance_m/math.cbrt(mass)
    if not math.isfinite(scaled):raise ValueError('scaled distance exceeds finite support')
    return EquivalentMassScaledDistanceResult(status='computed',equivalent_mass_kg=mass,
        scaled_distance_m_kg_neg_third=scaled,**base)
