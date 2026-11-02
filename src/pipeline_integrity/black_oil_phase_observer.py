"""Reference-component black-oil partition with supplied reviewed PVT closure.

The oil component is nonvolatile and the gas component may dissolve in it.
Formation factors refer to the named stock states, not to an implicit standard.
No material property curve is reconstructed from API gravity or gas/oil ratio.
"""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract, Name, Nonnegative, Positive
from .volume_weighting import normalize_component_volumes


class BlackOilPartitionRequest(Contract):
    pressure_Pa_abs: Positive | None
    temperature_K: Positive | None
    stock_oil_volume_m3: Nonnegative | None
    total_gas_reference_volume_m3: Nonnegative | None
    oil_reference_density_kg_m3: Positive | None
    gas_reference_density_kg_m3: Positive | None
    oil_reference_temperature_K: Positive | None
    oil_reference_pressure_Pa_abs: Positive | None
    gas_reference_temperature_K: Positive | None
    gas_reference_pressure_Pa_abs: Positive | None
    reference_basis_id: Name | None
    material_PVT_id: Name | None
    saturated_gas_ratio_ref_m3_per_stock_oil_m3: Nonnegative | None
    oil_formation_volume_factor: Positive | None
    oil_factor_at_dissolved_gas_ratio: Nonnegative | None
    gas_formation_volume_factor: Positive | None
    reviewed_nonvolatile_oil_single_gas_equilibrium_closure: bool = False
    formation_factors_and_saturation_at_requested_PT_and_reference_basis_reviewed: bool = False


class BlackOilPartitionResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    reference_basis_id: Name | None
    phase_state: Literal['undersaturated_liquid','bubble_boundary','oil_and_free_gas'] | None = None
    actual_dissolved_gas_ratio: Nonnegative | None = None
    dissolved_gas_reference_volume_m3: Nonnegative | None = None
    free_gas_reference_volume_m3: Nonnegative | None = None
    oil_phase_volume_m3: Nonnegative | None = None
    gas_phase_volume_m3: Nonnegative | None = None
    oil_phase_mass_kg: Nonnegative | None = None
    gas_phase_mass_kg: Nonnegative | None = None
    oil_phase_density_kg_m3: Positive | None = None
    gas_phase_density_kg_m3: Positive | None = None
    oil_volume_fraction: Nonnegative | None = None
    total_mass_kg: Nonnegative | None = None
    component_mass_residual_kg: float | None = None
    gas_reference_inventory_residual_m3: float | None = None
    issues: tuple[str, ...] = ()
    material_PVT_prediction_or_field_calibration_qualified: Literal[False] = False


def observe_black_oil_partition(q: BlackOilPartitionRequest) -> BlackOilPartitionResult:
    """Partition gas inventory subject to a supplied equilibrium solubility cap.

At positive stock-oil inventory, actual dissolved reference-gas ratio is the
lesser of total ratio and saturation capacity. Bo must be supplied for that
actual dissolved ratio at requested PT; using saturated Bo for undersaturated
oil is never silently accepted. Bg maps free gas from its explicitly named
reference state. Phase masses derive from conserved reference components.
This closed observation supplies no empirical solubility or swelling law.
"""
    q=BlackOilPartitionRequest.model_validate(q.model_dump(mode='python'))
    base={'reference_basis_id':q.reference_basis_id}
    names=('pressure_Pa_abs','temperature_K','stock_oil_volume_m3','total_gas_reference_volume_m3',
        'oil_reference_density_kg_m3','gas_reference_density_kg_m3','oil_reference_temperature_K',
        'oil_reference_pressure_Pa_abs','gas_reference_temperature_K','gas_reference_pressure_Pa_abs',
        'reference_basis_id','material_PVT_id','saturated_gas_ratio_ref_m3_per_stock_oil_m3',
        'oil_formation_volume_factor','oil_factor_at_dissolved_gas_ratio','gas_formation_volume_factor')
    if any(getattr(q,n) is None for n in names) or not q.reviewed_nonvolatile_oil_single_gas_equilibrium_closure or not q.formation_factors_and_saturation_at_requested_PT_and_reference_basis_reviewed:
        return BlackOilPartitionResult(status='needs_data',**base,issues=('Explicit component inventories, reference states and reviewed state-specific PVT closure required; API/GOR alone is insufficient.',))
    oil=q.stock_oil_volume_m3;gas=q.total_gas_reference_volume_m3
    if oil==0:
        return BlackOilPartitionResult(status='out_of_scope',**base,issues=('This oil-host partition requires positive stock-oil inventory; gas-only and empty domains are not inferred.',))
    total_ratio=gas/oil;sat=q.saturated_gas_ratio_ref_m3_per_stock_oil_m3
    rs=min(total_ratio,sat)
    if not math.isclose(q.oil_factor_at_dissolved_gas_ratio,rs,rel_tol=1e-12,abs_tol=1e-12):
        return BlackOilPartitionResult(status='needs_data',**base,actual_dissolved_gas_ratio=rs,issues=('Oil formation factor must describe the computed actual dissolved-gas ratio, not an unrelated saturated state.',))
    dissolved=gas if total_ratio<=sat else oil*sat
    free=gas-dissolved
    oil_volume=oil*q.oil_formation_volume_factor;gas_volume=free*q.gas_formation_volume_factor
    oil_component=oil*q.oil_reference_density_kg_m3;dissolved_mass=dissolved*q.gas_reference_density_kg_m3
    free_mass=free*q.gas_reference_density_kg_m3;oil_mass=math.fsum((oil_component,dissolved_mass))
    _,fractions=normalize_component_volumes({'oil_phase':oil_volume,'gas_phase':gas_volume})
    total_mass=math.fsum((oil_mass,free_mass));reference_mass=math.fsum((oil_component,gas*q.gas_reference_density_kg_m3))
    phase='oil_and_free_gas' if total_ratio>sat else ('bubble_boundary' if total_ratio==sat else 'undersaturated_liquid')
    return BlackOilPartitionResult(status='computed',**base,phase_state=phase,actual_dissolved_gas_ratio=rs,
        dissolved_gas_reference_volume_m3=dissolved,free_gas_reference_volume_m3=free,
        oil_phase_volume_m3=oil_volume,gas_phase_volume_m3=gas_volume,
        oil_phase_mass_kg=oil_mass,gas_phase_mass_kg=free_mass,
        oil_phase_density_kg_m3=oil_mass/oil_volume,gas_phase_density_kg_m3=q.gas_reference_density_kg_m3/q.gas_formation_volume_factor,
        oil_volume_fraction=fractions['oil_phase'],total_mass_kg=total_mass,
        component_mass_residual_kg=total_mass-reference_mass,
        gas_reference_inventory_residual_m3=math.fsum((dissolved,free))-gas)
