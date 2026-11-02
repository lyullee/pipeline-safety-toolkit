"""Conservative nonlinear gas/supercritical fluid in one rigid constant-area pipe.

Three finite-volume fields retain mass, axial momentum and total fluid energy.
Rusanov interfaces and SSPRK2 time stepping use piecewise-constant cell states;
shocks/contacts are therefore diffusive. This is neither a phase-interface,
resolved variable-area nozzle interior, elastic pipe-wall nor network junction solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Callable, Literal, Protocol, Sequence

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Nonnegative, Positive


@dataclass(frozen=True)
class GasThermodynamicPoint:
    pressure_pa_abs: float
    temperature_K: float
    sound_speed_m_s: float
    cv_J_kg_K: float
    phase: Literal["gas","supercritical_gas","supercritical"]


@dataclass(frozen=True)
class GasPressureOutflowFace:
    density_kg_m3: float
    outward_velocity_m_s: float
    pressure_pa_abs: float
    temperature_K: float
    sound_speed_m_s: float
    internal_energy_J_kg: float
    regime: Literal['subsonic','sonic','supersonic_extrapolated']

    @property
    def outward_mach_number(self):
        return self.outward_velocity_m_s/self.sound_speed_m_s


class GasInternalEnergyEOS(Protocol):
    def evaluate(self, density_kg_m3: float, internal_energy_J_kg: float) -> GasThermodynamicPoint: ...


@dataclass(frozen=True)
class GasPipeLocalState:
    """Immutable aligned cell observations for a deterministic constitutive callback."""
    time_s: float
    inner_diameter_m: float
    density_kg_m3: tuple[float,...]
    velocity_m_s: tuple[float,...]
    pressure_pa_abs: tuple[float,...]
    temperature_K: tuple[float,...]


GasMomentumDecayRateProvider = Callable[[GasPipeLocalState], Sequence[float]]


@dataclass(frozen=True)
class IdealGasInternalEnergyEOS:
    specific_gas_constant_J_kg_K: float = 287.05
    gamma: float = 1.4
    internal_energy_reference_J_kg: float = 0.0

    def __post_init__(self):
        if (not all(not isinstance(x, bool) and isinstance(x, Real) and math.isfinite(x) for x in (self.specific_gas_constant_J_kg_K,self.gamma,
                                              self.internal_energy_reference_J_kg))
                or self.specific_gas_constant_J_kg_K <= 0 or self.gamma <= 1):
            raise ValueError("finite R>0, gamma>1 and a finite energy reference required")

    @property
    def cv_J_kg_K(self):
        return self.specific_gas_constant_J_kg_K/(self.gamma-1)

    def internal_energy_from_temperature(self, temperature_K: float) -> float:
        if not math.isfinite(temperature_K) or temperature_K <= 0:
            raise ValueError("positive finite absolute temperature required")
        return self.cv_J_kg_K*temperature_K+self.internal_energy_reference_J_kg

    def evaluate(self, density_kg_m3, internal_energy_J_kg):
        rho=float(density_kg_m3);e=float(internal_energy_J_kg)
        t=(e-self.internal_energy_reference_J_kg)/self.cv_J_kg_K
        if not all(math.isfinite(x) for x in (rho,e,t)) or rho <= 0 or t <= 0:
            raise ValueError("ideal-gas state is outside positive density/temperature support")
        p=rho*self.specific_gas_constant_J_kg_K*t
        return GasThermodynamicPoint(p,t,math.sqrt(self.gamma*p/rho),self.cv_J_kg_K,"gas")


class CoolPropGasInternalEnergyEOS:
    """One serial low-level state object; do not share across concurrent solves.

    A declared energy offset may be added to the native CoolProp reference, so
    negative input energies remain valid without changing global reference states.
    No phase is imposed to manufacture a metastable gas flash.
    Gas and explicitly single-phase supercritical states are supported. Liquid,
    supercritical-liquid and two-phase states are outside the selected domain.
    """
    def __init__(self, fluid: str, backend: str = "HEOS", *, energy_reference_offset_J_kg: float = 0.0):
        import CoolProp
        from CoolProp import AbstractState
        if not math.isfinite(energy_reference_offset_J_kg):
            raise ValueError("finite energy reference offset required")
        self.fluid=fluid;self.backend=backend;self._constants=CoolProp
        self.energy_reference_offset_J_kg=energy_reference_offset_J_kg
        self._state=AbstractState(backend,fluid)
        self._state.update(CoolProp.PT_INPUTS,1e5,300.)
        self.reference_anchor={"pressure_pa_abs":1e5,"temperature_K":300.,
            "density_kg_m3":self._state.rhomass(),"internal_energy_J_kg":self._state.umass()+energy_reference_offset_J_kg,
            "enthalpy_J_kg":self._state.hmass()+energy_reference_offset_J_kg,"entropy_J_kg_K":self._state.smass()}

    def evaluate(self, density_kg_m3, internal_energy_J_kg):
        rho=float(density_kg_m3);e=float(internal_energy_J_kg)
        if not math.isfinite(rho) or rho <= 0 or not math.isfinite(e):
            raise ValueError("finite rho>0 and specific internal energy required")
        cp=self._constants
        self._state.update(cp.DmassUmass_INPUTS,rho,e-self.energy_reference_offset_J_kg)
        phases={cp.iphase_gas:"gas",cp.iphase_supercritical_gas:"supercritical_gas",cp.iphase_supercritical:"supercritical"}
        if self._state.phase() not in phases:
            raise ValueError("EOS did not resolve a supported single-phase gas state")
        point=GasThermodynamicPoint(self._state.p(),self._state.T(),self._state.speed_sound(),self._state.cvmass(),phases[self._state.phase()])
        if not all(math.isfinite(x) and x>0 for x in (point.pressure_pa_abs,point.temperature_K,point.sound_speed_m_s,point.cv_J_kg_K)):
            raise ValueError("EOS gas outputs must be finite and positive")
        return point


class GasPipeBoundary(Contract):
    kind: Literal["periodic","reflective","extrapolated","reservoir","pressure_outflow","restricted_ideal_vent","ideal_same_fluid_open"]
    reservoir_conservative: tuple[float,float,float] | None = None
    ambient_pressure_pa_abs: Positive | None = None
    vent_area_m2: Positive | None = None
    vent_discharge_coefficient: Positive | None = Field(default=None,le=1)
    quasisteady_ideal_nozzle_support_reviewed: bool = False
    reservoir_stagnation_temperature_K: Positive | None = None
    same_fluid_isentropic_reservoir_reviewed: bool = False

    @model_validator(mode="after")
    def reservoir_shape(self):
        if (self.kind=="reservoir") != (self.reservoir_conservative is not None):
            raise ValueError("only reservoir boundaries require a conservative state")
        if (self.kind in ('pressure_outflow','restricted_ideal_vent','ideal_same_fluid_open')) != (self.ambient_pressure_pa_abs is not None):
            raise ValueError('only explicit outflow boundaries require an ambient pressure')
        bidirectional=self.kind=='ideal_same_fluid_open'
        if bidirectional != (self.reservoir_stagnation_temperature_K is not None) or bidirectional != self.same_fluid_isentropic_reservoir_reviewed:
            raise ValueError('same-fluid open boundary requires reviewed ideal reservoir and stagnation temperature')
        restricted=self.kind=='restricted_ideal_vent'
        if restricted != (self.vent_area_m2 is not None) or restricted != (self.vent_discharge_coefficient is not None):
            raise ValueError('only restricted ideal vents require geometric area and supplied Cd')
        if restricted != self.quasisteady_ideal_nozzle_support_reviewed:
            raise ValueError('restricted ideal nozzle assumptions require explicit review')
        return self


class GasPipeBoundaryStage(Contract):
    start_time_s: float
    left: GasPipeBoundary
    right: GasPipeBoundary

    @model_validator(mode="after")
    def paired_periodic(self):
        if (self.left.kind=="periodic") != (self.right.kind=="periodic"):
            raise ValueError("periodicity requires both ends")
        return self


class GasAcousticPipeSettings(Contract):
    length_m: Positive
    inner_diameter_m: Positive
    duration_s: Positive
    start_time_s: float = 0.0
    cfl: Positive = Field(default=0.45,le=0.5)
    maximum_step_s: Positive = 1e100
    minimum_retry_step_s: Positive = 1e-14
    maximum_steps: int = Field(default=200000,ge=1,le=10000000)
    maximum_positivity_retries: int = Field(default=30,ge=0,le=100)
    darcy_friction_factor: Nonnegative | tuple[Nonnegative,...] = 0.0
    signed_axial_gravity_m_s2: float = 0.0
    wall_heat_transfer_W_m2_K: Nonnegative | tuple[Nonnegative,...] = 0.0
    ambient_temperature_K: Positive | tuple[Positive,...] | None = None
    source_fraction_per_step: Positive = Field(default=0.2,le=0.5)
    observation_times_s: tuple[float,...] = Field(default=(),max_length=1000000)
    stop_when_maximum_pressure_below_pa_abs: Positive | None = None
    pressure_stop_tolerance_pa: Positive = .01

    @model_validator(mode="after")
    def finite_finish(self):
        finish=self.start_time_s+self.duration_s
        if not math.isfinite(finish) or finish <= self.start_time_s:
            raise ValueError("duration must advance a finite clock")
        if any(b<=a for a,b in zip(self.observation_times_s,self.observation_times_s[1:])):
            raise ValueError("observation times must increase strictly, without sorting or deduplication")
        if self.observation_times_s and (self.observation_times_s[0]<self.start_time_s or self.observation_times_s[-1]>finish):
            raise ValueError("observation times must lie within the solve window")
        return self


@dataclass(frozen=True)
class GasPipeConservationLedger:
    initial_mass_momentum_energy: np.ndarray
    final_mass_momentum_energy: np.ndarray
    integrated_boundary_inflow: np.ndarray
    integrated_distributed_sources: np.ndarray
    residual: np.ndarray


@dataclass(frozen=True)
class GasPipeObservation:
    time_s: float
    conservative: np.ndarray
    pressure_pa_abs: np.ndarray
    temperature_K: np.ndarray
    velocity_m_s: np.ndarray
    phase_by_cell: tuple[str,...]

    @property
    def density_kg_m3(self):
        return self.conservative[:,0]


@dataclass(frozen=True)
class GasAcousticPipeResult:
    cell_centers_m: np.ndarray
    conservative: np.ndarray
    pressure_pa_abs: np.ndarray
    temperature_K: np.ndarray
    velocity_m_s: np.ndarray
    sound_speed_m_s: np.ndarray
    step_times_s: tuple[float,...]
    boundary_event_times_s: tuple[float,...]
    accepted_steps: int
    rejected_steps: int
    ledger: GasPipeConservationLedger
    eos_identity: dict
    phase_by_cell: tuple[str,...]
    observations: tuple[GasPipeObservation,...]
    scheme: str = "piecewise_constant_Rusanov_SSPRK2_with_state_checked_retry"
    termination: dict | None = None
    momentum_decay_identity: dict | None = None


def gas_conservative_state(density_kg_m3,velocity_m_s,internal_energy_J_kg):
    """Broadcast rho, axial velocity and reference-dependent specific internal energy."""
    rho,v,e=np.broadcast_arrays(np.asarray(density_kg_m3,dtype=float),np.asarray(velocity_m_s,dtype=float),
                                np.asarray(internal_energy_J_kg,dtype=float))
    if not all(np.all(np.isfinite(x)) for x in (rho,v,e)) or np.any(rho<=0):
        raise ValueError("finite rho>0, velocity and internal energy required")
    result=np.stack((rho,rho*v,rho*(e+0.5*v*v)),axis=-1)
    if not np.all(np.isfinite(result)):
        raise ValueError("conservative state exceeds finite numerical support")
    return result


def _recover(u,eos,*,include_phases=False):
    if not np.all(np.isfinite(u)) or np.any(u[:,0]<=0):
        raise ValueError("nonpositive density or nonfinite conservative state")
    rho=u[:,0];v=u[:,1]/rho;e=u[:,2]/rho-0.5*v*v
    if isinstance(eos,IdealGasInternalEnergyEOS):
        t=(e-eos.internal_energy_reference_J_kg)/eos.cv_J_kg_K
        p=rho*eos.specific_gas_constant_J_kg_K*t
        a=np.sqrt(np.maximum(0,eos.gamma*p/rho));cv=np.full_like(rho,eos.cv_J_kg_K)
    else:
        states=[eos.evaluate(float(r),float(energy)) for r,energy in zip(rho,e)]
        if any(not isinstance(s,GasThermodynamicPoint) for s in states):
            raise TypeError("EOS must return GasThermodynamicPoint")
        if any(s.phase not in ("gas","supercritical_gas","supercritical") for s in states):
            raise ValueError("injected EOS left the selected gas/supercritical domain")
        p,t,a,cv=(np.array([getattr(s,key) for s in states]) for key in
                  ("pressure_pa_abs","temperature_K","sound_speed_m_s","cv_J_kg_K"))
    if any(not np.all(np.isfinite(x)) or np.any(x<=0) for x in (p,t,a,cv)):
        raise ValueError("EOS recovery left positive finite p/T/sound-speed/cv support")
    result=(rho,v,p,t,a,cv)
    if include_phases:
        result+=(tuple("gas" for _ in rho) if isinstance(eos,IdealGasInternalEnergyEOS) else tuple(s.phase for s in states),)
    return result


def evaluate_pressure_outflow_face(conservative,eos,ambient_pressure_pa_abs,*,outward_normal=1) -> GasPressureOutflowFace:
    """Classical outgoing-characteristic pressure boundary, with sonic limiting.

    Entropy and the outgoing acoustic invariant are inherited from the interior.
    Subsonic flow takes the ambient pressure; at a sonic edge the interior
    characteristic selects pressure and the downstream ambient cannot prescribe
    it. This is a full-bore open edge, not a discharge-coefficient valve/nozzle.
    Sustained inflow needs reservoir temperature/composition data and is rejected.
    """
    if outward_normal not in (-1,1):raise ValueError('outward normal must be -1 or +1')
    pb=float(ambient_pressure_pa_abs)
    if not math.isfinite(pb) or pb<=0:raise ValueError('positive finite ambient pressure required')
    state=np.asarray(conservative,dtype=float)
    if state.shape!=(3,):raise ValueError('one conservative cell state required')
    rho,velocity,p,t,a,_=_recover(np.array([state]),eos)
    ri,ui,pi,ti,ai=map(float,(rho[0],outward_normal*velocity[0],p[0],t[0],a[0]))
    ei=float(state[2]/ri-.5*velocity[0]**2)
    if ui>=ai:
        if pb>pi:raise ValueError('supersonic outflow against increased ambient pressure needs a shock boundary model')
        return GasPressureOutflowFace(ri,ui,pi,ti,ai,ei,'supersonic_extrapolated')
    if isinstance(eos,IdealGasInternalEnergyEOS):
        gamma=eos.gamma;invariant=ui+2*ai/(gamma-1)
        sonic_a=(gamma-1)/(gamma+1)*invariant
        if sonic_a<=0:raise ValueError('interior state cannot feed a classical outward rarefaction')
        sonic_pressure=pi*(sonic_a/ai)**(2*gamma/(gamma-1))
        if pb<=sonic_pressure:
            ab=sonic_a;pressure=sonic_pressure;ub=ab;regime='sonic'
        else:
            pressure=pb;ab=ai*(pb/pi)**((gamma-1)/(2*gamma))
            ub=invariant-2*ab/(gamma-1);regime='subsonic'
        rb=ri*(pressure/pi)**(1/gamma);tb=ab*ab/(gamma*eos.specific_gas_constant_J_kg_K)
        eb=eos.internal_energy_from_temperature(tb)
    elif isinstance(eos,CoolPropGasInternalEnergyEOS):
        from scipy.integrate import quad
        from scipy.optimize import brentq
        eos.evaluate(ri,ei);entropy=eos._state.smass();cache={}
        def isentrope(pressure):
            if pressure not in cache:
                eos._state.update(eos._constants.PSmass_INPUTS,pressure,entropy)
                phases=(eos._constants.iphase_gas,eos._constants.iphase_supercritical_gas,eos._constants.iphase_supercritical)
                if eos._state.phase() not in phases:raise ValueError('pressure boundary isentrope left the selected single-gas domain')
                fundamental=eos._state.fundamental_derivative_of_gas_dynamics()
                if not math.isfinite(fundamental) or fundamental<=0:
                    raise ValueError('classical pressure outflow requires positive fundamental gas-dynamic derivative')
                cache[pressure]=(eos._state.rhomass(),eos._state.T(),eos._state.speed_sound(),
                    eos._state.umass()+eos.energy_reference_offset_J_kg)
            return cache[pressure]
        def face(pressure):
            rb,tb,ab,eb=isentrope(pressure)
            def integrand(log_pressure):
                pp=math.exp(log_pressure);rr,_,aa,_=isentrope(pp)
                return pp/(rr*aa)
            integral=quad(integrand,math.log(pi),math.log(pressure),epsabs=1e-8,epsrel=1e-10)[0]
            return rb,ui-integral,pressure,tb,ab,eb
        if pb>=pi:
            rb,ub,pressure,tb,ab,eb=face(pb);regime='subsonic'
        else:
            high=pi
            for _ in range(100):
                low=max(pb,high*.85)
                point=face(low)
                if point[1]>=point[4]:
                    pressure=brentq(lambda pp:face(pp)[1]-face(pp)[4],low,high,xtol=max(1e-8,pi*1e-12),rtol=1e-12)
                    rb,ub,pressure,tb,ab,eb=face(pressure);regime='sonic';break
                if low==pb:
                    rb,ub,pressure,tb,ab,eb=point;regime='subsonic';break
                high=low
            else:raise ValueError('sonic search did not reach pressure boundary support')
    else:
        raise ValueError('pressure outflow needs an explicit ideal-gas or supported CoolProp isentrope; injected rho/e recovery alone is insufficient')
    if not all(math.isfinite(x) for x in (rb,ub,pressure,tb,ab,eb)) or min(rb,pressure,tb,ab)<=0:
        raise ValueError('pressure outflow face left finite positive thermodynamic support')
    if ub<0:raise ValueError('pressure boundary requests inflow; reservoir entropy/temperature is not supplied')
    return GasPressureOutflowFace(rb,ub,pressure,tb,ab,eb,regime)


def _coefficient(value,n,name):
    a=np.asarray(value,dtype=float)
    if a.ndim==0:a=np.full(n,float(a))
    if a.shape!=(n,) or not np.all(np.isfinite(a)) or np.any(a<0):
        raise ValueError(name+" must be finite nonnegative scalar or one value per cell")
    return a


def _observation(time,state,eos):
    _,velocity,pressure,temperature,_,_,phases=_recover(state,eos,include_phases=True)
    arrays=[np.array(a,copy=True) for a in (state,pressure,temperature,velocity)]
    for array in arrays:array.setflags(write=False)
    return GasPipeObservation(time,*arrays,phases)


def solve_gas_acoustic_pipe(initial_conservative,settings:GasAcousticPipeSettings,*,eos:GasInternalEnergyEOS,
                            boundary_stages:tuple[GasPipeBoundaryStage,...] | None=None,
                            momentum_decay_rate_provider:GasMomentumDecayRateProvider | None=None) -> GasAcousticPipeResult:
    """Integrate one finite-volume pipe, returning SI inventories and flux/source budgets.

    Stored energy includes fluid internal and axial kinetic energy, excluding
    gravitational potential. Gravity therefore performs explicit fluid work.
    Stationary adiabatic-wall drag removes momentum but no total fluid energy;
    resolved kinetic energy becomes internal energy. Prescribed ambient wall
    exchange is the only wall energy source/sink. No wall heat capacity is modeled.
    Reservoir ghosts are prescribed conservative states, not a subsonic boundary
    characteristic compatibility solver or a valve/orifice law.
    Explicit pressure-outflow edges instead use outgoing-characteristic entropy
    compatibility with sonic limiting and a conservative physical face flux.

    Optional observation times are exact step endpoints with independent read-only
    snapshot arrays. At a simultaneous boundary switch, the snapshot is the
    continuous cell state after the preceding stage; the new ghost condition acts
    on subsequent positive-duration steps. No every-step field history is stored.

    An optional deterministic momentum-decay provider returns one finite rate in
    1/s per cell, evaluated from immutable local states at each SSPRK stage and
    source-step bound. Its momentum source is -rho*velocity*rate. It replaces
    fixed Darcy drag, so all fixed friction factors must be zero. Stationary-wall
    drag converts kinetic to internal energy without subtracting total energy.
    """
    if not isinstance(settings,GasAcousticPipeSettings):
        raise TypeError("GasAcousticPipeSettings required")
    if not callable(getattr(eos,"evaluate",None)):
        raise TypeError("EOS must implement evaluate(rho,specific_internal_energy)")
    q=GasAcousticPipeSettings.model_validate(settings.model_dump(mode="python"))
    u=np.array(initial_conservative,dtype=float,copy=True)
    if u.ndim!=2 or u.shape[1]!=3 or not 2<=len(u)<=1000000:
        raise ValueError("initial state requires 2..1000000 cell rows (rho,momentum,total energy)")
    _recover(u,eos)
    n=len(u);dx=q.length_m/n;area=math.pi*q.inner_diameter_m**2/4
    if not math.isfinite(area) or area<=0 or not math.isfinite(dx) or dx<=0:
        raise ValueError("pipe geometry exceeds finite positive numerical support")
    friction=_coefficient(q.darcy_friction_factor,n,"Darcy friction factor")
    if momentum_decay_rate_provider is not None:
        if not callable(momentum_decay_rate_provider):raise TypeError('momentum decay provider must be callable')
        if np.any(friction!=0):raise ValueError('state-dependent momentum decay and fixed Darcy drag are mutually exclusive')
    heat=_coefficient(q.wall_heat_transfer_W_m2_K,n,"wall heat transfer")
    ambient=None if q.ambient_temperature_K is None else _coefficient(q.ambient_temperature_K,n,"ambient temperature")
    if np.any(heat>0) and (ambient is None or np.any(ambient<=0)):
        raise ValueError("positive heat transfer requires positive ambient temperature per cell")
    if boundary_stages is None:
        end=GasPipeBoundary(kind="extrapolated")
        stages=(GasPipeBoundaryStage(start_time_s=q.start_time_s,left=end,right=end),)
    else:
        stages=tuple(GasPipeBoundaryStage.model_validate(s.model_dump(mode="python")) for s in boundary_stages)
    finish=q.start_time_s+q.duration_s
    if not stages or stages[0].start_time_s!=q.start_time_s:
        raise ValueError("first boundary stage must start at the solver start time")
    if any(b.start_time_s<=a.start_time_s for a,b in zip(stages,stages[1:])) or stages[-1].start_time_s>finish:
        raise ValueError("boundary events must increase strictly within the solve window")
    for stage in stages:
        for boundary in (stage.left,stage.right):
            if boundary.kind=="reservoir":_recover(np.asarray([boundary.reservoir_conservative]),eos)
            if boundary.kind=='pressure_outflow' and not isinstance(eos,(IdealGasInternalEnergyEOS,CoolPropGasInternalEnergyEOS)):
                raise ValueError('pressure outflow requires a supported isentropic boundary EOS')
            if boundary.kind=='ideal_same_fluid_open' and not isinstance(eos,IdealGasInternalEnergyEOS):
                raise ValueError('same-fluid open boundary currently requires calorically perfect ideal gas')
            if boundary.kind=='restricted_ideal_vent':
                if not isinstance(eos,IdealGasInternalEnergyEOS) or boundary.vent_area_m2/area>.25:
                    raise ValueError('restricted vent requires ideal gas and geometric vent/pipe area<=0.25')

    def rates(rho,v,p,t,a,cv,clock):
        if momentum_decay_rate_provider is None:decay=friction*np.abs(v)/(2*q.inner_diameter_m)
        else:
            local=GasPipeLocalState(float(clock),float(q.inner_diameter_m),tuple(map(float,rho)),tuple(map(float,v)),tuple(map(float,p)),tuple(map(float,t)))
            decay=np.asarray(momentum_decay_rate_provider(local),dtype=float)
            if decay.shape!=(n,) or not np.all(np.isfinite(decay)) or np.any(decay<0):
                raise ValueError('momentum decay provider requires one aligned finite nonnegative rate in 1/s per cell')
        bound=decay+4*heat/(q.inner_diameter_m*rho*cv)
        bound+=abs(q.signed_axial_gravity_m_s2)/np.maximum(a,np.abs(v))
        return decay,float(np.max(bound))

    def rhs(state,stage,clock):
        rho,v,p,t,a,cv=_recover(state,eos)
        decay,source_rate=rates(rho,v,p,t,a,cv,clock)
        ghosts=[]
        for side,boundary in ((0,stage.left),(-1,stage.right)):
            if boundary.kind=="periodic":ghost=state[-1 if side==0 else 0].copy()
            elif boundary.kind=="reservoir":ghost=np.asarray(boundary.reservoir_conservative,dtype=float)
            else:
                ghost=state[side].copy()
                if boundary.kind=="reflective":ghost[1]*=-1
            ghosts.append(ghost)
        padded=np.vstack((ghosts[0],state,ghosts[1]))
        gr,gv,gp,_,ga,_=_recover(padded,eos)
        flux=np.column_stack((padded[:,1],padded[:,1]*gv+gp,gv*(padded[:,2]+gp)))
        speed=np.maximum(np.abs(gv[:-1])+ga[:-1],np.abs(gv[1:])+ga[1:])
        interfaces=.5*(flux[:-1]+flux[1:])-0.5*speed[:,None]*(padded[1:]-padded[:-1])
        for side,boundary,normal,cell in ((0,stage.left,-1,state[0]),(-1,stage.right,1,state[-1])):
            if boundary.kind in ('pressure_outflow','restricted_ideal_vent','ideal_same_fluid_open'):
                if boundary.kind=='pressure_outflow':
                    face=evaluate_pressure_outflow_face(cell,eos,boundary.ambient_pressure_pa_abs,outward_normal=normal)
                elif boundary.kind=='ideal_same_fluid_open':
                    from .gas_reservoir_boundary import evaluate_ideal_same_fluid_open_face
                    face=evaluate_ideal_same_fluid_open_face(cell,eos,boundary.ambient_pressure_pa_abs,
                        boundary.reservoir_stagnation_temperature_K,outward_normal=normal,
                        same_fluid_isentropic_reservoir_reviewed=boundary.same_fluid_isentropic_reservoir_reviewed)
                else:
                    from .gas_restricted_vent import evaluate_restricted_ideal_vent_face
                    face=evaluate_restricted_ideal_vent_face(cell,eos,pipe_area_m2=area,vent_area_m2=boundary.vent_area_m2,
                        discharge_coefficient=boundary.vent_discharge_coefficient,ambient_pressure_pa_abs=boundary.ambient_pressure_pa_abs,
                        outward_normal=normal).pipe_face
                vv=normal*face.outward_velocity_m_s;rr=face.density_kg_m3;pp=face.pressure_pa_abs
                energy=rr*(face.internal_energy_J_kg+.5*vv*vv)
                interfaces[side]=(rr*vv,rr*vv*vv+pp,vv*(energy+pp))
                speed[side]=max(speed[side],abs(vv)+face.sound_speed_m_s)
        sources=np.zeros_like(state)
        sources[:,1]=-decay*rho*v+rho*q.signed_axial_gravity_m_s2
        sources[:,2]=rho*v*q.signed_axial_gravity_m_s2
        if ambient is not None:sources[:,2]+=4*heat/q.inner_diameter_m*(ambient-t)
        derivative=-(interfaces[1:]-interfaces[:-1])/dx+sources
        if not np.all(np.isfinite(derivative)):
            raise ValueError("flux/source derivative exceeds finite numerical support")
        return derivative,area*(interfaces[0]-interfaces[-1]),area*dx*np.sum(sources,axis=0),float(np.max(speed)),source_rate

    initial=area*dx*np.sum(u,axis=0)
    boundary_budget=np.zeros(3);source_budget=np.zeros(3)
    time=q.start_time_s;stage_index=0;times=[time];events=[];rejected=0
    observations=[];observation_index=0
    if q.observation_times_s and q.observation_times_s[0]==time:
        observations.append(_observation(time,u,eos));observation_index=1
    while time<finish:
        if q.stop_when_maximum_pressure_below_pa_abs is not None and float(np.max(_recover(u,eos)[2]))<=q.stop_when_maximum_pressure_below_pa_abs:
            break
        if len(times)-1>=q.maximum_steps:raise RuntimeError("maximum accepted time steps exceeded")
        while stage_index+1<len(stages) and stages[stage_index+1].start_time_s<=time:
            stage_index+=1;events.append(stages[stage_index].start_time_s)
        stage=stages[stage_index]
        event=stages[stage_index+1].start_time_s if stage_index+1<len(stages) else finish
        next_observation=q.observation_times_s[observation_index] if observation_index<len(q.observation_times_s) else finish
        target=min(finish,event,next_observation)
        rho,v,p,t,a,cv=_recover(u,eos)
        _,source_rate=rates(rho,v,p,t,a,cv,time)
        dt=min(q.maximum_step_s,q.cfl*dx/float(np.max(np.abs(v)+a)),target-time)
        if source_rate>0:dt=min(dt,q.source_fraction_per_step/source_rate)
        if dt<=0 or time+dt<=time:raise RuntimeError("time step cannot advance the finite clock")
        for retry in range(q.maximum_positivity_retries+1):
            try:
                k0,b0,s0,_,r0=rhs(u,stage,time)
                trial=u+dt*k0
                _recover(trial,eos)
                k1,b1,s1,max_speed,r1=rhs(trial,stage,time+dt)
                if dt*max_speed/dx>q.cfl*(1+1e-12):raise ValueError("stage CFL grew beyond support")
                if dt*max(r0,r1)>q.source_fraction_per_step*(1+1e-12):raise ValueError('stage source rate grew beyond support')
                candidate=.5*u+.5*(trial+dt*k1)
                _recover(candidate,eos)
                break
            except (ValueError,OverflowError,FloatingPointError) as exc:
                if retry==q.maximum_positivity_retries or dt/2<q.minimum_retry_step_s:
                    raise RuntimeError("state-checked CFL/positivity retries exhausted") from exc
                rejected+=1;dt*=.5
        if q.stop_when_maximum_pressure_below_pa_abs is not None:
            threshold=q.stop_when_maximum_pressure_below_pa_abs
            candidate_pressure=float(np.max(_recover(candidate,eos)[2]))
            if candidate_pressure<=threshold:
                # Retain a physical accepted state on the reached side of the
                # threshold, with its own actual SSPRK2 flux/source budgets.
                low=0.;high=dt;selected=(candidate,b1,s1,candidate_pressure)
                for _ in range(80):
                    if threshold-selected[3]<=q.pressure_stop_tolerance_pa:break
                    mid=(low+high)/2
                    if mid==low or mid==high:break
                    probe_trial=u+mid*k0;_recover(probe_trial,eos)
                    probe_k,probe_b,probe_s,probe_speed,probe_rate=rhs(probe_trial,stage,time+mid)
                    if mid*probe_speed/dx>q.cfl*(1+1e-12):raise RuntimeError('pressure-event refinement exceeded CFL')
                    if mid*max(r0,probe_rate)>q.source_fraction_per_step*(1+1e-12):raise RuntimeError('pressure-event refinement exceeded source bound')
                    probe=.5*u+.5*(probe_trial+mid*probe_k)
                    pressure=float(np.max(_recover(probe,eos)[2]))
                    if pressure>threshold:low=mid
                    else:high=mid;selected=(probe,probe_b,probe_s,pressure)
                if threshold-selected[3]>q.pressure_stop_tolerance_pa:
                    raise RuntimeError('pressure-stop precision could not be resolved')
                dt=high;candidate,b1,s1,_=selected
                if dt<=0 or time+dt<=time:raise RuntimeError('pressure-stop event cannot advance the finite clock')
        boundary_budget+=.5*dt*(b0+b1);source_budget+=.5*dt*(s0+s1)
        u=candidate
        new_time=time+dt
        # Exactly record a clipped event/final step, without skipping a residual interval.
        if dt==target-time:new_time=target
        time=new_time;times.append(time)
        if observation_index<len(q.observation_times_s) and time==q.observation_times_s[observation_index]:
            observations.append(_observation(time,u,eos));observation_index+=1
    while time==finish and stage_index+1<len(stages) and stages[stage_index+1].start_time_s==finish:
        stage_index+=1;events.append(finish)
    final=area*dx*np.sum(u,axis=0)
    rho,v,p,t,a,_,phases=_recover(u,eos,include_phases=True)
    ledger=GasPipeConservationLedger(initial,final,boundary_budget,source_budget,
                                     final-initial-boundary_budget-source_budget)
    identity={"provider_class":type(eos).__module__+"."+type(eos).__qualname__,
              "energy_input":"specific internal energy including the declared provider reference; negative values allowed"}
    if isinstance(eos,IdealGasInternalEnergyEOS):
        identity.update(specific_gas_constant_J_kg_K=eos.specific_gas_constant_J_kg_K,
                        gamma=eos.gamma,internal_energy_reference_J_kg=eos.internal_energy_reference_J_kg)
    elif isinstance(eos,CoolPropGasInternalEnergyEOS):
        from importlib.metadata import version
        identity.update(fluid=eos.fluid,backend=eos.backend,CoolProp_version=version("CoolProp"),
                        allowed_phases=["gas","supercritical_gas","supercritical"],
                        energy_reference_offset_J_kg=eos.energy_reference_offset_J_kg,
                        energy_reference_anchor=eos.reference_anchor)
    termination=None
    if q.stop_when_maximum_pressure_below_pa_abs is not None:
        reached=float(np.max(p))<=q.stop_when_maximum_pressure_below_pa_abs
        termination=dict(reason='maximum_pressure_threshold' if reached else 'duration_limit',threshold_reached=reached,
            threshold_pa_abs=q.stop_when_maximum_pressure_below_pa_abs,achieved_maximum_pressure_pa_abs=float(np.max(p)),
            pressure_tolerance_pa=q.pressure_stop_tolerance_pa,actual_stop_time_s=time,requested_finish_time_s=finish,
            event_scope='first accepted endpoint crossing refined locally; no guarantee of unseen multiple crossings within one CFL step')
    decay_identity=None if momentum_decay_rate_provider is None else dict(provider_class=type(momentum_decay_rate_provider).__module__+'.'+type(momentum_decay_rate_provider).__qualname__,rate_units='1/s',momentum_source='-rho*v*rate',fixed_Darcy_drag_replaced=True,stage_state_feedback=True,total_energy_removed_by_drag=False)
    return GasAcousticPipeResult((np.arange(n)+.5)*dx,u,p,t,v,a,tuple(times),tuple(events),len(times)-1,rejected,ledger,identity,phases,tuple(observations),termination=termination,momentum_decay_identity=decay_identity)
