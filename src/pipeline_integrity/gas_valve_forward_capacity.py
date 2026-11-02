"""Forward equal-bore turbulent gas capacity through the existing sizing kernel."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract,Name,Nonnegative,Positive
from .gas_valve_reference import gas_valve_capacity_coefficients
from .models.library_models import GasControlValve


class GasValveForwardCapacityRequest(Contract):
    coefficient_scale: Literal['Kv','Cv']
    sizing_reference_case: GasControlValve
    supplied_coefficient: Nonnegative | None = None
    normal_reference_density_kg_m3: Positive | None = None
    normal_density_basis_id: Name | None = None
    equal_bore_turbulent_and_reference_density_reviewed: bool = False
    coefficient_absolute_tolerance: Nonnegative = 1e-9
    coefficient_relative_tolerance: Nonnegative = 1e-8


class GasValveForwardCapacityResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','solver_withheld']
    normal_density_basis_id: Name | None
    normal_volume_flow_m3_s: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    kv: Nonnegative | None = None
    cv: Nonnegative | None = None
    diagnostics: dict | None = None
    issues: tuple[str,...] = ()
    normal_reference_temperature_k: Literal[273.15] = 273.15
    normal_reference_pressure_pa_abs: Literal[101325.] = 101325.
    manufacturer_or_device_qualification: Literal[False] = False


def evaluate_gas_valve_forward_capacity(request: GasValveForwardCapacityRequest) -> GasValveForwardCapacityResult:
    """Invert the existing linear turbulent equal-bore capacity, then replay it.

    The supplied sizing case provides a positive reference-flow coordinate with
    the desired state, valve factors and geometry. Only a known nonlaminar,
    equal-bore branch is inverted. The final sizing replay must recover the
    supplied conventional coefficient. Mass rate uses an explicitly supplied
    density matching the kernel's normal-volume convention; no EOS or ambiguous
    'standard' reference density is silently selected.
    """
    if not isinstance(request,GasValveForwardCapacityRequest):raise TypeError('GasValveForwardCapacityRequest required')
    q=GasValveForwardCapacityRequest.model_validate(request.model_dump(mode='python'))
    base=dict(normal_density_basis_id=q.normal_density_basis_id)
    fields=('supplied_coefficient','normal_reference_density_kg_m3','normal_density_basis_id')
    issues=['missing '+k for k in fields if getattr(q,k) is None]
    if not q.equal_bore_turbulent_and_reference_density_reviewed:
        issues.append('turbulent equal-bore branch and matching normal-density basis require review')
    if issues:return GasValveForwardCapacityResult(status='needs_data',issues=tuple(issues),**base)
    reference=q.sizing_reference_case
    if not all(math.isclose(d,reference.valve_diameter_m,rel_tol=1e-12,abs_tol=0.)
               for d in (reference.inlet_diameter_m,reference.outlet_diameter_m)):
        return GasValveForwardCapacityResult(status='out_of_scope',**base,issues=('forward inversion is limited to equal bores',))
    if q.supplied_coefficient==0:
        return GasValveForwardCapacityResult(status='computed',**base,normal_volume_flow_m3_s=0.,mass_flow_kg_s=0.,
            kv=0.,cv=0.,diagnostics={'zero_supplied_capacity':True,'laminar':None,'choked':None})
    capacity=gas_valve_capacity_coefficients(reference)
    if capacity.diagnostics.get('laminar') is not False:
        return GasValveForwardCapacityResult(status='out_of_scope',**base,issues=('sizing reference is not a known turbulent branch',))
    source_coefficient=capacity.kv if q.coefficient_scale=='Kv' else capacity.cv
    flow=reference.standard_volume_flow_m3_s*q.supplied_coefficient/source_coefficient
    if not math.isfinite(flow) or flow<=0:raise ValueError('forward normal flow exceeds positive finite support')
    replay=gas_valve_capacity_coefficients(reference.model_copy(update={'standard_volume_flow_m3_s':flow}))
    if replay.diagnostics.get('laminar') is not False:
        return GasValveForwardCapacityResult(status='out_of_scope',**base,issues=('predicted capacity leaves the declared turbulent branch',))
    target=replay.kv if q.coefficient_scale=='Kv' else replay.cv
    if abs(target-q.supplied_coefficient)>q.coefficient_absolute_tolerance+q.coefficient_relative_tolerance*q.supplied_coefficient:
        return GasValveForwardCapacityResult(status='solver_withheld',**base,issues=('sizing replay does not recover supplied coefficient',))
    mass=flow*q.normal_reference_density_kg_m3
    if not math.isfinite(mass) or mass<=0:raise ValueError('mass flow exceeds positive finite support')
    return GasValveForwardCapacityResult(status='computed',**base,normal_volume_flow_m3_s=flow,mass_flow_kg_s=mass,
        kv=replay.kv,cv=replay.cv,diagnostics=replay.diagnostics)
