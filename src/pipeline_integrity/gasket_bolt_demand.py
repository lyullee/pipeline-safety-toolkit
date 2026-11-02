"""Two local bolt-demand components from supplied effective gasket data."""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Nonnegative, Positive


class GasketBoltDemandRequest(Contract):
    pressure_difference_pa: Nonnegative | None = None
    reaction_diameter_m: Positive | None = None
    effective_width_m: Positive | None = None
    operating_gasket_factor: Nonnegative | None = None
    seating_stress_pa: Nonnegative | None = None
    effective_geometry_and_gasket_data_reviewed: bool = False


class GasketBoltDemandResult(Contract):
    status: Literal['computed','needs_data']
    pressure_end_force_n: Nonnegative | None = None
    operating_seal_force_n: Nonnegative | None = None
    operating_demand_n: Nonnegative | None = None
    seating_demand_n: Nonnegative | None = None
    governing_demand_n: Nonnegative | None = None
    governing_case: Literal['operating','seating','equal'] | None = None
    issues: tuple[str,...] = ()


def evaluate_gasket_bolt_demand(request: GasketBoltDemandRequest) -> GasketBoltDemandResult:
    """Return operating and assembly-seating demand for one supplied joint.

    Pressure is the nonnegative internal-minus-external pressure difference.
    Effective reaction diameter/width and both gasket parameters must already
    have a reviewed interpretation. The operating seal band uses twice the
    supplied width, while the seating band uses the supplied width once.
    This primitive selects neither gasket data nor geometry rules, bolt area,
    torque, actual retained preload, flange rating or standards acceptance.
    """
    if not isinstance(request,GasketBoltDemandRequest):
        raise TypeError('GasketBoltDemandRequest required')
    q=GasketBoltDemandRequest.model_validate(request.model_dump(mode='python'))
    needed=('pressure_difference_pa','reaction_diameter_m','effective_width_m',
            'operating_gasket_factor','seating_stress_pa')
    issues=['missing '+name for name in needed if getattr(q,name) is None]
    if not q.effective_geometry_and_gasket_data_reviewed:
        issues.append('effective geometry and supplied gasket parameters require review')
    if issues:return GasketBoltDemandResult(status='needs_data',issues=tuple(issues))
    diameter=q.reaction_diameter_m;width=q.effective_width_m;p=q.pressure_difference_pa
    pressure_force=math.pi/4*diameter*diameter*p
    seal_force=2*math.pi*diameter*width*q.operating_gasket_factor*p
    operating=math.fsum((pressure_force,seal_force))
    seating=math.pi*diameter*width*q.seating_stress_pa
    if not all(math.isfinite(v) for v in (pressure_force,seal_force,operating,seating)):
        raise ValueError('joint demand exceeds finite numerical support')
    controlling='equal' if operating==seating else ('operating' if operating>seating else 'seating')
    return GasketBoltDemandResult(status='computed',pressure_end_force_n=pressure_force,
        operating_seal_force_n=seal_force,operating_demand_n=operating,seating_demand_n=seating,
        governing_demand_n=max(operating,seating),governing_case=controlling)
