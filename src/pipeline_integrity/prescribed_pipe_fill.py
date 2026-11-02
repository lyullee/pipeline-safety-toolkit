"""Prescribed exponential filling in a constant-area, constant-density pipe.

Front position is a flow integral. Momentum accumulation, inlet momentum
flux and net external fluid force are separate control-volume quantities.
Neither pump pressure nor the reaction on a particular elbow is predicted.
"""
from __future__ import annotations

import math

from .contracts import Calculation,Contract,Name,Nonnegative,Positive


class ExponentialPipeFill(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    fluid_basis_id: Name
    area_m2: Positive
    liquid_density_kg_m3: Positive
    maximum_volume_flow_m3_s: Positive
    rise_time_s: Positive
    start_time_s: Nonnegative = 0.0
    initial_filled_length_m: Nonnegative = 0.0
    uniform_velocity_fixed_inlet_and_moving_front_reviewed: bool = False


def _integrated_fraction(z):
    # z+expm1(-z) loses its positive quadratic leading term near startup.
    if z<1e-3:
        return math.fsum((-1)**k*z**k/math.factorial(k) for k in range(2,10))
    return z+math.expm1(-z)


def evaluate_prescribed_pipe_fill(request:ExponentialPipeFill,time_s:float)->Calculation:
    q=ExponentialPipeFill.model_validate(request.model_dump(mode="python"))
    if not q.uniform_velocity_fixed_inlet_and_moving_front_reviewed:
        raise ValueError("uniform velocity, fixed inlet and moving filling-front assumptions require review")
    if not math.isfinite(time_s) or time_s<0:
        raise ValueError("fill observation needs a finite nonnegative time on the declared clock")
    elapsed=max(0.0,time_s-q.start_time_s)
    active=time_s>=q.start_time_s
    try:
        z=elapsed/q.rise_time_s
        fraction=-math.expm1(-z)
        flow=q.maximum_volume_flow_m3_s*fraction
        acceleration=(q.maximum_volume_flow_m3_s/q.area_m2/q.rise_time_s)*math.exp(-z) if active else 0.0
        added=q.maximum_volume_flow_m3_s*(q.rise_time_s*_integrated_fraction(z) if z<1 else
            elapsed-q.rise_time_s*fraction)
        if elapsed>0 and (flow<=0 or added<=0):
            raise ValueError("positive startup flow/inventory is below numerical representation")
        front=q.initial_filled_length_m+added/q.area_m2
        volume=q.area_m2*front
        mass=q.liquid_density_kg_m3*volume
        velocity=flow/q.area_m2
        momentum=mass*velocity
        inlet_flux=q.liquid_density_kg_m3*flow*velocity
        external=mass*acceleration
        accumulation=math.fsum((inlet_flux,external))
        kinetic=0.5*mass*velocity**2
        inlet_kinetic_power=0.5*q.liquid_density_kg_m3*flow*velocity**2
        external_power=external*velocity
        numeric=dict(elapsed_since_start_s=elapsed,inlet_volume_flow_m3_s=flow,
            added_liquid_volume_m3=added,front_position_m=front,stored_liquid_mass_kg=mass,
            liquid_velocity_m_s=velocity,liquid_acceleration_m_s2=acceleration,
            axial_liquid_momentum_kg_m_s=momentum,inlet_axial_momentum_flux_N=inlet_flux,
            axial_momentum_accumulation_N=accumulation,net_external_axial_force_on_fluid_N=external,
            liquid_kinetic_energy_J=kinetic,inlet_kinetic_energy_flux_W=inlet_kinetic_power,
            net_external_fluid_mechanical_power_W=external_power,
            kinetic_energy_accumulation_W=math.fsum((inlet_kinetic_power,external_power)))
        if not all(math.isfinite(v) for v in numeric.values()):
            raise ValueError("prescribed fill state exceeds finite arithmetic")
    except (ArithmeticError,ValueError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values={"asset_id":q.asset_id,"clock_id":q.clock_id,"time_s":time_s,
        **numeric,"elbow_reaction_N":None,"elbow_reaction_status":"needs_data",
        "startup_acceleration_is_right_sided":time_s==q.start_time_s},notes=(
        "Prescribed flow and plug kinematics only; no pump pressure, gas displacement or free-boundary hydrodynamics is predicted.",
        "Net external force on fluid includes all pressure, gravity and wall contributions; it is not an identified elbow/hardware reaction.",
        "An elbow reaction requires its actual wetted geometry, pressure and outgoing momentum states; use the separate prescribed-port momentum API when these are supplied.",))


def time_to_prescribed_fill_position(request:ExponentialPipeFill,target_position_m:float)->Calculation:
    from scipy.optimize import brentq
    q=ExponentialPipeFill.model_validate(request.model_dump(mode="python"))
    # Reuse evaluation policy and numerical input validation, including review.
    initial=evaluate_prescribed_pipe_fill(q,q.start_time_s)
    if initial.status!="computed":
        return initial
    if not math.isfinite(target_position_m) or target_position_m<q.initial_filled_length_m:
        return Calculation(values={},status="out_of_scope",notes=("Target must be a finite position at or beyond the initial front.",))
    delta=target_position_m-q.initial_filled_length_m
    if delta==0:
        return Calculation(values={"clock_id":q.clock_id,"arrival_time_s":q.start_time_s,
            "elapsed_since_start_s":0.0,"target_position_m":target_position_m})
    try:
        target=delta*q.area_m2/(q.maximum_volume_flow_m3_s*q.rise_time_s)
        if not math.isfinite(target) or target<=0 or target+1==target:
            raise ValueError("fill arrival target is outside representable root support")
        z=brentq(lambda z:_integrated_fraction(z)-target,target,target+1,
            xtol=min(1e-12,target),rtol=1e-12,maxiter=1000)
        elapsed=q.rise_time_s*z
        arrival=q.start_time_s+elapsed
        if not math.isfinite(arrival) or arrival<=q.start_time_s:
            raise ValueError("fill arrival is not resolved on the declared clock")
    except (ArithmeticError,ValueError,RuntimeError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values={"clock_id":q.clock_id,"arrival_time_s":arrival,
        "elapsed_since_start_s":elapsed,"target_position_m":target_position_m},notes=(
        "Unique positive flow-integral root; this is a kinematic arrival, not an impact force or pressure event.",))
