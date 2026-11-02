"""Explicit pseudo-critical approximations, not mixture critical-point/EOS solves.

Component critical properties and correlation applicability are caller data.
A gas-gravity estimate does not identify composition, viscosity, heating value,
phase or a uniquely defined real-gas equation of state.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Fraction


class CriticalMixtureComponent(Contract):
    id: Name
    mole_fraction: Fraction
    critical_temperature_K: Positive
    critical_pressure_Pa_abs: Positive


class PseudocriticalRequest(Contract):
    method: Literal['kay_mole_average','gravity_linear_estimate']
    components: tuple[CriticalMixtureComponent,...] = ()
    relative_gas_gravity_air: Positive | None = None
    reviewed_gas_gravity_support: tuple[Positive,Positive] | None = None
    carbon_dioxide_mole_fraction: Fraction | None = None
    hydrogen_sulfide_mole_fraction: Fraction | None = None
    approximation_and_component_basis_reviewed: bool = False
    sour_correction_applicability_reviewed: bool = False

    @model_validator(mode='after')
    def aligned(self):
        if self.components:
            if len({c.id for c in self.components})!=len(self.components):raise ValueError('component identifiers must be unique')
            if abs(math.fsum(c.mole_fraction for c in self.components)-1)>1e-12:raise ValueError('component mole fractions must sum to one')
        if self.method=='kay_mole_average' and self.relative_gas_gravity_air is not None:
            raise ValueError('composition averaging and gas-gravity estimate inputs must not be mixed')
        if self.method=='gravity_linear_estimate' and self.components:raise ValueError('choose one estimate basis')
        if self.reviewed_gas_gravity_support is not None and self.reviewed_gas_gravity_support[0]>=self.reviewed_gas_gravity_support[1]:
            raise ValueError('gas-gravity support must be ordered')
        a,b=self.carbon_dioxide_mole_fraction,self.hydrogen_sulfide_mole_fraction
        if (a is None)!=(b is None):raise ValueError('both sour component fractions are required together')
        if a is not None and a+b>1:raise ValueError('sour component fractions exceed total mixture')
        return self


class PseudocriticalResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    method: Literal['kay_mole_average','gravity_linear_estimate']
    uncorrected_pseudo_temperature_K: Positive | None = None
    uncorrected_pseudo_pressure_Pa_abs: Positive | None = None
    adjusted_pseudo_temperature_K: Positive | None = None
    adjusted_pseudo_pressure_Pa_abs: Positive | None = None
    sour_temperature_shift_K: float | None = None
    issues: tuple[str,...] = ()
    actual_mixture_critical_point_established: Literal[False] = False
    equation_of_state_or_phase_established: Literal[False] = False


def estimate_gas_pseudocritical(request: PseudocriticalRequest) -> PseudocriticalResult:
    """Evaluate a declared mixing estimate, optionally with sour-gas correction.

    Gas-gravity linear estimates use an explicitly dimensional engineering fit
    expressed here in SI after Rankine/psi conversion. Their numerical output
    does not extend the caller's reviewed composition/gravity/calibration domain.
    Kay averaging is a pseudo-property definition, not a rigorous phase-envelope
    critical-point rule. No undocumented default gas composition is constructed.
    """
    q=PseudocriticalRequest.model_validate(request.model_dump(mode='python'));issues=[]
    if not q.approximation_and_component_basis_reviewed:issues.append('pseudo-property approximation and component reference basis require review')
    if q.method=='kay_mole_average' and not q.components:issues.append('component mole fractions and critical properties required')
    if q.method=='gravity_linear_estimate' and (q.relative_gas_gravity_air is None or q.reviewed_gas_gravity_support is None):
        issues.append('gas gravity and reviewed empirical support required')
    if q.carbon_dioxide_mole_fraction is not None and not q.sour_correction_applicability_reviewed:
        issues.append('sour-gas empirical correction applicability requires review')
    if issues:return PseudocriticalResult(status='needs_data',method=q.method,issues=tuple(issues))
    if q.method=='kay_mole_average':
        temp=math.fsum(c.mole_fraction*c.critical_temperature_K for c in q.components)
        pressure=math.fsum(c.mole_fraction*c.critical_pressure_Pa_abs for c in q.components)
    else:
        gravity=q.relative_gas_gravity_air;lo,hi=q.reviewed_gas_gravity_support
        if not lo<=gravity<=hi:return PseudocriticalResult(status='out_of_scope',method=q.method,issues=('gas gravity outside reviewed fit support',))
        temp=(170.491+307.344*gravity)*(5/9)
        pressure=(709.604-58.718*gravity)*6894.757293168
    shift=0.
    if q.carbon_dioxide_mole_fraction is not None:
        b=q.hydrogen_sulfide_mole_fraction;a=q.carbon_dioxide_mole_fraction+b
        shift=(120*(a**.9-a**1.6)+15*(math.sqrt(b)-b**4))*(5/9)
        corrected_temp=temp-shift;denominator=temp+b*(1-b)*shift
        corrected_pressure=pressure*corrected_temp/denominator
    else:corrected_temp=temp;corrected_pressure=pressure
    if not all(math.isfinite(x) and x>0 for x in (temp,pressure,corrected_temp,corrected_pressure)):
        return PseudocriticalResult(status='out_of_scope',method=q.method,issues=('pseudo-property estimate leaves finite positive domain',))
    return PseudocriticalResult(status='computed',method=q.method,uncorrected_pseudo_temperature_K=temp,
        uncorrected_pseudo_pressure_Pa_abs=pressure,adjusted_pseudo_temperature_K=corrected_temp,
        adjusted_pseudo_pressure_Pa_abs=corrected_pressure,sour_temperature_shift_K=shift)
