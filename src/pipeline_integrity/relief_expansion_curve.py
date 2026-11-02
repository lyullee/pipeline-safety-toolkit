"""Total-resistance capacity from an explicitly supplied expansion curve.

The curve is calibration data on its exact K/gamma/inlet convention. No
rupture-disk coefficient, proprietary expansion chart or KR/Cd conversion is
included. A reviewed sonic capacity maximum is not inferred from arbitrary data.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive
from .relief_line_capacity import (CalibratedReliefLineRequest,CalibratedReliefLineResult,
    NetExpansionCalibration,evaluate_calibrated_relief_line)


class ReliefExpansionPoint(Contract):
    calibration_id: Name
    revision: Name
    coefficient_definition_id: Name
    inlet_state_definition_id: Name
    inlet_pressure_Pa_abs: Positive
    inlet_temperature_K: Positive
    inlet_density_kg_m3: Positive
    total_K: Positive
    heat_capacity_ratio: float = Field(gt=1)
    pressure_drop_fraction: float = Field(ge=0,lt=1)
    net_expansion_factor: float = Field(gt=0,le=1)


class ReliefCurveRequest(Contract):
    system: CalibratedReliefLineRequest
    calibration_id: Name | None
    coefficient_definition_id: Name
    critical_selection: Literal['reviewed_curve_maximum','provided_model_critical_limit'] = 'reviewed_curve_maximum'
    pressure_drop_fraction_support: tuple[float,float]
    reviewed_curve_state_convention_and_single_sonic_capacity_maximum: bool = False
    maximum_search_iterations: int = Field(default=200,ge=1,le=2000)
    search_absolute_tolerance: Positive = Field(default=1e-10,le=1e-3)

    @model_validator(mode='after')
    def interval(self):
        if self.system.net_expansion is not None:raise ValueError('Choose curve-driven calibration, not an additional independent scalar Y/critical datum.')
        lo,hi=self.pressure_drop_fraction_support
        if not 0<=lo<hi<1:raise ValueError('Curve support must be an ordered finite drop-fraction interval in0..1.')
        return self


class ReliefCurveResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','insufficient_support','not_converged']
    calibration_id: Name | None
    coefficient_definition_id: Name
    critical_selection: Literal['reviewed_curve_maximum','provided_model_critical_limit']
    calibration_revision: Name | None = None
    critical_pressure_drop_fraction: float | None = None
    effective_net_expansion_factor: float | None = None
    capacity: CalibratedReliefLineResult | None = None
    issues: tuple[str,...] = ()
    universal_expansion_KR_Cd_or_Fanno_mapping_inferred: Literal[False] = False
    actual_curve_or_device_calibration_qualified: Literal[False] = False


def observe_curve_driven_relief_capacity(q:ReliefCurveRequest,*,expansion_provider=None)->ReliefCurveResult:
    """Locate a supplied curve's reviewed sonic limit and reuse total-K capacity.

Provider ``point(x, total_K=..., heat_capacity_ratio=..., inlet_state_definition_id=...,
inlet_pressure_Pa_abs=..., inlet_temperature_K=..., inlet_density_kg_m3=...)``
returns ReliefExpansionPoint. The optimized quantity is Y(x)^2*x: the remaining
positive dimensional factors are fixed at the reviewed inlet/K/area state.
    Curve support/shape checks are not a general proof of choking; the physical
    single-sonic-maximum interpretation requires the caller's explicit review.
    Alternatively, ``critical_limit(**state_arguments)`` supplies a tagged sonic
    point, and only the precritical/boundary curve is needed. That mode queries
    the supplied model's critical lookup; it does not invent a continuation past
    its supported sonic boundary.
