"""Supplied exponential liquid-viscosity law on a declared temperature interval.

This is a constitutive interpolation, not a fluid identifier or phase model.
Pressure dependence, uncertainty and calibration quality remain external data.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract,Name,Positive


class ExponentialViscosityLaw(Contract):
    material_basis_id: Name
    reference_temperature_K: Positive
    reference_kinematic_viscosity_m2_s: Positive
    temperature_coefficient_per_K: float
    temperature_support_K: tuple[Positive,Positive]

    @model_validator(mode="after")
    def support(self):
        lo,hi=self.temperature_support_K
        if not lo < hi or not lo <= self.reference_temperature_K <= hi:
            raise ValueError("ordered viscosity support must contain the reference temperature")
        return self


class TwoPointViscosityFit(Contract):
    material_basis_id: Name
    temperatures_K: tuple[Positive,Positive]
    kinematic_viscosities_m2_s: tuple[Positive,Positive]
    temperature_support_K: tuple[Positive,Positive] | None = None


class ViscosityFitResult(Contract):
    status: Literal["ok","needs_data","out_of_scope"]
    law: ExponentialViscosityLaw | None = None
    reason: str
    empirical_accuracy_claim: bool = False


def fit_exponential_viscosity(request:TwoPointViscosityFit)->ViscosityFitResult:
    q=TwoPointViscosityFit.model_validate(request.model_dump(mode="python"))
    first,second=q.temperatures_K
    nu_first,nu_second=q.kinematic_viscosities_m2_s
    if first==second:
        return ViscosityFitResult(status="needs_data" if nu_first==nu_second else "out_of_scope",
            reason="Repeated temperatures do not determine a slope; unequal viscosities at the same state contradict this single-valued law.")
    support=q.temperature_support_K or (min(first,second),max(first,second))
    if not support[0]<support[1] or not all(support[0]<=t<=support[1] for t in (first,second)):
        return ViscosityFitResult(status="out_of_scope",reason="The declared support must contain both calibration temperatures.")
    change=nu_second-nu_first
    log_ratio=(math.log1p(change/nu_first) if abs(change)<=0.5*nu_first
               else math.log(nu_second)-math.log(nu_first))
    coefficient=-log_ratio/(second-first)
    if not math.isfinite(coefficient):
        return ViscosityFitResult(status="out_of_scope",reason="The fitted slope exceeds finite arithmetic.")
    law=ExponentialViscosityLaw(material_basis_id=q.material_basis_id,reference_temperature_K=first,
        reference_kinematic_viscosity_m2_s=nu_first,temperature_coefficient_per_K=coefficient,
        temperature_support_K=support)
    return ViscosityFitResult(status="ok",law=law,
        reason="Two-point semilog law on declared support. Extending the data interval, pressure/phase and measured accuracy remain caller-reviewed.")


class ViscosityEvaluationRequest(Contract):
    law: ExponentialViscosityLaw
    temperature_K: Positive
    density_kg_m3: Positive | None = None
    density_material_basis_id: Name | None = None
    density_temperature_K: Positive | None = None


class ViscosityResult(Contract):
    status: Literal["ok","needs_data","out_of_scope"]
    kinematic_viscosity_m2_s: Positive | None = None
    dynamic_viscosity_Pa_s: Positive | None = None
    reason: str
    empirical_accuracy_claim: bool = False


def evaluate_exponential_viscosity(request:ViscosityEvaluationRequest)->ViscosityResult:
    q=ViscosityEvaluationRequest.model_validate(request.model_dump(mode="python"))
    law=q.law
    if not law.temperature_support_K[0]<=q.temperature_K<=law.temperature_support_K[1]:
        return ViscosityResult(status="out_of_scope",reason="Temperature is outside declared viscosity support; no extrapolation.")
    exponent=-law.temperature_coefficient_per_K*(q.temperature_K-law.reference_temperature_K)
    if not math.isfinite(exponent):
        return ViscosityResult(status="out_of_scope",reason="Viscosity exponent exceeds finite arithmetic.")
    try:
        nu=(law.reference_kinematic_viscosity_m2_s if exponent==0 else
            math.exp(math.log(law.reference_kinematic_viscosity_m2_s)+exponent))
    except OverflowError:
        return ViscosityResult(status="out_of_scope",reason="Viscosity exceeds finite arithmetic.")
    if not math.isfinite(nu) or nu<=0:
        return ViscosityResult(status="out_of_scope",reason="Positive viscosity is not representable; underflow is not zero viscosity.")
    if q.density_kg_m3 is None:
        return ViscosityResult(status="ok",kinematic_viscosity_m2_s=nu,
            reason="Kinematic viscosity only; density and dynamic viscosity are not inferred.")
    if (q.density_material_basis_id!=law.material_basis_id or q.density_temperature_K is None
            or not math.isclose(q.density_temperature_K,q.temperature_K,rel_tol=1e-12,abs_tol=1e-10)):
        return ViscosityResult(status="needs_data",kinematic_viscosity_m2_s=nu,
            reason="Dynamic viscosity requires supplied density on the same declared material/state basis.")
    dynamic=q.density_kg_m3*nu
    if not math.isfinite(dynamic) or dynamic<=0:
        return ViscosityResult(status="out_of_scope",kinematic_viscosity_m2_s=nu,
            reason="Dynamic viscosity exceeds positive finite arithmetic.")
    return ViscosityResult(status="ok",kinematic_viscosity_m2_s=nu,dynamic_viscosity_Pa_s=dynamic,
        reason="Supplied-law kinematic viscosity and same-basis density conversion; no pressure or phase prediction.")
