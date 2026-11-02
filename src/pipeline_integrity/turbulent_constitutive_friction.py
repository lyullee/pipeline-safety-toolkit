"""Implicit empirical turbulent resistance laws, with explicit domain selection.

The Reynolds coordinate is model dependent. For power-law material it is
|v|**(2-n)*D**n/(K/rho); for viscoplastic material it uses plastic viscosity.
These functions solve the declared relations; they do not identify a flow
regime, fit rheology or establish an additive product's concentration response.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Nonnegative, Positive


class TurbulentResistanceRequest(Contract):
    model: Literal["power_law", "viscoplastic", "wall_interaction"]
    reynolds_coordinate: Positive | None = None
    flow_index: Positive | None = None
    hedstrom_number: Nonnegative | None = None
    relative_roughness: Fraction | None = None
    wall_interaction_parameter: Positive | None = None
    wall_relation: Literal["mixing_length", "smooth_rounded"] = "mixing_length"
    mixing_length_constant: Positive = 0.4
    roughness_wall_constant: Nonnegative = 0.31
    friction_search_bounds: tuple[Positive,Positive] = (1e-6,1.0)
    log_root_absolute_tolerance: Positive = Field(default=1e-12,le=1e-4)
    root_maximum_iterations: int = Field(default=150,ge=1,le=10000)
    turbulent_regime_coordinate_and_calibration_reviewed: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if self.friction_search_bounds[0]>=self.friction_search_bounds[1]:
            raise ValueError("friction search interval must increase")
        return self


class TurbulentResistanceResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope", "no_bracket"]
    darcy_friction_factor: Positive | None = None
    dimensionless_equation_residual: float | None = None
    yield_to_wall_shear_ratio: Nonnegative | None = None
    solver_iterations: int | None = None
    issues: tuple[str,...] = ()


def _log_one_plus_exp(x):
    return max(x,0.)+math.log1p(math.exp(-abs(x)))


def _wall_log_denominator(log_re,log_f,roughness,a):
    if roughness==0 or a==0:
        return 0.
    return _log_one_plus_exp(math.log(a)+math.log(roughness)+log_re+.5*(log_f-math.log(8)))


def evaluate_turbulent_resistance(request: TurbulentResistanceRequest) -> TurbulentResistanceResult:
    """Solve in log friction to retain a positive coefficient without clipping.

    A missing sign-changing bracket is a numerical/domain outcome, not a
    declaration that the real pipe cannot flow. The viscoplastic branch only
    accepts yield stress below wall stress. Supplied search bounds are solver
    bounds, not empirical validity limits. No Newtonian correlation is silently
    substituted for an unqualified non-Newtonian regime.
    """
    if not isinstance(request,TurbulentResistanceRequest):
        raise TypeError("TurbulentResistanceRequest required")
    q=TurbulentResistanceRequest.model_validate(request.model_dump(mode="python"))
    needed=["reynolds_coordinate"]
    if q.model=="power_law":needed.append("flow_index")
    elif q.model=="viscoplastic":needed.append("hedstrom_number")
    else:
        needed.append("wall_interaction_parameter")
        if q.wall_relation=="mixing_length":needed.append("relative_roughness")
    issues=["missing "+name for name in needed if getattr(q,name) is None]
    if not q.turbulent_regime_coordinate_and_calibration_reviewed:
        issues.append("turbulent regime, Reynolds definition and correlation calibration require review")
    if issues:return TurbulentResistanceResult(status="needs_data",issues=tuple(issues))
    if q.model=="power_law" and not .2<=q.flow_index<=2:
        return TurbulentResistanceResult(status="out_of_scope",issues=("supported power-law index interval is 0.2 to 2",))
    from scipy.optimize import brentq
    log_re=math.log(q.reynolds_coordinate)
    lo,hi=(math.log(value) for value in q.friction_search_bounds)
    if q.model=="viscoplastic" and q.hedstrom_number>0:
        threshold=math.log(8)+math.log(q.hedstrom_number)-2*log_re
        lo=max(lo,threshold+1e-10)
        if lo>=hi:
            return TurbulentResistanceResult(status="out_of_scope",issues=("search interval does not reach above the wall-yield threshold",))

    def residual(log_f):
        inverse_sqrt=math.exp(-.5*log_f)
        if q.model=="power_law":
            n=q.flow_index
            log_k=(1-n/2)*math.log(2)+n*(2.83-.2/n**1.2)/.88
            return inverse_sqrt-(.88/n)*(log_k+log_re+(1-n/2)*(log_f-math.log(8)))+2.83
        if q.model=="viscoplastic":
            ratio=0. if q.hedstrom_number==0 else math.exp(math.log(8)+math.log(q.hedstrom_number)-2*log_re-log_f)
            return inverse_sqrt-(1-ratio)*(.88*(log_re+.5*log_f)-.8)-2.76*ratio
        if q.wall_relation=="smooth_rounded":
            return inverse_sqrt-.88*(math.log(q.wall_interaction_parameter)+log_re+.5*log_f)+3.745
        logarithm=(math.log(q.wall_interaction_parameter)+math.log(q.mixing_length_constant)
                   +log_re+.5*(log_f-math.log(8))
                   -_wall_log_denominator(log_re,log_f,q.relative_roughness,q.roughness_wall_constant))
        return math.sqrt(8)*inverse_sqrt-(logarithm-137/60)/q.mixing_length_constant

    rlo,rhi=residual(lo),residual(hi)
    if not all(math.isfinite(x) for x in (rlo,rhi)):
        raise ValueError("resistance equation exceeds finite numerical support")
    if rlo!=0 and rhi!=0 and math.copysign(1,rlo)==math.copysign(1,rhi):
        return TurbulentResistanceResult(status="no_bracket",issues=("no sign-changing root in the supplied friction interval",))
    if rlo==0:root,iterations=lo,0
    elif rhi==0:root,iterations=hi,0
    else:
        root,info=brentq(residual,lo,hi,xtol=q.log_root_absolute_tolerance,rtol=1e-14,
                        maxiter=q.root_maximum_iterations,full_output=True,disp=True)
        if not info.converged:raise RuntimeError("implicit resistance root did not converge")
        iterations=int(info.iterations)
    factor=math.exp(root)
    ratio=(0. if q.hedstrom_number==0 else math.exp(math.log(8)+math.log(q.hedstrom_number)-2*log_re-root)) if q.model=="viscoplastic" else None
    if ratio is not None and not 0<=ratio<1:
        raise RuntimeError("resolved root does not satisfy wall-yield support")
    return TurbulentResistanceResult(status="computed",darcy_friction_factor=factor,
        dimensionless_equation_residual=residual(root),yield_to_wall_shear_ratio=ratio,
        solver_iterations=iterations)


class WallInteractionInverseRequest(Contract):
    reynolds_coordinate: Positive | None = None
    darcy_friction_factor: Positive | None = None
    relative_roughness: Fraction | None = None
    wall_relation: Literal["mixing_length", "smooth_rounded"] = "mixing_length"
    mixing_length_constant: Positive = .4
    roughness_wall_constant: Nonnegative = .31
    turbulent_regime_coordinate_and_calibration_reviewed: bool = False


class WallInteractionInverseResult(Contract):
    status: Literal["computed", "needs_data"]
    wall_interaction_parameter: Positive | None = None
    issues: tuple[str,...] = ()


def required_wall_interaction_parameter(request: WallInteractionInverseRequest) -> WallInteractionInverseResult:
    """Invert for a model interaction parameter, never a product concentration.

    Concentration/dose requires a separate caller-owned reviewed relation. No
    product table, concentration interpolation or degradation law is built in.
    The full mixing-length equation and rounded smooth-wall equation retain
    different constants rather than silently mixing rounded representations.
    """
    if not isinstance(request,WallInteractionInverseRequest):
        raise TypeError("WallInteractionInverseRequest required")
    q=WallInteractionInverseRequest.model_validate(request.model_dump(mode="python"))
    needed=["reynolds_coordinate","darcy_friction_factor"]
    if q.wall_relation=="mixing_length":needed.append("relative_roughness")
    issues=["missing "+name for name in needed if getattr(q,name) is None]
    if not q.turbulent_regime_coordinate_and_calibration_reviewed:
        issues.append("turbulent regime, Reynolds definition and correlation calibration require review")
    if issues:return WallInteractionInverseResult(status="needs_data",issues=tuple(issues))
    lr,lf=math.log(q.reynolds_coordinate),math.log(q.darcy_friction_factor)
    if q.wall_relation=="smooth_rounded":
        log_k=(1/math.sqrt(q.darcy_friction_factor)+3.745)/.88-lr-.5*lf
    else:
        log_k=(q.mixing_length_constant*math.sqrt(8/q.darcy_friction_factor)+137/60
               +_wall_log_denominator(lr,lf,q.relative_roughness,q.roughness_wall_constant)
               -math.log(q.mixing_length_constant)-lr-.5*(lf-math.log(8)))
    try:k=math.exp(log_k)
    except OverflowError as exc:raise ValueError("required wall parameter exceeds finite numerical support") from exc
    if not math.isfinite(k) or k<=0:raise ValueError("required wall parameter exceeds positive finite support")
    return WallInteractionInverseResult(status="computed",wall_interaction_parameter=k)
