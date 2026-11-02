"""Uniform passive DC pipe/coating line with explicit potential and current signs.

The state is a potential displacement about a caller-defined zero-drain reference.
No reference-electrode value, polarization kinetics or protection criterion is
inferred. Conventional axial current is positive in the coordinate +x direction;
endpoint currents are positive into the line and shunt current positive outward.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from scipy.integrate import quad
from .contracts import Contract,Name,Nonnegative,Positive


class CPLineBoundary(Contract):
    potential_displacement_V: float | None = None
    inward_current_A: float | None = None

    @model_validator(mode='after')
    def one_coordinate(self):
        if (self.potential_displacement_V is None)==(self.inward_current_A is None):
            raise ValueError('one endpoint potential or inward current is required')
        return self


class UniformCPLineRequest(Contract):
    mode: Literal['finite','insulated_end_target','semi_infinite_arm']
    longitudinal_resistance_ohm_m: Positive | None = None
    transverse_conductance_S_m: Nonnegative | None = None
    length_m: Positive | None = None
    left: CPLineBoundary | None = None
    right: CPLineBoundary | None = None
    insulated_end_potential_displacement_V: float | None = None
    sample_positions_m: tuple[Nonnegative,...] = ()
    semi_infinite_target_magnitude_V: Positive | None = None
    symmetric_two_identical_arms_reviewed: bool = False
    asset_id: Name
    coordinate_frame_id: Name
    potential_reference_id: Name
    coefficient_basis_id: Name
    uniform_passive_ohmic_pipe_and_shunt_reference_reviewed: bool = False
    quadrature_relative_tolerance: Positive = Field(default=1e-10,ge=5e-14)
    absolute_charge_balance_tolerance_A: Positive = 1e-9
    absolute_power_balance_tolerance_W: Positive = 1e-9

    @model_validator(mode='after')
    def geometry_and_boundaries(self):
        if self.mode=='semi_infinite_arm':
            if self.length_m is not None or self.right is not None or self.insulated_end_potential_displacement_V is not None:
                raise ValueError('semi-infinite arm has only its drain endpoint and infinity reference')
        else:
            if self.symmetric_two_identical_arms_reviewed or self.semi_infinite_target_magnitude_V is not None:
                raise ValueError('two-arm and reach observations belong to the semi-infinite arm')
            if self.mode=='insulated_end_target' and (self.left is not None or self.right is not None):
                raise ValueError('insulated-end inverse supplies end potential and zero axial end current, not extra endpoint data')
            if self.mode=='finite' and self.insulated_end_potential_displacement_V is not None:
                raise ValueError('finite endpoint mode cannot also impose an insulated-end inverse')
            if self.length_m is not None and any(x>self.length_m for x in self.sample_positions_m):
                raise ValueError('finite observations must be inside the declared line')
        return self


class CPLinePoint(Contract):
    position_m: Nonnegative
    potential_displacement_V: float
    positive_x_axial_current_A: float
    outward_shunt_current_A_m: float
    longitudinal_joule_power_W_m: Nonnegative
    shunt_joule_power_W_m: Nonnegative


class UniformCPLineResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','non_identifiable','inconsistent_boundaries','not_converged']
    asset_id: Name
    coordinate_frame_id: Name
    potential_reference_id: Name
    coefficient_basis_id: Name
    points: tuple[CPLinePoint,...] = ()
    attenuation_per_m: Nonnegative | None = None
    left_potential_displacement_V: float | None = None
    right_potential_displacement_V: float | None = None
    left_inward_current_A: float | None = None
    right_inward_current_A: float | None = None
    symmetric_two_arm_total_drain_current_A: float | None = None
    semi_infinite_target_reach_m: Nonnegative | None = None
    semi_infinite_target_status: Literal['not_requested','reached','not_attained'] = 'not_requested'
    outward_shunt_current_integral_A: float | None = None
    charge_balance_residual_A: float | None = None
    boundary_power_into_line_W: float | None = None
    longitudinal_joule_power_W: Nonnegative | None = None
    shunt_joule_power_W: Nonnegative | None = None
    power_balance_residual_W: float | None = None
    charge_quadrature_error_A: Nonnegative | None = None
    longitudinal_power_quadrature_error_W: Nonnegative | None = None
    shunt_power_quadrature_error_W: Nonnegative | None = None
    issues: tuple[str,...] = ()
    electrochemical_polarization_or_protection_acceptance_inferred: Literal[False] = False


def evaluate_uniform_cp_line(q:UniformCPLineRequest)->UniformCPLineResult:
    """Solve V'=-r I and I'=-g V for the explicitly selected boundary problem.

    'insulated_end_target' inverts a supplied far-end potential with I(L)=0.
    A semi-infinite result always reports one arm; the optional two-arm total is
    a separately declared symmetric topology, never an implicit chart factor.
    """
    q=UniformCPLineRequest.model_validate(q.model_dump(mode='python'))
    common=dict(asset_id=q.asset_id,coordinate_frame_id=q.coordinate_frame_id,
        potential_reference_id=q.potential_reference_id,coefficient_basis_id=q.coefficient_basis_id)
    missing=(q.longitudinal_resistance_ohm_m is None or q.transverse_conductance_S_m is None
        or not q.uniform_passive_ohmic_pipe_and_shunt_reference_reviewed)
    if q.mode=='finite':missing|=q.length_m is None or q.left is None or q.right is None
    elif q.mode=='insulated_end_target':missing|=q.length_m is None or q.insulated_end_potential_displacement_V is None
    else:missing|=q.left is None
    if missing:return UniformCPLineResult(status='needs_data',issues=('coefficient, boundary and passive reference reviews required',),**common)
    r,g=q.longitudinal_resistance_ohm_m,q.transverse_conductance_S_m
    k=math.sqrt(r)*math.sqrt(g)
    try:
        if q.mode=='semi_infinite_arm':
            if g==0:return UniformCPLineResult(status='out_of_scope',issues=('decay to zero at infinity needs positive shunt conductance',),**common)
            y0=math.sqrt(g)/math.sqrt(r)
            v0=q.left.potential_displacement_V if q.left.potential_displacement_V is not None else q.left.inward_current_A/y0
            current=y0*v0
            positions=q.sample_positions_m or (0.,)
            if any(k*x>500 for x in positions):
                return UniformCPLineResult(status='out_of_scope',issues=('requested decay exceeds the declared finite observation support',),**common)
            def field(x):
                v=v0*math.exp(-k*x);return v,y0*v
            longitudinal=shunt=.5*v0*current
            power=v0*current;leak=current
            reach=None;target_status='not_requested'
            if q.semi_infinite_target_magnitude_V is not None:
                target=q.semi_infinite_target_magnitude_V
                if abs(v0)<target:target_status='not_attained'
                else:reach=(math.log(abs(v0))-math.log(target))/k;target_status='reached'
            right_v=right_i=None;left_v=v0;left_i=current
            errq=errr=errg=0.;charge_residual=power_residual=0.
        else:
            length=q.length_m;theta=k*length
            if not math.isfinite(theta) or theta>500:
                return UniformCPLineResult(status='out_of_scope',issues=('line decay outside declared finite arithmetic support',),**common)
            if q.mode=='insulated_end_target':
                terminal=q.insulated_end_potential_displacement_V
                left=CPLineBoundary(potential_displacement_V=terminal*math.cosh(theta))
                right=CPLineBoundary(inward_current_A=0.)
            else:left,right=q.left,q.right
            if g==0:
                if left.inward_current_A is not None and right.inward_current_A is not None:
                    if abs(left.inward_current_A+right.inward_current_A)>q.absolute_charge_balance_tolerance_A:
                        return UniformCPLineResult(status='inconsistent_boundaries',issues=('perfect coating cannot balance net injected current',),**common)
                    return UniformCPLineResult(status='non_identifiable',issues=('zero leakage and current-only boundaries leave the potential reference free',),**common)
                if left.potential_displacement_V is not None:
                    left_v=left.potential_displacement_V
                    current=(left_v-right.potential_displacement_V)/(r*length) if right.potential_displacement_V is not None else -right.inward_current_A
                    right_v=left_v-r*length*current
                else:
                    current=left.inward_current_A;right_v=right.potential_displacement_V;left_v=right_v+r*length*current
                left_i,right_i=current,-current
                def field(x):return left_v-r*x*current,current
            else:
                y0=math.sqrt(g)/math.sqrt(r);half=theta/2
                if half==0:return UniformCPLineResult(status='out_of_scope',issues=('positive leakage decay underflow',),**common)
                yc=y0*math.tanh(half);yd=y0/math.tanh(half)
                if left.potential_displacement_V is not None and right.potential_displacement_V is not None:
                    left_v,right_v=left.potential_displacement_V,right.potential_displacement_V
                    mean=.5*left_v+.5*right_v;difference=.5*left_v-.5*right_v
                    left_i=yc*mean+yd*difference;right_i=yc*mean-yd*difference
                elif left.inward_current_A is not None and right.inward_current_A is not None:
                    left_i,right_i=left.inward_current_A,right.inward_current_A
                    mean=(.5*left_i+.5*right_i)/yc;difference=(.5*left_i-.5*right_i)/yd
                    left_v,right_v=mean+difference,mean-difference
                else:
                    sech=2*math.exp(-theta)/(1+math.exp(-2*theta));tan=math.tanh(theta)
                    if left.potential_displacement_V is not None:
                        left_v=left.potential_displacement_V;right_i=right.inward_current_A
                        right_v=sech*left_v+tan/y0*right_i
                        left_i=y0*tan*left_v-sech*right_i
                    else:
                        left_i=left.inward_current_A;right_v=right.potential_displacement_V
                        left_v=sech*right_v+tan/y0*left_i
                        right_i=y0*tan*right_v-sech*left_i
                mean=.5*left_v+.5*right_v;difference=.5*left_v-.5*right_v
                def field(x):
                    z=k*(x-length/2);az=abs(z);sign=1 if z>=0 else -1
                    scale=math.exp(az-half)
                    cosh_ratio=scale*(1+math.exp(-2*az))/(1+math.exp(-2*half))
                    sinh_ratio=sign*scale*(-math.expm1(-2*az))/(-math.expm1(-2*half))
                    sinh_cosh_ratio=sign*scale*(-math.expm1(-2*az))/(1+math.exp(-2*half))
                    return mean*cosh_ratio-difference*sinh_ratio,y0*(-mean*sinh_cosh_ratio+difference*cosh_ratio/math.tanh(half))
            positions=q.sample_positions_m or (0.,length)
            opts=dict(epsabs=min(q.absolute_charge_balance_tolerance_A,q.absolute_power_balance_tolerance_W)*.01,
                epsrel=q.quadrature_relative_tolerance,limit=300)
            leak,errq=quad(lambda x:g*field(x)[0],0,length,**opts)
            longitudinal,errr=quad(lambda x:r*field(x)[1]**2,0,length,**opts)
            shunt,errg=quad(lambda x:g*field(x)[0]**2,0,length,**opts)
            power=left_v*left_i+right_v*right_i
            charge_residual=left_i+right_i-leak;power_residual=power-longitudinal-shunt
            reach=None;target_status='not_requested'
            if abs(charge_residual)>q.absolute_charge_balance_tolerance_A+q.quadrature_relative_tolerance*max(abs(left_i),abs(right_i)) or abs(power_residual)>q.absolute_power_balance_tolerance_W+q.quadrature_relative_tolerance*abs(power):
                return UniformCPLineResult(status='not_converged',charge_balance_residual_A=charge_residual,
                    power_balance_residual_W=power_residual,charge_quadrature_error_A=errq,
                    longitudinal_power_quadrature_error_W=errr,shunt_power_quadrature_error_W=errg,
                    issues=('integrated charge/power error budget not met',),**common)
        points=tuple(CPLinePoint(position_m=x,potential_displacement_V=field(x)[0],positive_x_axial_current_A=field(x)[1],
            outward_shunt_current_A_m=g*field(x)[0],longitudinal_joule_power_W_m=r*field(x)[1]**2,
            shunt_joule_power_W_m=g*field(x)[0]**2) for x in positions)
        return UniformCPLineResult(status='computed',points=points,attenuation_per_m=k,
            left_potential_displacement_V=left_v,right_potential_displacement_V=right_v,
            left_inward_current_A=left_i,right_inward_current_A=right_i,
            symmetric_two_arm_total_drain_current_A=2*left_i if q.symmetric_two_identical_arms_reviewed else None,
            semi_infinite_target_reach_m=reach,semi_infinite_target_status=target_status,
            outward_shunt_current_integral_A=leak,charge_balance_residual_A=charge_residual,
            boundary_power_into_line_W=power,longitudinal_joule_power_W=longitudinal,shunt_joule_power_W=shunt,
            power_balance_residual_W=power_residual,charge_quadrature_error_A=errq,
            longitudinal_power_quadrature_error_W=errr,shunt_power_quadrature_error_W=errg,**common)
    except (ValueError,OverflowError,ZeroDivisionError) as exc:
        return UniformCPLineResult(status='out_of_scope',issues=('finite passive line arithmetic unavailable: '+str(exc),),**common)
