"""Current-state quasi-steady wall resistance for the liquid acoustic owner.

Viscosity is supplied at the declared local state. The acoustic owner does not
predict temperature, rheological memory or frictional heat transport.
"""
from dataclasses import dataclass
import math
from numbers import Real
from typing import Callable, Literal, Sequence
import numpy as np
from .models.hydraulics import LiquidHeadLoss, liquid_head_loss


@dataclass(frozen=True)
class LiquidMOCFrictionState:
    pipe_id: str
    time_s: float
    inner_diameter_m: float
    density_kg_m3: float
    chainage_m: tuple[float,...]
    pressure_pa_abs: tuple[float,...]
    velocity_m_s: tuple[float,...]

    def __post_init__(self):
        if not isinstance(self.pipe_id, str) or not self.pipe_id.strip():
            raise ValueError('nonempty pipe identity required')
        def finite(value):
            return not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value)
        if not finite(self.time_s):
            raise ValueError('finite time_s required')
        if any(not finite(x) or x <= 0 for x in (self.inner_diameter_m, self.density_kg_m3)):
            raise ValueError('finite positive diameter and density required')
        arrays = (self.chainage_m, self.pressure_pa_abs, self.velocity_m_s)
        if any(not isinstance(a, tuple) for a in arrays) or len({len(a) for a in arrays}) != 1 or len(self.chainage_m) < 3:
            raise ValueError('aligned immutable station arrays of length at least three required')
        if any(not finite(v) for a in arrays for v in a):
            raise ValueError('finite station, pressure and velocity values required')
        if any(p <= 0 for p in self.pressure_pa_abs):
            raise ValueError('positive absolute station pressures required')
        if any(b <= a for a, b in zip(self.chainage_m, self.chainage_m[1:])):
            raise ValueError('station chainage must strictly increase')


@dataclass(frozen=True)
class LiquidMOCQuasisteadyDarcy:
    kinematic_viscosity_provider: Callable[[LiquidMOCFrictionState], Sequence[float]]
    roughness_m: float = 0.
    transition_policy: Literal['withhold','use_declared_correlation'] = 'withhold'
    friction_method: Literal['Clamond','Colebrook','Haaland','Swamee_Jain_1976'] = 'Clamond'

    def __post_init__(self):
        if not callable(self.kinematic_viscosity_provider):raise TypeError('a local-state viscosity provider is required')
        if not math.isfinite(self.roughness_m) or self.roughness_m<0:raise ValueError('finite nonnegative roughness required')
        if self.transition_policy not in ('withhold','use_declared_correlation'):raise ValueError('unknown transition policy')
        if self.friction_method not in ('Clamond','Colebrook','Haaland','Swamee_Jain_1976'):raise ValueError('unknown Darcy method')

    def __call__(self,state:LiquidMOCFrictionState):
        if not isinstance(state,LiquidMOCFrictionState):raise TypeError('immutable liquid MOC state required')
        d=state.inner_diameter_m
        if not all(math.isfinite(x) and x>0 for x in (d,state.density_kg_m3)):raise ValueError('finite positive diameter and physical density required')
        n=len(state.velocity_m_s)
        if n<3 or len(state.chainage_m)!=n or len(state.pressure_pa_abs)!=n:raise ValueError('aligned MOC station states required')
        if not math.isfinite(state.time_s) or any(not math.isfinite(x) for x in (*state.chainage_m,*state.velocity_m_s)) or any(not math.isfinite(x) or x<=0 for x in state.pressure_pa_abs):raise ValueError('finite stations, speed and positive absolute pressure required')
        if self.roughness_m/d>.1:raise ValueError('relative roughness exceeds reviewed pipe domain')
        nu=np.array(self.kinematic_viscosity_provider(state),dtype=float,copy=True)
        if nu.shape!=(n,) or not np.all(np.isfinite(nu)) or np.any(nu<=0):raise ValueError('aligned finite positive kinematic viscosities required')
        area=math.pi*d*d/4
        rates=[]
        for v,viscosity in zip(state.velocity_m_s,nu,strict=True):
            re=abs(v)*d/float(viscosity)
            if not math.isfinite(re) or (v!=0 and re==0):raise ValueError('Reynolds state outside finite support')
            if re<2300:
                # Continuous laminar limit of 64/Re times |v|/(2D).
                rate=32*float(viscosity)/d**2
            else:
                result=liquid_head_loss(LiquidHeadLoss(diameter_m=d,length_m=1.,roughness_m=self.roughness_m,
                    volume_flow_m3_s=v*area,kinematic_viscosity_m2_s=float(viscosity),
                    transition_policy=self.transition_policy,friction_method=self.friction_method))
                if result.status!='computed' or not result.values:raise ValueError('local Darcy closure withheld: '+str(result.notes))
                rate=result.values['darcy_friction_factor']*abs(v)/(2*d)
            if not math.isfinite(rate) or rate<0:raise ValueError('momentum decay exceeds finite support')
            rates.append(rate)
        return tuple(rates)
