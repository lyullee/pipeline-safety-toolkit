"""Quasi-steady ideal-gas restriction coupled to a pipe acoustic boundary.

The existing relief-area owner supplies nozzle capacity. Its rounded dimensional
constants are normalized to exact SI ideal-gas units to avoid a fictitious jump
at the sonic transition. No standard/device certification is implied.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import TYPE_CHECKING
import numpy as np
from pydantic import Field,model_validator
from scipy.constants import R as UNIVERSAL_GAS_CONSTANT
from scipy.optimize import brentq
from .models.library_models import ReliefGas,relief_gas
from .contracts import Contract,Positive
if TYPE_CHECKING:
    from .gas_acoustic_pipe import GasPressureOutflowFace,GasAcousticPipeResult,GasAcousticPipeSettings,IdealGasInternalEnergyEOS


@dataclass(frozen=True)
class RestrictedIdealVentFace:
    pipe_face: GasPressureOutflowFace
    mass_flow_kg_s: float
    throat_regime: str
    stagnation_pressure_pa_abs: float
    stagnation_temperature_K: float
    raw_relief_capacity_kg_s: float
    exact_SI_constant_normalization: float
    relative_capacity_residual: float


def ideal_vent_capacity(*,stagnation_pressure_pa_abs,stagnation_temperature_K,ambient_pressure_pa_abs,
    vent_area_m2,discharge_coefficient,eos):
    """Reuse the relief sizing shape as mass capacity for a supplied geometric area.

    Z=1 and both additional correction factors are one. The two SI conversion
    normalizations only remove the dimensional constants' rounding (0.03948,
    17.9 in the upstream Fluids implementation), not an empirical flow error.
    """
    from .gas_acoustic_pipe import IdealGasInternalEnergyEOS
    from fluids.compressible import is_critical_flow
    if not isinstance(eos,IdealGasInternalEnergyEOS):raise ValueError('restricted vent currently requires reviewed calorically perfect ideal gas')
    p,t,pb,area,cd=map(float,(stagnation_pressure_pa_abs,stagnation_temperature_K,ambient_pressure_pa_abs,vent_area_m2,discharge_coefficient))
    if not all(math.isfinite(v) and v>0 for v in (p,t,pb,area,cd)) or cd>1:raise ValueError('positive finite ideal-vent inputs and Cd<=1 required')
    if p<=pb:return 0.,'no_outflow',0.,1.
    molecular_mass=UNIVERSAL_GAS_CONSTANT/eos.specific_gas_constant_J_kg_K
    sized=relief_gas(ReliefGas(mass_flow_kg_s=1.,temperature_k=t,compressibility=1.,molar_mass_kg_mol=molecular_mass,
        heat_capacity_ratio=eos.gamma,relieving_pressure_pa_abs=p,back_pressure_pa_abs=pb,
        discharge_coefficient=cd,back_pressure_factor=1.,combination_factor=1.))
    raw=area/sized.values['required_area_m2']
    critical=is_critical_flow(p,pb,eos.gamma)
    normalization=(3.6/(.03948*math.sqrt(1000*UNIVERSAL_GAS_CONSTANT)) if critical else
        (17.9*3.6)*math.sqrt(2/(1000*UNIVERSAL_GAS_CONSTANT)))
    return raw*normalization,('choked' if critical else 'subcritical'),raw,normalization


def evaluate_restricted_ideal_vent_face(conservative,eos,*,pipe_area_m2,vent_area_m2,
    discharge_coefficient,ambient_pressure_pa_abs,outward_normal=1):
    """Match outgoing pipe invariant to one-way nozzle mass capacity.

    Pipe-face entropy is inherited from the pipe. Total enthalpy determines the
    nozzle's stagnation temperature and pressure; a static-cell pressure is not
    silently substituted for stagnation pressure. Only small restrictions and
    subsonic pipe states are supported; no nozzle interior or external jet is
    resolved. At/above ambient no reverse-reservoir state is manufactured.
    """
    from .gas_acoustic_pipe import IdealGasInternalEnergyEOS,GasPressureOutflowFace,evaluate_pressure_outflow_face
    if not isinstance(eos,IdealGasInternalEnergyEOS):raise ValueError('restricted vent requires the explicit ideal-gas EOS')
    if outward_normal not in (-1,1):raise ValueError('normal must be -1 or +1')
    area=float(pipe_area_m2);vent=float(vent_area_m2);cd=float(discharge_coefficient);pb=float(ambient_pressure_pa_abs)
    if not all(math.isfinite(x) and x>0 for x in (area,vent,cd,pb)) or cd>1 or vent/area>.25:
        raise ValueError('reviewed vent requires0<area/pipe_area<=0.25 and0<Cd<=1')
    u=np.asarray(conservative,dtype=float)
    if u.shape!=(3,) or not np.isfinite(u).all() or u[0]<=0:raise ValueError('finite conservative cell state required')
    rho=float(u[0]);velocity=outward_normal*float(u[1]/rho);energy=float(u[2]/rho-.5*velocity**2)
    state=eos.evaluate(rho,energy);p=state.pressure_pa_abs;t=state.temperature_K;a=state.sound_speed_m_s;k=eos.gamma
    if abs(velocity)/a>=.3:raise ValueError('restricted vent pipe state exceeds reviewed subsonic Mach0.3 support')
    invariant=velocity+2*a/(k-1)
    closed_a=(k-1)*invariant/2
    if closed_a<=0:raise ValueError('no positive stagnating characteristic state')
    closed_pressure=p*(closed_a/a)**(2*k/(k-1))
    def point(pressure):
        af=a*(pressure/p)**((k-1)/(2*k));v=invariant-2*af/(k-1)
        if v < -1e-10:raise ValueError('no reverse-reservoir flow state is supplied')
        if v<0:v=0. # Roundoff at the exactly stagnating characteristic bound.
        rf=rho*(pressure/p)**(1/k);tf=af*af/(k*eos.specific_gas_constant_J_kg_K)
        cp=k*eos.cv_J_kg_K;t0=tf+v*v/(2*cp);p0=pressure*(t0/tf)**(k/(k-1))
        flow,regime,raw,normalization=ideal_vent_capacity(stagnation_pressure_pa_abs=p0,stagnation_temperature_K=t0,
            ambient_pressure_pa_abs=pb,vent_area_m2=vent,discharge_coefficient=cd,eos=eos)
        return rf,v,tf,af,p0,t0,flow,regime,raw,normalization
    if closed_pressure<=pb:
        pressure=closed_pressure;rf,v,tf,af,p0,t0,flow,regime,raw,normalization=point(pressure)
        v=flow=raw=0.;regime='no_outflow'
    else:
        # The full-bore face bounds a small-restriction solution from below.
        open_face=evaluate_pressure_outflow_face(u,eos,pb,outward_normal=outward_normal)
        low=open_face.pressure_pa_abs;high=closed_pressure
        def residual(pressure):
            rf,v,_,_,_,_,flow,*_=point(pressure)
            return area*rf*v-flow
        if residual(low)<0:raise ValueError('restriction capacity exceeds classical pipe characteristic support')
        pressure=brentq(residual,low,high,xtol=max(1e-8,p*2e-13),rtol=1e-13)
        rf,v,tf,af,p0,t0,flow,regime,raw,normalization=point(pressure)
    actual=area*rf*v;relative=abs(actual-flow)/max(1e-20,actual,flow)
    if relative>2e-8:raise ValueError('nozzle/pipe mass-capacity match did not converge')
    face=GasPressureOutflowFace(rf,v,pressure,tf,af,eos.internal_energy_from_temperature(tf),'subsonic')
    return RestrictedIdealVentFace(face,actual,regime,p0,t0,raw,normalization,relative)


class RestrictedVentBlowdownSpec(Contract):
    geometric_vent_area_m2: Positive
    discharge_coefficient: Positive = Field(le=1)
    ambient_pressure_pa_abs: Positive
    target_maximum_pipe_pressure_pa_abs: Positive
    quasisteady_ideal_small_vent_and_one_way_support_reviewed: bool = False

    @model_validator(mode='after')
    def explicit_endpoint(self):
        if self.target_maximum_pipe_pressure_pa_abs<=self.ambient_pressure_pa_abs:
            raise ValueError('finite-horizon evacuation target must exceed ambient; no time-to-empty estimate is inferred')
        return self


@dataclass(frozen=True)
class RestrictedPipeBlowdownResult:
    core: GasAcousticPipeResult
    target_reached: bool
    elapsed_stop_time_s: float
    remaining_mass_kg: float
    discharged_mass_kg: float
    final_vent_face: RestrictedIdealVentFace | None
    assumptions: tuple[str,...]


def solve_ideal_restricted_pipe_blowdown(initial_conservative,settings:GasAcousticPipeSettings,eos:IdealGasInternalEnergyEOS,
    vent:RestrictedVentBlowdownSpec,*,opening_time_s=None):
    """One isolated pipe, closed upstream and a supplied small downstream vent.

    The original nonlinear mass/momentum/energy FV solver remains the only time
    integrator. A pressure-threshold event refines its accepted SSPRK2 step with
    matching boundary/source ledgers. A requested finite horizon may end before
    the target; this is returned explicitly instead of inventing emptying time.
    """
    from .gas_acoustic_pipe import GasAcousticPipeSettings,GasPipeBoundary,GasPipeBoundaryStage,solve_gas_acoustic_pipe
    v=RestrictedVentBlowdownSpec.model_validate(vent.model_dump(mode='python'))
    if not v.quasisteady_ideal_small_vent_and_one_way_support_reviewed:raise ValueError('ideal small-vent, one-way and quasi-steady nozzle support requires review')
    q=GasAcousticPipeSettings.model_validate(settings.model_dump(mode='python'))
    if q.stop_when_maximum_pressure_below_pa_abs not in (None,v.target_maximum_pipe_pressure_pa_abs):
        raise ValueError('numerical and physical pressure-stop specifications disagree')
    q=q.model_copy(update={'stop_when_maximum_pressure_below_pa_abs':v.target_maximum_pipe_pressure_pa_abs})
    opening=q.start_time_s if opening_time_s is None else float(opening_time_s)
    if not math.isfinite(opening) or not q.start_time_s<=opening<=q.start_time_s+q.duration_s:raise ValueError('vent opening must lie in the finite solve window')
    closed=GasPipeBoundary(kind='reflective')
    opened=GasPipeBoundary(kind='restricted_ideal_vent',ambient_pressure_pa_abs=v.ambient_pressure_pa_abs,
        vent_area_m2=v.geometric_vent_area_m2,vent_discharge_coefficient=v.discharge_coefficient,
        quasisteady_ideal_nozzle_support_reviewed=True)
    stages=[GasPipeBoundaryStage(start_time_s=q.start_time_s,left=closed,right=opened if opening==q.start_time_s else closed)]
    if opening>q.start_time_s:stages.append(GasPipeBoundaryStage(start_time_s=opening,left=closed,right=opened))
    core=solve_gas_acoustic_pipe(initial_conservative,q,eos=eos,boundary_stages=tuple(stages))
    final=None
    if core.step_times_s[-1]>opening:
        final=evaluate_restricted_ideal_vent_face(core.conservative[-1],eos,pipe_area_m2=math.pi*q.inner_diameter_m**2/4,
            vent_area_m2=v.geometric_vent_area_m2,discharge_coefficient=v.discharge_coefficient,ambient_pressure_pa_abs=v.ambient_pressure_pa_abs)
    return RestrictedPipeBlowdownResult(core,core.termination['threshold_reached'],core.step_times_s[-1]-q.start_time_s,
        float(core.ledger.final_mass_momentum_energy[0]),float(core.ledger.initial_mass_momentum_energy[0]-core.ledger.final_mass_momentum_energy[0]),final,
        ('Ideal calorically perfect single gas, explicitly supplied Cd and geometric vent/pipe area<=0.25.',
         'Closed upstream, one-way quasi-steady isentropic downstream nozzle; no resolved nozzle interior, reverse reservoir or external jet.',
         'The target is maximum cell pressure above ambient, not zero inventory. Finite-horizon failure to reach it remains explicit.',
         'Original conservative pipe FV and supplied heat/friction/gravity sources are retained; no historical empirical-duration equivalence is claimed.'))
