"""Caller-supplied first-order liquid density tangents about one reference.

Reference isothermal bulk modulus and thermal density slope specify a local
constitutive approximation. They do not determine adiabatic acoustic modulus,
phase equilibrium, temperature-dependent coefficients or material validity.
"""
from __future__ import annotations
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive


class LinearizedLiquidDensityLaw(Contract):
    fluid_id: Name
    property_basis_id: Name
    reference_density_kg_m3: Positive
    reference_pressure_pa_abs: Positive
    reference_temperature_K: Positive
    reference_isothermal_bulk_modulus_pa: Positive
    reference_volumetric_thermal_expansion_K_inv: float
    pressure_support_pa_abs: tuple[Positive,Positive]
    temperature_support_K: tuple[Positive,Positive]
    maximum_absolute_fractional_density_perturbation: Positive = Field(le=.1)
    single_liquid_linearized_property_support_reviewed: bool = False

    @model_validator(mode='after')
    def bounded_reference(self):
        for bounds,reference in ((self.pressure_support_pa_abs,self.reference_pressure_pa_abs),
                                 (self.temperature_support_K,self.reference_temperature_K)):
            if bounds[0]>=bounds[1] or not bounds[0]<=reference<=bounds[1]:raise ValueError('ordered property support must contain its reference')
        return self


class LinearizedLiquidDensityResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_pa_abs: Positive
    temperature_K: Positive
    density_kg_m3: Positive | None = None
    density_pressure_derivative_kg_m3_pa: Positive | None = None
    density_temperature_derivative_kg_m3_K: float | None = None
    current_isothermal_compressibility_pa_inv: Positive | None = None
    current_volumetric_expansion_K_inv: float | None = None
    pressure_fractional_density_perturbation: float | None = None
    thermal_fractional_density_perturbation: float | None = None
    adiabatic_acoustic_modulus_inferred: bool = False
    issues: tuple[str,...] = ()


def evaluate_linearized_liquid_density(law:LinearizedLiquidDensityLaw,*,pressure_pa_abs:float,temperature_K:float):
    """Evaluate rho/rho_ref=1+DeltaP/K_Tref-alpha_ref*DeltaT.

    Pressure and temperature perturbations are bounded separately, so large
    unreviewed terms cannot cancel into a deceptively small density change.
    Current normalized derivatives use current rho, not reference rho.
    """
    q=LinearizedLiquidDensityLaw.model_validate(law.model_dump(mode='python'))
    common=dict(pressure_pa_abs=pressure_pa_abs,temperature_K=temperature_K)
    # Validate absolute coordinates even when review/data are missing.
    LinearizedLiquidDensityResult(status='needs_data',**common)
    if not q.single_liquid_linearized_property_support_reviewed:
        return LinearizedLiquidDensityResult(status='needs_data',issues=('single-liquid local constitutive support requires review',),**common)
    if not q.pressure_support_pa_abs[0]<=pressure_pa_abs<=q.pressure_support_pa_abs[1] or not q.temperature_support_K[0]<=temperature_K<=q.temperature_support_K[1]:
        return LinearizedLiquidDensityResult(status='out_of_scope',issues=('state outside supplied property support',),**common)
    p=(pressure_pa_abs-q.reference_pressure_pa_abs)/q.reference_isothermal_bulk_modulus_pa
    t=-q.reference_volumetric_thermal_expansion_K_inv*(temperature_K-q.reference_temperature_K)
    if max(abs(p),abs(t),abs(p+t))>q.maximum_absolute_fractional_density_perturbation:
        return LinearizedLiquidDensityResult(status='out_of_scope',issues=('individual or combined linearized density perturbation exceeds reviewed support',),**common)
    rho=q.reference_density_kg_m3*(1+p+t)
    dp=q.reference_density_kg_m3/q.reference_isothermal_bulk_modulus_pa
    dt=-q.reference_density_kg_m3*q.reference_volumetric_thermal_expansion_K_inv
    return LinearizedLiquidDensityResult(status='computed',density_kg_m3=rho,
        density_pressure_derivative_kg_m3_pa=dp,density_temperature_derivative_kg_m3_K=dt,
        current_isothermal_compressibility_pa_inv=dp/rho,current_volumetric_expansion_K_inv=-dt/rho,
        pressure_fractional_density_perturbation=p,thermal_fractional_density_perturbation=t,**common)


class LinearizedLiquidCompatibilityProvider:
    """Adapt to the existing generic quasistatic mass-compatibility protocol.

    Its historical argument name water_temperature_K is a temperature coordinate;
    returned fluid_id/property_basis_id always remain the explicit supplied fluid.
    """
    def __init__(self,law:LinearizedLiquidDensityLaw):
        self.law=LinearizedLiquidDensityLaw.model_validate(law.model_dump(mode='python'))

    def resolve_density(self,*,pressure_Pa_abs,water_temperature_K):
        from .closed_liquid_compatibility import TestLiquidState
        state=evaluate_linearized_liquid_density(self.law,pressure_pa_abs=pressure_Pa_abs,temperature_K=water_temperature_K)
        return TestLiquidState(status=state.status,pressure_Pa_abs=pressure_Pa_abs,water_temperature_K=water_temperature_K,
            fluid_id=self.law.fluid_id,property_basis_id=self.law.property_basis_id,density_kg_m3=state.density_kg_m3,
            phase='liquid' if state.status=='computed' else None,
            isothermal_compressibility_Pa_inv=state.current_isothermal_compressibility_pa_inv,issues=state.issues)
