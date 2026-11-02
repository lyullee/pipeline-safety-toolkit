"""Signed mass residual for one declared control volume and observation window.

Port inputs are already integrated masses over the same clock window. This
primitive does not interpolate flow samples, estimate inventory or issue alarms.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative


class SectionMassBalanceRequest(Contract):
    clock_id: Name
    window_start_s: float
    window_finish_s: float
    inlet_masses_kg: dict[Name, Nonnegative | None]
    outlet_masses_kg: dict[Name, Nonnegative | None]
    initial_inventory_mass_kg: Nonnegative | None
    final_inventory_mass_kg: Nonnegative | None
    common_window_mass_basis_and_boundary_reviewed: bool = False

    @model_validator(mode="after")
    def observation_interval(self):
        if self.window_finish_s <= self.window_start_s:
            raise ValueError("a positive observation window is required")
        if not math.isfinite(self.window_finish_s - self.window_start_s):
            raise ValueError("observation window exceeds finite numerical support")
        return self


class SectionMassBalanceResult(Contract):
    status: Literal["computed", "needs_data"]
    clock_id: Name
    window_start_s: float
    window_finish_s: float
    elapsed_time_s: float
    inlet_mass_kg: Nonnegative | None = None
    outlet_mass_kg: Nonnegative | None = None
    inventory_change_kg: float | None = None
    mass_residual_kg: float | None = None
    mean_mass_residual_rate_kg_s: float | None = None
    issues: tuple[str, ...] = ()
    leak_presence_inferred: Literal[False] = False
    uncertainty_computed: Literal[False] = False


def evaluate_section_mass_balance(request: SectionMassBalanceRequest) -> SectionMassBalanceResult:
    """Return entering minus leaving minus stored mass change, preserving sign.

    Inlet/outlet masses are nonnegative directional totals. A reversing physical
    port may contribute to both directional dictionaries. Empty dictionaries
    explicitly represent no ports in that direction, while None is unresolved.
    Initial/final inventory belongs to the start/finish boundary respectively.
    """
    if not isinstance(request, SectionMassBalanceRequest):
        raise TypeError("SectionMassBalanceRequest required")
    q = SectionMassBalanceRequest.model_validate(request.model_dump(mode="python"))
    elapsed = q.window_finish_s - q.window_start_s
    base = dict(clock_id=q.clock_id, window_start_s=q.window_start_s,
                window_finish_s=q.window_finish_s, elapsed_time_s=elapsed)
    issues = []
    if not q.common_window_mass_basis_and_boundary_reviewed:
        issues.append("common observation window, mass basis and control-volume boundary require review")
    for label, ports in (("inlet", q.inlet_masses_kg), ("outlet", q.outlet_masses_kg)):
        issues.extend(f"missing {label} mass: {port}" for port, value in ports.items() if value is None)
    for name in ("initial_inventory_mass_kg", "final_inventory_mass_kg"):
        if getattr(q, name) is None:
            issues.append("missing " + name)
    if issues:
        return SectionMassBalanceResult(status="needs_data", issues=tuple(issues), **base)
    try:
        incoming = math.fsum(q.inlet_masses_kg.values())
        outgoing = math.fsum(q.outlet_masses_kg.values())
        storage_change = q.final_inventory_mass_kg - q.initial_inventory_mass_kg
        residual = math.fsum((incoming, -outgoing, -storage_change))
        rate = residual / elapsed
    except OverflowError as exc:
        raise ValueError("mass ledger exceeds finite numerical support") from exc
    if not all(math.isfinite(x) for x in (incoming, outgoing, storage_change, residual, rate)):
        raise ValueError("mass ledger exceeds finite numerical support")
    return SectionMassBalanceResult(status="computed", inlet_mass_kg=incoming,
        outlet_mass_kg=outgoing, inventory_change_kg=storage_change,
        mass_residual_kg=residual, mean_mass_residual_rate_kg_s=rate, **base)
