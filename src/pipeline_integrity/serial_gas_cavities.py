"""Prescribed-area serial gas restrictions with a shared steady mass flow."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from scipy.optimize import least_squares
from scipy.special import softmax
from .contracts import Contract,Name,Positive,Nonnegative
from .models.library_models import ReliefGas,relief_gas


class GasCavityRestriction(Contract):
    id: Name
    actual_flow_area_m2: Positive
    supplied_discharge_coefficient: Positive = Field(le=1)


class SerialGasCavitiesRequest(Contract):
    upstream_pressure_Pa_abs: Positive
    downstream_pressure_Pa_abs: Positive
    common_stagnation_temperature_K: Positive
    molar_mass_kg_mol: Positive
    heat_capacity_ratio: float = Field(gt=1)
    restrictions: tuple[GasCavityRestriction,...] = Field(min_length=1,max_length=64)
    property_and_coefficient_basis_id: Name
    ideal_gas_constant_cp_adiabatic_rest_cavities_and_no_bypass_reviewed: bool = False
    coefficient_geometry_correlation_domain_reviewed: bool = False
    relative_mass_tolerance: Positive = Field(default=1e-8,le=1e-2)
    absolute_mass_tolerance_kg_s: Nonnegative = 1e-12
    maximum_function_evaluations: int = Field(default=200,gt=0,le=10000)

    @model_validator(mode='after')
    def direction(self):
        if self.upstream_pressure_Pa_abs<=self.downstream_pressure_Pa_abs:
            raise ValueError('strictly decreasing absolute boundary pressures required')
        if len({r.id for r in self.restrictions})!=len(self.restrictions):
            raise ValueError('restriction IDs must be unique')
        return self


class SerialGasCavitiesResult(Contract):
    status: Literal['computed','needs_data','not_converged']
    property_and_coefficient_basis_id: Name
    common_mass_flow_kg_s: Positive | None = None
    cavity_pressure_Pa_abs: tuple[Positive,...] = ()
    restriction_mass_flow_kg_s: tuple[Positive,...] = ()
    restriction_is_choked: tuple[bool,...] = ()
    maximum_mass_mismatch_kg_s: Nonnegative | None = None
    function_evaluations: int | None = None
    issues: tuple[str,...] = ()
    tooth_coefficients_or_compressor_performance_inferred: Literal[False] = False


def solve_serial_gas_cavities(q: SerialGasCavitiesRequest) -> SerialGasCavitiesResult:
    """Reuse the existing nozzle area law and solve the serial pressure coupling.

    Each resting cavity recovers the nozzle jet's kinetic energy adiabatically,
    giving the same ideal-gas stagnation temperature. Cavity pressure decreases;
    the gas is not forced to retain a common static nozzle temperature. Actual
    tooth coefficients, kinetic carry-over, clearance changes and bypass flow
    remain independent closure data. This is a declared nozzle-network alternative
    to historical adjacent-tooth expressions, not a copied labyrinth correlation.
    """
    if not isinstance(q,SerialGasCavitiesRequest):raise TypeError('SerialGasCavitiesRequest required')
    q=SerialGasCavitiesRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_and_coefficient_basis_id=q.property_and_coefficient_basis_id)
    if not (q.ideal_gas_constant_cp_adiabatic_rest_cavities_and_no_bypass_reviewed
        and q.coefficient_geometry_correlation_domain_reviewed):
        return SerialGasCavitiesResult(status='needs_data',**base,issues=('gas/cavity thermodynamics and actual restriction coefficient domain require review',))
    n=len(q.restrictions);pin=q.upstream_pressure_Pa_abs;pout=q.downstream_pressure_Pa_abs
    def capacities(pressure):
        values=[]
        for r,up,down in zip(q.restrictions,pressure[:-1],pressure[1:]):
            # The existing area law is linear in prescribed mass. Unit mass is
            # an explicit normalization used to invert that verified operator.
            area=relief_gas(ReliefGas(mass_flow_kg_s=1.,temperature_k=q.common_stagnation_temperature_K,
                compressibility=1.,molar_mass_kg_mol=q.molar_mass_kg_mol,heat_capacity_ratio=q.heat_capacity_ratio,
                relieving_pressure_pa_abs=up,back_pressure_pa_abs=down,discharge_coefficient=r.supplied_discharge_coefficient,
                back_pressure_factor=1.,combination_factor=1.)).values['required_area_m2']
            value=r.actual_flow_area_m2/area
            if not math.isfinite(value) or value<=0:raise ValueError('positive restriction capacity exceeds finite support')
            values.append(value)
        return values
    def pressure_from_logits(x):
        drops=softmax([*x,0.])*(pin-pout)
        pressure=[pin]
        for drop in drops[:-1]:pressure.append(pressure[-1]-float(drop))
        pressure.append(pout)
        if any(a<=b for a,b in zip(pressure,pressure[1:])):
            raise ValueError('cavity pressure drop is below finite numerical support')
        return pressure
    evaluations=0
    def residual(x):
        nonlocal evaluations
        evaluations+=1
        values=capacities(pressure_from_logits(x))
        return [math.log(value/values[-1]) for value in values[:-1]]
    if n==1:
        pressure=[pin,pout];values=capacities(pressure);success=True;evaluations=1
    else:
        fit=least_squares(residual,[0.]*(n-1),bounds=(-25.,25.),xtol=1e-12,ftol=1e-12,gtol=1e-12,
            max_nfev=q.maximum_function_evaluations)
        pressure=pressure_from_logits(fit.x);values=capacities(pressure);success=fit.success
    common=math.fsum(values)/n;mismatch=max(abs(v-common) for v in values)
    tolerance=q.absolute_mass_tolerance_kg_s+q.relative_mass_tolerance*common
    critical=(2/(q.heat_capacity_ratio+1))**(q.heat_capacity_ratio/(q.heat_capacity_ratio-1))
    choked=tuple(down/up<=critical for up,down in zip(pressure,pressure[1:]))
    return SerialGasCavitiesResult(status='computed' if success and mismatch<=tolerance else 'not_converged',**base,
        common_mass_flow_kg_s=common,cavity_pressure_Pa_abs=tuple(pressure),restriction_mass_flow_kg_s=tuple(values),
        restriction_is_choked=choked,maximum_mass_mismatch_kg_s=mismatch,function_evaluations=evaluations,
        issues=() if success and mismatch<=tolerance else ('serial common-mass pressure solve did not meet declared residual/support budget',))
