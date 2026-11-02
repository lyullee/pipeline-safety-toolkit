"""Measured steady compressor control-volume work and external mechanical loss."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive


class ObservedEnergyPort(Contract):
    id: Name
    signed_outward_mass_flow_kg_s: float
    specific_enthalpy_J_kg: float | None
    composition_basis_id: Name
    energy_reference_id: Name


class CompressorExternalEnergyRequest(Contract):
    asset_id: Name
    clock_id: Name
    ports: tuple[ObservedEnergyPort,...] = Field(min_length=2)
    composition_basis_id: Name
    energy_reference_id: Name
    signed_heat_into_fluid_W: float | None
    external_mechanical_loss_W: Nonnegative | None
    absolute_mass_tolerance_kg_s: Positive = 1e-10
    relative_mass_tolerance: Nonnegative = Field(default=1e-10,le=1e-3)
    steady_no_reaction_storage_and_negligible_KE_PE_reviewed: bool = False

    @model_validator(mode='after')
    def identities(self):
        if len({p.id for p in self.ports})!=len(self.ports):raise ValueError('port IDs must be unique')
        if any((p.composition_basis_id,p.energy_reference_id)!=(self.composition_basis_id,self.energy_reference_id) for p in self.ports):
            raise ValueError('all measured enthalpies require the same composition and reference basis')
        return self


class CompressorExternalEnergyResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    asset_id: Name
    clock_id: Name
    net_outward_mass_residual_kg_s: float | None = None
    mass_tolerance_kg_s: Nonnegative | None = None
    net_outward_enthalpy_flow_W: float | None = None
    required_fluid_work_input_W: float | None = None
    required_external_shaft_input_W: float | None = None
    energy_budget_residual_W: float | None = None
    enthalpy_reference_shift_sensitivity_kg_s: float | None = None
    issues: tuple[str,...] = ()
    compression_efficiency_or_seal_leakage_inferred: Literal[False] = False


def observe_compressor_external_energy(q: CompressorExternalEnergyRequest) -> CompressorExternalEnergyResult:
    """Invert a complete measured energy boundary; negative input work is retained.

    Flow is positive outward at every explicitly identified port. Internal return
    streams are not extra external ports. Heat into the fluid is positive and an
    external mechanical loss adds to the required driver input. Mechanical heat
    deposited inside the chosen fluid boundary must not be entered again as a
    separate loss. Every active leakage port needs its actual exit enthalpy.
    """
    if not isinstance(q,CompressorExternalEnergyRequest):raise TypeError('CompressorExternalEnergyRequest required')
    q=CompressorExternalEnergyRequest.model_validate(q.model_dump(mode='python'))
    base=dict(asset_id=q.asset_id,clock_id=q.clock_id)
    if (not q.steady_no_reaction_storage_and_negligible_KE_PE_reviewed
        or q.signed_heat_into_fluid_W is None or q.external_mechanical_loss_W is None
        or any(p.signed_outward_mass_flow_kg_s!=0 and p.specific_enthalpy_J_kg is None for p in q.ports)):
        return CompressorExternalEnergyResult(status='needs_data',**base,issues=('complete reviewed measured ports, heat and external mechanical loss required',))
    incoming=math.fsum(-p.signed_outward_mass_flow_kg_s for p in q.ports if p.signed_outward_mass_flow_kg_s<0)
    outgoing=math.fsum(p.signed_outward_mass_flow_kg_s for p in q.ports if p.signed_outward_mass_flow_kg_s>0)
    residual=math.fsum(p.signed_outward_mass_flow_kg_s for p in q.ports)
    tolerance=q.absolute_mass_tolerance_kg_s+q.relative_mass_tolerance*max(incoming,outgoing)
    mass=dict(net_outward_mass_residual_kg_s=residual,mass_tolerance_kg_s=tolerance,
        enthalpy_reference_shift_sensitivity_kg_s=residual)
    if min(incoming,outgoing)<=0 or abs(residual)>tolerance:
        return CompressorExternalEnergyResult(status='out_of_scope',**base,**mass,
            issues=('positive entering/leaving flows and a closed external steady mass budget required',))
    enthalpy=math.fsum(p.signed_outward_mass_flow_kg_s*p.specific_enthalpy_J_kg
        for p in q.ports if p.signed_outward_mass_flow_kg_s!=0)
    fluid=math.fsum((enthalpy,-q.signed_heat_into_fluid_W))
    shaft=math.fsum((fluid,q.external_mechanical_loss_W))
    energy=math.fsum((shaft,-q.external_mechanical_loss_W,q.signed_heat_into_fluid_W,-enthalpy))
    if not all(math.isfinite(v) for v in (incoming,outgoing,residual,tolerance,enthalpy,fluid,shaft,energy)):
        raise ValueError('observed energy boundary exceeds finite numerical support')
    return CompressorExternalEnergyResult(status='computed',**base,**mass,
        net_outward_enthalpy_flow_W=enthalpy,required_fluid_work_input_W=fluid,
        required_external_shaft_input_W=shaft,energy_budget_residual_W=energy)
