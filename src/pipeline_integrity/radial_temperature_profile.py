"""Fixed-surface radial temperature outputs through the existing layer engine."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field,model_validator

from .adapters import MaterialClient
from .contracts import Contract,Name,Nonnegative,Positive,RunContext,digest
from .material_exchange import evaluate_material_job,prepare_material_job


class RadialTemperatureProfileRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    time_s: Nonnegative
    evaluation_radii_m: tuple[Positive,...] = Field(min_length=2,max_length=1000)
    conductivity_w_m_k: Positive | None = None
    length_m: Positive
    inner_surface_temperature_k: Positive | None = None
    outer_surface_temperature_k: Positive | None = None
    geometry_and_thermal_evidence_id: Name
    steady_homogeneous_radial_conduction_reviewed: bool = False
    initial_virtual_boundary_transfer_w_m2_k: Positive = 1000.
    maximum_virtual_boundary_transfer_w_m2_k: Positive = 1e12
    surface_temperature_tolerance_k: Positive = 1e-6
    heat_balance_absolute_tolerance_w: Nonnegative = 1e-5
    heat_balance_relative_tolerance: float = Field(default=1e-6,ge=0,le=1e-2)

    @model_validator(mode='after')
    def ordered(self):
        if any(b<=a for a,b in zip(self.evaluation_radii_m,self.evaluation_radii_m[1:])):
            raise ValueError('evaluation radii must strictly increase across the whole wall')
        if self.maximum_virtual_boundary_transfer_w_m2_k<self.initial_virtual_boundary_transfer_w_m2_k:
            raise ValueError('virtual boundary transfer bracket must be ordered')
        return self


class RadialTemperatureProfileResult(Contract):
    status: Literal['computed','needs_data','solver_withheld']
    request_digest: str
    context_digest: str
    evaluation_radii_m: tuple[Positive,...]
    time_s: Nonnegative
    temperatures_k: tuple[Positive,...] | None = None
    heat_rate_outward_w: float | None = None
    maximum_surface_error_k: Nonnegative | None = None
    virtual_boundary_transfer_w_m2_k: Positive | None = None
    native_material_receipt: dict | None = None
    issues: tuple[str,...] = ()
    physical_boundary_films_inferred: Literal[False] = False


def evaluate_radial_temperature_profile(request: RadialTemperatureProfileRequest,
                                         context: RunContext,*,client=None) -> RadialTemperatureProfileResult:
    """Expose arbitrary radial samples as equal-material layer interfaces.

    Two supplied wall-surface temperatures are mapped to virtual finite-film
    bulk temperatures using the layer engine's own resistance outputs. These
    auxiliary films are only a boundary input conversion. No physical film
    coefficient, transient, thermal stress or new conduction law is inferred.
    """
    if not isinstance(request,RadialTemperatureProfileRequest):
        raise TypeError('RadialTemperatureProfileRequest required')
    q=RadialTemperatureProfileRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('profile and context asset/clock differ')
    if q.geometry_and_thermal_evidence_id not in {e.id for e in c.evidence}:
        raise ValueError('profile geometry/thermal evidence is absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),evaluation_radii_m=q.evaluation_radii_m,time_s=q.time_s)
    required=('conductivity_w_m_k','inner_surface_temperature_k','outer_surface_temperature_k')
    issues=['missing '+k for k in required if getattr(q,k) is None]
    if not q.steady_homogeneous_radial_conduction_reviewed:
        issues.append('steady homogeneous radial conduction requires review')
    if issues:return RadialTemperatureProfileResult(status='needs_data',issues=tuple(issues),**base)
    client=MaterialClient() if client is None else client
    def run(inner,outer,transfer):
        parameters=dict(radii_m=list(q.evaluation_radii_m),conductivity_W_m_K=[q.conductivity_w_m_k]*(len(q.evaluation_radii_m)-1),
            length_m=q.length_m,inner_fluid_temperature_K=inner,ambient_temperature_K=outer,
            radiative_environment_temperature_K=outer,inner_convection_W_m2_K=transfer,
            outer_convection_W_m2_K=transfer,outer_emissivity=0.)
        job=prepare_material_job('thermal.cylindrical_layers',parameters,c,location_id=q.location_id,
            evidence_ids=(q.geometry_and_thermal_evidence_id,),client=client)
        return evaluate_material_job(job,c,client=client)
    inner=q.inner_surface_temperature_k;outer=q.outer_surface_temperature_k
    transfer=q.initial_virtual_boundary_transfer_w_m2_k
    seed=run(inner,outer,transfer)
    if seed.native.status!='computed':
        return RadialTemperatureProfileResult(status='solver_withheld',**base,issues=('native layer engine withheld resistance output',))
    resistances=seed.native.payload['values']['conduction_resistances_K_W']
    if len(resistances)!=len(q.evaluation_radii_m)-1 or not all(math.isfinite(r) and r>0 for r in resistances):
        raise ValueError('native radial resistances do not match positive declared layers')
    target_heat=(inner-outer)/math.fsum(resistances)
    area_inner=2*math.pi*q.evaluation_radii_m[0]*q.length_m
    area_outer=2*math.pi*q.evaluation_radii_m[-1]*q.length_m
    while True:
        virtual_inner=inner+target_heat/(transfer*area_inner)
        virtual_outer=outer-target_heat/(transfer*area_outer)
        if all(math.isfinite(t) and t>0 for t in (virtual_inner,virtual_outer)):break
        if transfer>=q.maximum_virtual_boundary_transfer_w_m2_k:
            return RadialTemperatureProfileResult(status='solver_withheld',**base,
                issues=('fixed-surface input conversion requires unsupported virtual absolute temperatures',))
        transfer=min(10*transfer,q.maximum_virtual_boundary_transfer_w_m2_k)
    receipt=run(virtual_inner,virtual_outer,transfer)
    if receipt.native.status!='computed':
        return RadialTemperatureProfileResult(status='solver_withheld',**base,issues=('native layer engine withheld profile output',))
    values=receipt.native.payload['values'];temperatures=tuple(values['interface_temperatures_K'])
    if len(temperatures)!=len(q.evaluation_radii_m) or not all(math.isfinite(t) and t>0 for t in temperatures):
        raise ValueError('native temperature outputs do not match requested interfaces')
    error=max(abs(temperatures[0]-inner),abs(temperatures[-1]-outer))
    heat=values['heat_rate_outward_W'];energy_limit=q.heat_balance_absolute_tolerance_w+q.heat_balance_relative_tolerance*abs(target_heat)
    layer_heat=[(a-b)/r for a,b,r in zip(temperatures,temperatures[1:],resistances)]
    if (error>q.surface_temperature_tolerance_k or abs(heat-target_heat)>energy_limit
        or abs(values['outer_balance_residual_W'])>energy_limit or any(abs(v-heat)>energy_limit for v in layer_heat)):
        return RadialTemperatureProfileResult(status='solver_withheld',**base,
            native_material_receipt=receipt.model_dump(mode='json'),
            issues=('fixed-surface or radial energy residual exceeds supplied numerical tolerance',))
    return RadialTemperatureProfileResult(status='computed',**base,temperatures_k=temperatures,
        heat_rate_outward_w=heat,maximum_surface_error_k=error,virtual_boundary_transfer_w_m2_k=transfer,
        native_material_receipt=receipt.model_dump(mode='json'))
