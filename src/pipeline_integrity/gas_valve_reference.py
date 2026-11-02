"""Explicit gas reference-flow normalization and conventional valve coefficients."""
from __future__ import annotations

import math
from typing import Literal
from .contracts import Contract,Name,Nonnegative,Positive
from .models.thermal import Inventory,inventory
from .models.library_models import GasControlValve,gas_control_valve
from .thermo import CheckedProvider,CoolPropProvider


class GasReferenceFlowRequest(Contract):
    fluid: Name
    source_volume_flow_m3_s: Nonnegative | None = None
    source_pressure_pa_abs: Positive
    source_temperature_k: Positive
    target_pressure_pa_abs: Positive = 101325.
    target_temperature_k: Positive = 273.15
    same_composition_gas_reference_basis_reviewed: bool = False


class GasReferenceFlowResult(Contract):
    status: Literal["computed","needs_data"]
    fluid: Name
    target_pressure_pa_abs: Positive
    target_temperature_k: Positive
    target_volume_flow_m3_s: Nonnegative | None = None
    reference_volume_ratio: Positive | None = None
    issues: tuple[str,...] = ()


class _GasPhaseProvider:
    def __init__(self,provider):self.provider=CheckedProvider(provider)
    def resolve(self,request):
        state=self.provider.resolve(request)
        if state.phase not in ("gas","supercritical_gas","supercritical"):
            raise ValueError("gas reference conversion requires reviewed single gas states")
        if (not math.isclose(state.pressure_pa_abs,request.inputs["P"],rel_tol=1e-10,abs_tol=1e-5)
            or not math.isclose(state.temperature_k,request.inputs["T"],rel_tol=1e-10,abs_tol=1e-8)):
            raise ValueError("property provider returned a different reference state")
        return state


def normalize_gas_reference_flow(request: GasReferenceFlowRequest,*,property_provider=None) -> GasReferenceFlowResult:
    """Reuse the inventory density/reference-volume ratio for the same time basis.

    Both volume rates refer to the same reviewed gas composition. Real density
    references may be supplied through the existing property-provider protocol;
    an ideal reference convention must be deliberately supplied as such. No
    implicit standard temperature, gas composition or liquid-state substitution
    is inferred from an ambiguous 'standard' volume label.
    """
    if not isinstance(request,GasReferenceFlowRequest):raise TypeError("GasReferenceFlowRequest required")
    q=GasReferenceFlowRequest.model_validate(request.model_dump(mode="python"))
    base=dict(fluid=q.fluid,target_pressure_pa_abs=q.target_pressure_pa_abs,target_temperature_k=q.target_temperature_k)
    issues=[]
    if q.source_volume_flow_m3_s is None:issues.append("missing source reference volume rate")
    if not q.same_composition_gas_reference_basis_reviewed:issues.append("same-composition gas reference basis requires review")
    if issues:return GasReferenceFlowResult(status="needs_data",issues=tuple(issues),**base)
    provider=property_provider if property_provider is not None else CoolPropProvider()
    ratio=inventory(Inventory(fluid=q.fluid,pressure_pa_abs=q.source_pressure_pa_abs,temperature_k=q.source_temperature_k,
        internal_volume_m3=1.,reference_pressure_pa_abs=q.target_pressure_pa_abs,reference_temperature_k=q.target_temperature_k),
        property_provider=_GasPhaseProvider(provider)).values["reference_volume_m3"]
    flow=q.source_volume_flow_m3_s*ratio
    if not math.isfinite(flow):raise ValueError("reference volume rate exceeds finite support")
    return GasReferenceFlowResult(status="computed",reference_volume_ratio=ratio,target_volume_flow_m3_s=flow,**base)


class GasValveCapacityCoefficients(Contract):
    kv: Positive
    cv: Positive
    normal_reference_temperature_k: Literal[273.15] = 273.15
    normal_reference_pressure_pa_abs: Literal[101325.] = 101325.
    diagnostics: dict


def gas_valve_capacity_coefficients(request: GasControlValve) -> GasValveCapacityCoefficients:
    """Reuse one gas-sizing kernel and expose both conventional Kv and Cv scales."""
    from fluids.control_valve import convert_flow_coefficient
    if not isinstance(request,GasControlValve):raise TypeError("GasControlValve required")
    q=GasControlValve.model_validate(request.model_dump(mode="python"))
    values=gas_control_valve(q).values
    return GasValveCapacityCoefficients(kv=values["kv"],cv=convert_flow_coefficient(values["kv"],"Kv","Cv"),
        diagnostics=values["diagnostics"])
