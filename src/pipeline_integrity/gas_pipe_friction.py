"""Quasisteady single-phase Darcy drag from current local thermodynamic states.

This is local instantaneous wall resistance, not frequency-dependent wall memory,
resolved turbulence, developing flow or a two-phase constitutive closure.
"""
from dataclasses import dataclass
from typing import Callable,Literal,Sequence
import math
import numpy as np
from .gas_acoustic_pipe import GasPipeLocalState


@dataclass(frozen=True)
class StateDependentDarcyDecay:
    dynamic_viscosity_provider: Callable[[GasPipeLocalState],Sequence[float]]
    roughness_m: float | tuple[float,...] = 0.
    transition_policy: Literal['withhold','use_declared_correlation'] = 'withhold'
    friction_method: Literal['Clamond','Colebrook','Haaland','Swamee_Jain_1976'] = 'Clamond'

    def __post_init__(self):
        if not callable(self.dynamic_viscosity_provider):raise TypeError('a deterministic state-to-dynamic-viscosity provider is required')
        if self.transition_policy not in ('withhold','use_declared_correlation'):raise ValueError('unknown transitional-flow policy')
        if self.friction_method not in ('Clamond','Colebrook','Haaland','Swamee_Jain_1976'):raise ValueError('unknown Darcy friction correlation')
        rr=np.asarray(self.roughness_m,dtype=float)
        if rr.ndim>1 or not np.all(np.isfinite(rr)) or np.any(rr<0):raise ValueError('roughness requires finite nonnegative metres')
        object.__setattr__(self,'roughness_m',float(rr) if rr.ndim==0 else tuple(map(float,rr)))

    def __call__(self,state:GasPipeLocalState):
        from fluids.friction import friction_factor
        if not isinstance(state,GasPipeLocalState):raise TypeError('immutable local gas-pipe state required')
        d=state.inner_diameter_m
        if not math.isfinite(d) or d<=0:raise ValueError('positive finite hydraulic diameter required')
        rho=np.asarray(state.density_kg_m3);v=np.asarray(state.velocity_m_s);n=len(rho)
        pressure=np.asarray(state.pressure_pa_abs);temperature=np.asarray(state.temperature_K)
        if not math.isfinite(state.time_s) or pressure.shape!=(n,) or temperature.shape!=(n,) or not np.all(np.isfinite(pressure)) or not np.all(np.isfinite(temperature)) or np.any(pressure<=0) or np.any(temperature<=0):
            raise ValueError('aligned positive finite absolute pressure and temperature are required')
        mu=np.asarray(self.dynamic_viscosity_provider(state),dtype=float)
        if mu.shape!=(n,) or not np.all(np.isfinite(mu)) or np.any(mu<=0):raise ValueError('viscosity provider requires aligned positive finite Pa*s values')
        if v.shape!=(n,) or not np.all(np.isfinite(rho)) or np.any(rho<=0) or not np.all(np.isfinite(v)):raise ValueError('finite aligned density and velocity required')
        rough=np.asarray(self.roughness_m,dtype=float)
        if rough.ndim==0:rough=np.full(n,float(rough))
        if rough.shape!=(n,) or np.any(rough/d>.1):raise ValueError('aligned relative roughness within 0..0.1 required')
        nu=mu/rho;re=np.abs(v)*d/nu
        if not np.all(np.isfinite(nu)) or np.any(nu<=0) or not np.all(np.isfinite(re)):raise ValueError('viscosity/Reynolds state exceeds finite support')
        if np.any((v!=0)&(re==0)):raise ValueError('nonzero Reynolds state underflow is not zero flow')
        if self.transition_policy=='withhold' and np.any((re>=2300)&(re<4000)):raise ValueError('transitional flow requires an explicitly reviewed correlation policy')
        # Algebraic laminar limit avoids the singular 64/Re factor at rest.
        rate=32*nu/d**2
        for i in np.flatnonzero(re>=2300):rate[i]=friction_factor(float(re[i]),float(rough[i]/d),Method=self.friction_method,Darcy=True)*abs(v[i])/(2*d)
        if not np.all(np.isfinite(rate)) or np.any(rate<0):raise ValueError('Darcy momentum decay exceeds finite nonnegative support')
        return tuple(map(float,rate))
