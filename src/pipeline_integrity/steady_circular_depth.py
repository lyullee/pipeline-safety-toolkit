"""Steady wet circular-section observations on the existing geometry/Chezy law.

The axial coordinate follows the pipe, depth is normal to its bottom, and the
positive downhill angle gives g*sin(angle) along the axis and g*cos(angle)
normal to the section. Dry, full, critical transitions and hydraulic jumps are
not silently crossed by the smooth steady-profile integration.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field,model_validator

from .contracts import Calculation,Contract,Positive
from .gravity_free_surface_pipe import circular_section_from_depth
from .gravity_uniform_flow import UniformChezyRequest,evaluate_uniform_chezy_flow


class SteadyCircularFlow(Contract):
    inner_diameter_m: Positive
    volume_flow_m3_s: float
    gravity_m_s2: Positive = 9.80665
    signed_downhill_angle_rad: float = Field(default=0.0,gt=-math.pi/2,lt=math.pi/2)
    friction_model: Literal["none","chezy"] = "none"
    chezy_coefficient_m_half_per_s: Positive | None = None
    hydrostatic_prismatic_wet_section_reviewed: bool = False

    @model_validator(mode="after")
    def friction(self):
        if (self.friction_model=="chezy")!=(self.chezy_coefficient_m_half_per_s is not None):
            raise ValueError("Chezy friction needs a supplied coefficient; lossless mode omits it")
        return self


def _state(q,depth):
    section=circular_section_from_depth(depth,q.inner_diameter_m)
    area=float(section.area_m2)
    width=float(section.top_width_m)
    radius=float(section.hydraulic_radius_m)
    normal=q.gravity_m_s2*math.cos(q.signed_downhill_angle_rad)
    velocity=q.volume_flow_m3_s/area
    froude2=q.volume_flow_m3_s**2*width/(normal*area**3)
    slope=0.0 if q.friction_model=="none" else velocity*abs(velocity)/(q.chezy_coefficient_m_half_per_s**2*radius)
    values=dict(depth_m=float(depth),area_m2=area,top_width_m=width,
        wetted_perimeter_m=float(section.wetted_perimeter_m),hydraulic_radius_m=radius,
        mean_velocity_m_s=velocity,froude_squared=froude2,critical_denominator=1-froude2,
        signed_friction_slope=slope,normal_gravity_m_s2=normal)
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError("steady circular state exceeds finite arithmetic")
    return values


def _flow(request):
    q=SteadyCircularFlow.model_validate(request.model_dump(mode="python"))
    if not q.hydrostatic_prismatic_wet_section_reviewed:
        raise ValueError("hydrostatic prismatic wet-section applicability requires review")
    return q


class CircularDepthRootRequest(Contract):
    flow: SteadyCircularFlow
    depth_bracket_m: tuple[Positive,Positive]
    absolute_depth_tolerance_m: Positive = 1e-11
    relative_boundary_tolerance: Positive = 1e-8
    maximum_iterations: int = Field(default=150,ge=1,le=10000)

    @model_validator(mode="after")
    def bracket(self):
        lo,hi=self.depth_bracket_m
        if not 0<lo<hi<self.flow.inner_diameter_m:
            raise ValueError("depth bracket must strictly increase inside the wet circular section")
        return self


def _root(request,critical):
    from scipy.optimize import brentq
    q=CircularDepthRootRequest.model_validate(request.model_dump(mode="python"))
    flow=_flow(q.flow)
    if flow.volume_flow_m3_s==0:
        return Calculation(values={},status="out_of_scope",notes=("Zero discharge has no unique normal depth or strictly wet critical-depth root.",))
    if not critical and (flow.friction_model!="chezy" or flow.volume_flow_m3_s*flow.signed_downhill_angle_rad<=0):
        return Calculation(values={},status="out_of_scope",notes=("Nonzero uniform flow requires supplied resistance and a matching nonzero downhill direction.",))
    def residual(depth):
        state=_state(flow,depth)
        if critical:return state["froude_squared"]-1
        native=evaluate_uniform_chezy_flow(UniformChezyRequest(wetted_area_m2=state["area_m2"],
            wetted_perimeter_m=state["wetted_perimeter_m"],signed_downhill_bed_slope=math.tan(flow.signed_downhill_angle_rad),
            chezy_coefficient_m_half_per_s=flow.chezy_coefficient_m_half_per_s,uniform_prismatic_free_surface_reviewed=True))
        return (native.volume_flow_m3_s-flow.volume_flow_m3_s)/abs(flow.volume_flow_m3_s)
    try:
        lo,hi=q.depth_bracket_m
        left,right=residual(lo),residual(hi)
        if left==0:depth=lo
        elif right==0:depth=hi
        elif (left>0)==(right>0):
            raise ValueError("selected depth bracket does not enclose this root; other normal-depth branches may exist")
        else:depth=brentq(residual,lo,hi,xtol=q.absolute_depth_tolerance_m,rtol=1e-12,maxiter=q.maximum_iterations)
        error=residual(depth)
        if abs(error)>q.relative_boundary_tolerance:
            raise ValueError("depth root fails the supplied normalized boundary tolerance")
        values={**_state(flow,depth),"normalized_boundary_residual":error,
            "root_kind":"critical" if critical else "normal","global_uniqueness_proved":False}
    except (ArithmeticError,ValueError,RuntimeError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values=values,notes=("One caller-selected wet-depth root; no dry/full transition, hydraulic jump or regime-routing inference.",))


def solve_circular_normal_depth(request:CircularDepthRootRequest)->Calculation:
    return _root(request,False)


def solve_circular_normal_depth_branches(request:CircularDepthRootRequest)->Calculation:
    """Enumerate both Chezy normal-depth branches on the declared wet support.

    In a circular section Q squared is proportional to A cubed divided by
    wetted perimeter. Its single interior maximum separates the increasing
    shallow branch and decreasing near-full branch. The existing root owner
    solves each supported monotone interval, including a tangency at the peak.
    """
    from scipy.optimize import brentq
    q=CircularDepthRootRequest.model_validate(request.model_dump(mode='python'))
    flow=_flow(q.flow)
    if (flow.volume_flow_m3_s==0 or flow.friction_model!='chezy'
            or flow.volume_flow_m3_s*flow.signed_downhill_angle_rad<=0):
        return Calculation(values={},status='out_of_scope',
            notes=('Nonzero Chezy flow in the signed downhill direction required.',))
    theta=brentq(lambda t:6*t*math.sin(t)**2-t+math.sin(t)*math.cos(t),
                 math.pi/2,math.pi,xtol=1e-13)
    peak=flow.inner_diameter_m*(1-math.cos(theta))/2
    lo,hi=q.depth_bracket_m
    split=[lo]+([peak] if lo<peak<hi else [])+[hi]
    roots=[]
    if lo<=peak<=hi:
        point=_state(flow,peak)
        normal=evaluate_uniform_chezy_flow(UniformChezyRequest(wetted_area_m2=point['area_m2'],
            wetted_perimeter_m=point['wetted_perimeter_m'],
            signed_downhill_bed_slope=math.tan(flow.signed_downhill_angle_rad),
            chezy_coefficient_m_half_per_s=flow.chezy_coefficient_m_half_per_s,
            uniform_prismatic_free_surface_reviewed=True))
        residual=(normal.volume_flow_m3_s-flow.volume_flow_m3_s)/abs(flow.volume_flow_m3_s)
        # At the exact maximum the two monotone intervals share one tangent
        # root. Tiny sign changes from rounded geometry must not create a pair
        # of roots plus a third peak. A user root tolerance is too broad here:
        # it would merge physically distinct near-maximum branches.
        if abs(residual)<=64*math.ulp(1.):
            roots.append({**point,'normalized_boundary_residual':residual,'root_kind':'normal_tangent',
                          'global_uniqueness_proved':False})
    for left,right in (() if roots else zip(split,split[1:])):
        result=solve_circular_normal_depth(q.model_copy(update={'depth_bracket_m':(left,right)}))
        if result.status=='computed':
            depth=result.values['depth_m']
            if not any(abs(depth-old['depth_m'])<=q.absolute_depth_tolerance_m for old in roots):
                roots.append(result.values)
    roots.sort(key=lambda item:item['depth_m'])
    return Calculation(values={'normal_depth_roots':tuple(roots),'root_count':len(roots),
        'maximum_discharge_depth_m':peak,'wet_support_m':q.depth_bracket_m,
        'complete_on_declared_wet_support':True},
        notes=('Both monotone circular-Chezy branches searched; dry/full endpoints and hydraulic jumps excluded.',))


def solve_circular_critical_depth(request:CircularDepthRootRequest)->Calculation:
    return _root(request,True)


class SteadyCircularProfileRequest(Contract):
    flow: SteadyCircularFlow
    initial_depth_m: Positive
    stations_m: tuple[float,...] = Field(min_length=2,max_length=100001)
    depth_support_m: tuple[Positive,Positive]
    minimum_critical_denominator_magnitude: Positive = Field(default=1e-3,lt=1)
    maximum_step_m: Positive
    relative_tolerance: Positive = Field(default=1e-9,le=1e-3)
    absolute_depth_tolerance_m: Positive = 1e-11
    absolute_work_tolerance_J_kg: Positive = 1e-9
    energy_budget_tolerance_J_kg: Positive = 1e-6
    maximum_rhs_evaluations: int = Field(default=200000,ge=1,le=2000000)

    @model_validator(mode="after")
    def domain(self):
        lo,hi=self.depth_support_m
        if not 0<lo<self.initial_depth_m<hi<self.flow.inner_diameter_m:
            raise ValueError("initial depth must be strictly within a positive wet support")
        if self.stations_m[0]!=0 or any(b<=a for a,b in zip(self.stations_m,self.stations_m[1:])):
            raise ValueError("profile stations must start at zero and strictly increase")
        return self


def evaluate_steady_circular_profile(request:SteadyCircularProfileRequest)->Calculation:
    from scipy.integrate import solve_ivp
    q=SteadyCircularProfileRequest.model_validate(request.model_dump(mode="python"))
    flow=_flow(q.flow)
    try:
        initial=_state(flow,q.initial_depth_m)
    except (ArithmeticError,ValueError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    margin=q.minimum_critical_denominator_magnitude
    if abs(initial["critical_denominator"])<=margin:
        return Calculation(values={},status="out_of_scope",notes=("Initial state is inside the excluded critical band.",))
    sign=math.copysign(1,initial["critical_denominator"])
    normal=initial["normal_gravity_m_s2"]
    axial=flow.gravity_m_s2*math.sin(flow.signed_downhill_angle_rad)
    initial_energy=normal*q.initial_depth_m+0.5*initial["mean_velocity_m_s"]**2
    evaluations=0
    def rhs(x,y):
        nonlocal evaluations
        evaluations+=1
        if evaluations>q.maximum_rhs_evaluations:raise ValueError("steady-profile RHS budget exhausted")
        state=_state(flow,float(y[0]))
        return ((math.tan(flow.signed_downhill_angle_rad)-state["signed_friction_slope"])/state["critical_denominator"],
                normal*state["signed_friction_slope"])
    def lower(x,y):return y[0]-q.depth_support_m[0]
    def upper(x,y):return q.depth_support_m[1]-y[0]
    def critical(x,y):return sign*_state(flow,float(y[0]))["critical_denominator"]-margin
    for event in (lower,upper,critical):event.terminal=True;event.direction=-1
    try:
        solution=solve_ivp(rhs,(0,q.stations_m[-1]),(q.initial_depth_m,0.0),method="DOP853",
            rtol=q.relative_tolerance,atol=(q.absolute_depth_tolerance_m,q.absolute_work_tolerance_J_kg),
            max_step=q.maximum_step_m,t_eval=q.stations_m,events=(lower,upper,critical))
        if not solution.success:raise ValueError("steady-profile integration failed: "+solution.message)
        samples=list(zip(solution.t,solution.y.T))
        terminal="requested_station_reached"
        for label,times,states in zip(("lower_depth_support","upper_depth_support","critical_margin"),solution.t_events,solution.y_events):
            if len(times):
                terminal=label
                if samples[-1][0]<times[0]:samples.append((times[0],states[0]))
        rows=[]
        for x,y in samples:
            state=_state(flow,float(y[0]))
            energy=normal*state["depth_m"]+0.5*state["mean_velocity_m_s"]**2-axial*x
            error=energy-initial_energy+float(y[1])
            if abs(error)>q.energy_budget_tolerance_J_kg:
                raise ValueError("steady-profile specific mechanical-energy budget failed")
            rows.append({"station_m":float(x),**state,"specific_mechanical_energy_J_kg":float(energy),
                "signed_friction_work_J_kg":float(y[1]),"energy_budget_residual_J_kg":float(error)})
    except (ArithmeticError,ValueError,RuntimeError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values={"rows":rows,"termination":terminal,"rhs_evaluations":evaluations,
        "constant_volume_flow_m3_s":flow.volume_flow_m3_s},notes=(
        "Smooth steady hydrostatic profile on one selected critical branch; terminal support/critical events retain their actual station.",
        "No jump continuation, pressurized routing, gas-pocket pressure, vapor-floor head profile or field-calibrated resistance is inferred.",))