"""
    from scipy.optimize import minimize_scalar
    q=ReliefCurveRequest.model_validate(q.model_dump(mode='python'));s=q.system
    base=dict(calibration_id=q.calibration_id,coefficient_definition_id=q.coefficient_definition_id,critical_selection=q.critical_selection)
    baseline=evaluate_calibrated_relief_line(s)
    if baseline.status=='computed':return ReliefCurveResult(status='computed',**base,capacity=baseline)
    if baseline.status=='out_of_scope':return ReliefCurveResult(status='out_of_scope',**base,capacity=baseline,issues=baseline.issues)
    required=(s.inlet_pressure_Pa_abs,s.back_pressure_Pa_abs,s.inlet_temperature_K,s.inlet_density_kg_m3,s.heat_capacity_ratio,s.common_flow_area_m2)
    if any(v is None for v in required) or not s.loss_terms or not s.same_bore_common_K_reference_and_steady_inlet_state_reviewed or any(not t.applicability_reviewed for t in s.loss_terms):
        return ReliefCurveResult(status='needs_data',**base,capacity=baseline,issues=baseline.issues)
    if expansion_provider is None or q.calibration_id is None or not q.reviewed_curve_state_convention_and_single_sonic_capacity_maximum:
        return ReliefCurveResult(status='needs_data',**base,issues=('A state-specific calibrated expansion curve and reviewed single sonic maximum are required; no chart values inferred.',))
    identity=None;k=baseline.total_K
    state_arguments=dict(total_K=k,heat_capacity_ratio=s.heat_capacity_ratio,
        inlet_state_definition_id=s.inlet_state_definition_id,inlet_pressure_Pa_abs=s.inlet_pressure_Pa_abs,
        inlet_temperature_K=s.inlet_temperature_K,inlet_density_kg_m3=s.inlet_density_kg_m3)
    def checked(point,x=None):
        nonlocal identity
        if not isinstance(point,ReliefExpansionPoint):raise ValueError('Typed expansion calibration point required.')
        point=ReliefExpansionPoint.model_validate(point.model_dump(mode='python'))
        if (point.calibration_id,point.coefficient_definition_id)!=(q.calibration_id,q.coefficient_definition_id):raise ValueError('Expansion calibration identity/convention changed.')
        expected=(s.inlet_state_definition_id,s.inlet_pressure_Pa_abs,s.inlet_temperature_K,s.inlet_density_kg_m3,k,s.heat_capacity_ratio,point.pressure_drop_fraction if x is None else x)
        actual=(point.inlet_state_definition_id,point.inlet_pressure_Pa_abs,point.inlet_temperature_K,point.inlet_density_kg_m3,point.total_K,point.heat_capacity_ratio,point.pressure_drop_fraction)
        if actual!=expected:raise ValueError('Expansion point changed requested K/gamma/inlet state or pressure fraction.')
        if identity is not None and identity!=point.revision:raise ValueError('Expansion calibration revision changed during capacity observation.')
        identity=point.revision
        return point
    def factor(x):return checked(expansion_provider.point(x,**state_arguments),x).net_expansion_factor
    lo,hi=q.pressure_drop_fraction_support
    critical_factor=None
    if q.critical_selection=='provided_model_critical_limit':
        if not callable(getattr(expansion_provider,'critical_limit',None)):
            return ReliefCurveResult(status='needs_data',**base,issues=('The selected calibration model must supply its state-matched critical-limit observation.',))
        limit=checked(expansion_provider.critical_limit(**state_arguments))
        critical=limit.pressure_drop_fraction;critical_factor=limit.net_expansion_factor
        if not lo<critical<=hi:return ReliefCurveResult(status='insufficient_support',**base,issues=('Supplied critical limit lies outside reviewed curve support.',))
    else:
        grid=[lo+(hi-lo)*i/32 for i in range(33)];factors=[factor(x) for x in grid]
        shape=[x*y*y for x,y in zip(grid,factors)];peak=max(range(len(shape)),key=shape.__getitem__)
        if peak in (0,32):return ReliefCurveResult(status='insufficient_support',**base,issues=('Supported curve does not bracket an interior capacity maximum.',))
        slack=max(shape)*1e-12
        if any(shape[i+1]<shape[i]-slack for i in range(peak)) or any(shape[i+1]>shape[i]+slack for i in range(peak,32)):
            return ReliefCurveResult(status='out_of_scope',**base,issues=('Supported curve is not a resolved single-maximum capacity branch.',))
        optimum=minimize_scalar(lambda x:-x*factor(x)**2,bounds=(grid[peak-1],grid[peak+1]),method='bounded',
            options={'xatol':q.search_absolute_tolerance,'maxiter':q.maximum_search_iterations})
        if not optimum.success:return ReliefCurveResult(status='not_converged',**base,issues=('Calibrated capacity-maximum search did not converge.',))
        critical=float(optimum.x)
        if not lo<critical<hi:return ReliefCurveResult(status='insufficient_support',**base,issues=('Critical limit is outside reviewed calibration support.',))
        probe=min((hi-lo)/64,critical-lo,hi-critical)/2
        nearby=[(critical+offset)*factor(critical+offset)**2 for offset in (-probe,probe)]
        if -optimum.fun-max(nearby)<=abs(optimum.fun)*1e-12:
            return ReliefCurveResult(status='insufficient_support',**base,issues=('A unique capacity-maximum location is not numerically resolved on this supported curve.',))
        if not math.isclose(factor(grid[0]),factors[0],rel_tol=1e-12,abs_tol=0):raise ValueError('Expansion curve drifted at a repeated support point.')
    x=(s.inlet_pressure_Pa_abs-s.back_pressure_Pa_abs)/s.inlet_pressure_Pa_abs;effective=min(x,critical)
    if effective<lo:return ReliefCurveResult(status='insufficient_support',**base,issues=('Actual precritical pressure drop lies below calibrated support.',))
    value=factor(effective)
    if critical_factor is not None and effective==critical and not math.isclose(value,critical_factor,rel_tol=1e-12,abs_tol=0):
        raise ValueError('Critical-limit and expansion-curve observations disagree on the same state.')
    calibration=NetExpansionCalibration(basis_id=q.calibration_id,total_K=k,heat_capacity_ratio=s.heat_capacity_ratio,
        inlet_state_definition_id=s.inlet_state_definition_id,critical_pressure_drop_fraction=critical,
        expansion_evaluated_at_drop_fraction=effective,net_expansion_factor=value,common_area_K_and_expansion_convention_reviewed=True)
    capacity=evaluate_calibrated_relief_line(s.model_copy(update={'net_expansion':calibration}))
    return ReliefCurveResult(status=capacity.status,**base,calibration_revision=identity,critical_pressure_drop_fraction=critical,
        effective_net_expansion_factor=value,capacity=capacity,issues=capacity.issues)
