"""Reviewed same-fluid ideal-reservoir open edge with bounded reverse inflow.

Absolute exterior pressure is static pressure during outflow and stagnation
pressure of the quiescent exterior reservoir during inflow. The supplied
stagnation temperature applies only to incoming gas. No air/species mixing is
inferred. Supersonic interior inflow and reservoir-choked inflow are withheld.
"""
import math
import numpy as np
from scipy.optimize import brentq
from .gas_acoustic_pipe import (
    IdealGasInternalEnergyEOS, GasPressureOutflowFace, _recover,
    evaluate_pressure_outflow_face,
)


def evaluate_ideal_same_fluid_open_face(conservative,eos,reservoir_pressure_pa_abs,
        reservoir_stagnation_temperature_K,*,outward_normal=1,
        same_fluid_isentropic_reservoir_reviewed=False) -> GasPressureOutflowFace:
    """Match an entropy-admissible interior wave to reservoir mass/enthalpy.

    Subsonic outflow has one prescribed exterior pressure and inherits the
    interior state through the outgoing wave. Subsonic inflow supplies two
    reservoir conditions (entropy and stagnation enthalpy); pressure/velocity
    match the interior wave across the incoming contact. Inflow roots are
    confined to the unique subsonic branch, not selected from a squared
    polynomial with an unreviewed extra root.
    """
    if not same_fluid_isentropic_reservoir_reviewed:
        raise ValueError('same-fluid isentropic reservoir assumptions require review')
    if not isinstance(eos,IdealGasInternalEnergyEOS):raise TypeError('calorically perfect ideal-gas EOS required')
    if outward_normal not in (-1,1):raise ValueError('outward normal must be -1 or +1')
    p0=float(reservoir_pressure_pa_abs);t0=float(reservoir_stagnation_temperature_K)
    if not all(math.isfinite(x) and x>0 for x in (p0,t0)):raise ValueError('positive finite absolute reservoir pressure/temperature required')
    u=np.asarray(conservative,dtype=float)
    if u.shape!=(3,):raise ValueError('one conservative cell state required')
    rr,vv,pp,tt,aa,_=_recover(u[None,:],eos)
    rho,vi,pi,ti,ai=map(float,(rr[0],outward_normal*vv[0],pp[0],tt[0],aa[0]))
    gamma=eos.gamma;R=eos.specific_gas_constant_J_kg_K;cp=gamma*R/(gamma-1)
    if vi<=-ai:raise ValueError('supersonic interior inflow is outside the subsonic reservoir contract')
    if vi>=ai:return evaluate_pressure_outflow_face(u,eos,p0,outward_normal=outward_normal)
    b=(gamma-1)/(gamma+1)
    def wave(p):
        if p<=pi:return 2*ai/(gamma-1)*math.expm1((gamma-1)/(2*gamma)*math.log(p/pi))
        return (p-pi)*math.sqrt(2/((gamma+1)*rho)/(p+b*pi))
    residual_zero=wave(p0)-vi
    if residual_zero<=0:
        # The existing expansion/sonic owner remains the selected outflow law.
        if p0<=pi:return evaluate_pressure_outflow_face(u,eos,p0,outward_normal=outward_normal)
        velocity=vi-wave(p0)
        ratio=p0/pi;density=rho*(ratio+b)/(b*ratio+1)
        temperature=p0/(density*R);sound=math.sqrt(gamma*R*temperature)
        return GasPressureOutflowFace(density,velocity,p0,temperature,sound,
            eos.internal_energy_from_temperature(temperature),'subsonic_compression_outflow')
    sonic=math.sqrt(2*(gamma-1)*cp*t0/(gamma+1))
    def reservoir(velocity):
        fraction=-velocity*velocity/(2*cp*t0)
        temperature=t0*(1+fraction)
        pressure=p0*math.exp(gamma/(gamma-1)*math.log1p(fraction))
        return pressure,temperature
    # Velocity coordinates remain resolved at reversal: the pressure decrement
    # is quadratic there and may round to zero while signed velocity is finite.
    def residual(velocity):return velocity+wave(reservoir(velocity)[0])-vi
    if residual(-sonic)>=0:
        raise ValueError('reservoir-choked inflow requires a sonic/supersonic boundary extension')
    velocity=brentq(residual,-sonic,0.,xtol=max(1e-300,sonic*2e-14),rtol=8e-15)
    pressure,temperature=reservoir(velocity);density=pressure/(R*temperature)
    sound=math.sqrt(gamma*R*temperature)
    if not -sound<velocity<0:raise ValueError('reservoir root left the unique subsonic inflow branch')
    if abs(residual(velocity))>1e-9*max(ai,abs(vi),sound):raise ValueError('reservoir wave matching residual unresolved')
    return GasPressureOutflowFace(density,velocity,pressure,temperature,sound,
        eos.internal_energy_from_temperature(temperature),'subsonic_same_fluid_reservoir_inflow')
