"""Calibrated net-expansion relief capacity and a distinct reviewed Fanno branch.

Device KR, discharge Cd, and distributed Darcy friction are different inputs.
No standard factor, fitting table, or automatic KR-to-Fanno mapping is supplied.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative


class ReliefLossTerm(Contract):
    id: Name
    K: Nonnegative
    coefficient_basis_id: Name
    applicability_reviewed: bool = False


class NetExpansionCalibration(Contract):
    basis_id: Name
    total_K: Positive
    heat_capacity_ratio: float = Field(gt=1)
    inlet_state_definition_id: Name
    critical_pressure_drop_fraction: float = Field(gt=0,lt=1)
    expansion_evaluated_at_drop_fraction: float = Field(gt=0,lt=1)
    net_expansion_factor: float = Field(gt=0,le=1)
    common_area_K_and_expansion_convention_reviewed: bool = False


class CalibratedReliefLineRequest(Contract):
    inlet_pressure_Pa_abs: Positive | None
    back_pressure_Pa_abs: Positive | None
    inlet_temperature_K: Positive | None
    inlet_density_kg_m3: Positive | None
    heat_capacity_ratio: float | None = Field(default=None,gt=1)
    common_flow_area_m2: Positive | None
    inlet_state_definition_id: Name
    loss_terms: tuple[ReliefLossTerm,...]
    same_bore_common_K_reference_and_steady_inlet_state_reviewed: bool = False
    net_expansion: NetExpansionCalibration | None = None
    reference_density_kg_m3: Positive | None = None
    reference_volume_basis_id: Name | None = None
    supplied_capacity_factor: float | None = Field(default=None,gt=0,le=1)
    capacity_factor_basis_id: Name | None = None

    @model_validator(mode='after')
    def identity(self):
        if len({t.id for t in self.loss_terms})!=len(self.loss_terms):raise ValueError('unique physical loss-term IDs required')
        if self.inlet_pressure_Pa_abs is not None and self.back_pressure_Pa_abs is not None and self.back_pressure_Pa_abs>self.inlet_pressure_Pa_abs:
            raise ValueError('forward relieving pressure direction required')
        return self


class CalibratedReliefLineResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    total_K: Nonnegative
    regime: Literal['zero_drive','subcritical','limited_by_supplied_sonic_criterion'] | None = None
    actual_pressure_drop_fraction: Nonnegative | None = None
    effective_pressure_drop_Pa: Nonnegative | None = None
    mass_flux_kg_m2_s: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    reference_volume_flow_m3_s: Nonnegative | None = None
    supplied_factor_adjusted_mass_flow_kg_s: Nonnegative | None = None
    expansion_basis_id: Name | None = None
    issues: tuple[str,...] = ()
    calibration_or_standard_compliance_inferred: Literal[False] = False


def evaluate_calibrated_relief_line(q: CalibratedReliefLineRequest) -> CalibratedReliefLineResult:
    """Use supplied expansion/critical data on its exact common-K/state basis.

    The SI capacity is A*Y*sqrt(2*rho_in*effective_dP/Ktotal). Y must have been
    supplied at the actual pressure-drop fraction or at its supplied sonic cap.
    A disk's tested KR is an input loss observation, never a discharge Cd.
    This is a conditional capacity observer, not a new empirical Y calibration.
    """
    if not isinstance(q,CalibratedReliefLineRequest):raise TypeError('CalibratedReliefLineRequest required')
    q=CalibratedReliefLineRequest.model_validate(q.model_dump(mode='python'))
    k=math.fsum(t.K for t in q.loss_terms);base=dict(total_K=k)
    if not math.isfinite(k):raise ValueError('finite total resistance required')
    if (None in (q.inlet_pressure_Pa_abs,q.back_pressure_Pa_abs,q.inlet_temperature_K,q.inlet_density_kg_m3,
        q.heat_capacity_ratio,q.common_flow_area_m2) or not q.loss_terms or not q.same_bore_common_K_reference_and_steady_inlet_state_reviewed
        or any(not t.applicability_reviewed for t in q.loss_terms)):
        return CalibratedReliefLineResult(status='needs_data',**base,issues=('complete state/area and reviewed common-bore loss coefficients required',))
    if k==0:return CalibratedReliefLineResult(status='out_of_scope',**base,issues=('net-expansion resistance formula is singular at zero K; an explicitly reviewed nozzle/zero-friction model is a separate branch',))
    x=(q.inlet_pressure_Pa_abs-q.back_pressure_Pa_abs)/q.inlet_pressure_Pa_abs
    if x==0:return CalibratedReliefLineResult(status='computed',**base,regime='zero_drive',actual_pressure_drop_fraction=0.,
        effective_pressure_drop_Pa=0.,mass_flux_kg_m2_s=0.,mass_flow_kg_s=0.)
    c=q.net_expansion
    if c is None or not c.common_area_K_and_expansion_convention_reviewed:
        return CalibratedReliefLineResult(status='needs_data',**base,actual_pressure_drop_fraction=x,issues=('supplied Y and sonic criterion on the actual K/state convention required; no lookup table or universal value inferred',))
    if c.inlet_state_definition_id!=q.inlet_state_definition_id or not math.isclose(c.total_K,k,rel_tol=1e-12,abs_tol=0) or not math.isclose(c.heat_capacity_ratio,q.heat_capacity_ratio,rel_tol=1e-12,abs_tol=0):
        raise ValueError('expansion calibration does not match total K, gas heat ratio or inlet pressure/density definition')
    effective=min(x,c.critical_pressure_drop_fraction)
    if not math.isclose(c.expansion_evaluated_at_drop_fraction,effective,rel_tol=1e-10,abs_tol=1e-13):
        raise ValueError('supplied expansion factor is evaluated at a different effective pressure drop')
    flux=c.net_expansion_factor*math.sqrt(2*q.inlet_density_kg_m3*q.inlet_pressure_Pa_abs*effective/k)
    flow=flux*q.common_flow_area_m2
    if not math.isfinite(flow) or not math.isfinite(flux):raise ValueError('finite relief capacity required')
    reference=None;adjusted=None;issues=[]
    if q.reference_density_kg_m3 is not None:
        if q.reference_volume_basis_id is None:issues.append('reference density needs its gas/composition/P/T volume basis')
        else:reference=flow/q.reference_density_kg_m3
    if q.supplied_capacity_factor is not None:
        if q.capacity_factor_basis_id is None:issues.append('capacity derating needs a supplied criterion/data basis')
        else:adjusted=flow*q.supplied_capacity_factor
    return CalibratedReliefLineResult(status='needs_data' if issues else 'computed',**base,
        regime='limited_by_supplied_sonic_criterion' if x>=c.critical_pressure_drop_fraction else 'subcritical',
        actual_pressure_drop_fraction=x,effective_pressure_drop_Pa=q.inlet_pressure_Pa_abs*effective,
        mass_flux_kg_m2_s=flux,mass_flow_kg_s=flow,reference_volume_flow_m3_s=reference,
        supplied_factor_adjusted_mass_flow_kg_s=adjusted,expansion_basis_id=c.basis_id,issues=tuple(issues))


def fanno_remaining_darcy_friction(mach: float,gamma: float) -> float:
    """Dimensionless integral f_D dx/D from subsonic M to M=1.

    Close to sonic, a positive power series of the same analytic relation avoids
    subtracting its cancelling first-order terms. f_D is Darcy, not Fanning.
    """
    m=float(mach);g=float(gamma)
    if not math.isfinite(m) or not math.isfinite(g) or not 0<m<=1 or g<=1:raise ValueError('subsonic 0<M<=1 and finite gamma>1 required')
    z=1-m*m
    if z<.1:
        b=(g-1)/(g+1);c=(g+1)/(2*g)
        return math.fsum((1/g-c*(1-b**n)/n)*z**n for n in range(2,41))
    return z/(g*m*m)+(g+1)/(2*g)*math.log((g+1)*m*m/(2+(g-1)*m*m))


class FannoReliefLineRequest(Contract):
    reservoir_stagnation_pressure_Pa_abs: Positive | None
    reservoir_stagnation_temperature_K: Positive | None
    back_pressure_Pa_abs: Positive | None
    flow_area_m2: Positive | None
    integrated_darcy_friction: Nonnegative | None
    specific_gas_constant_J_kg_K: Positive
    heat_capacity_ratio: float = Field(gt=1)
    friction_basis_id: Name
    constant_area_adiabatic_ideal_gas_and_isentropic_entrance_reviewed: bool = False
    friction_integral_is_not_automatically_total_K_or_disk_KR_reviewed: bool = False
    samples_at_friction_fraction: tuple[float,...] = (0.,.25,.5,.75,1.)
    minimum_inlet_Mach: Positive = Field(default=1e-5,lt=1)
    maximum_root_iterations: int = Field(default=150,ge=1,le=1000)

    @model_validator(mode='after')
    def support(self):
        if (self.reservoir_stagnation_pressure_Pa_abs is not None and self.back_pressure_Pa_abs is not None
            and self.back_pressure_Pa_abs>self.reservoir_stagnation_pressure_Pa_abs):raise ValueError('forward reservoir-to-line pressure direction required')
        a=self.samples_at_friction_fraction
        if not a or a[0]!=0 or a[-1]!=1 or any(not 0<=x<=1 for x in a) or any(y<=x for x,y in zip(a,a[1:])):
            raise ValueError('ordered distinct friction fractions including0 and1 required')
        return self


class FannoLinePoint(Contract):
    friction_fraction: float
    Mach: Nonnegative
    pressure_Pa_abs: Positive
    temperature_K: Positive
    density_kg_m3: Positive
    velocity_m_s: Nonnegative
    mass_flux_kg_m2_s: Nonnegative
    stagnation_pressure_Pa_abs: Positive
    stagnation_temperature_K: Positive


class FannoReliefLineResult(Contract):
    status: Literal['computed','needs_data','insufficient_Mach_support','not_converged']
    regime: Literal['zero_drive','subcritical','choked_at_line_exit'] | None = None
    mass_flow_kg_s: Nonnegative | None = None
    critical_back_pressure_Pa_abs: Positive | None = None
    mass_flux_kg_m2_s: Nonnegative | None = None
    profile: tuple[FannoLinePoint,...] = ()
    wall_momentum_loss_Pa: Nonnegative | None = None
    momentum_residual_Pa: float | None = None
    maximum_specific_energy_residual_J_kg: Nonnegative | None = None
    entropy_increase_J_kg_K: Nonnegative | None = None
    issues: tuple[str,...] = ()
    original_total_K_expansion_calibration_qualified: Literal[False] = False


def evaluate_fanno_relief_line(q: FannoReliefLineRequest) -> FannoReliefLineResult:
    """Steady constant-bore adiabatic perfect-gas reference line.

    The inlet reservoir is stagnating and its entrance acceleration is
    isentropic. The supplied integral f_D dx/D describes DISTRIBUTED friction;
    it is not a local head-loss sum, certified disk KR or an exit-loss factor.
    The ideal-gas EOS and stagnation-pressure transform are existing owners.
    No disk opening, area change, branch/manifold, heat transfer or blowdown
    dynamics are solved. Positions label friction integral, not arbitrary route
    distance. This reference model does not reproduce a calibrated Y(K) chart.
    """
    from scipy.optimize import brentq
    from scipy.integrate import quad
    from fluids.compressible import P_stagnation
    from .gas_acoustic_pipe import IdealGasInternalEnergyEOS
    if not isinstance(q,FannoReliefLineRequest):raise TypeError('FannoReliefLineRequest required')
    q=FannoReliefLineRequest.model_validate(q.model_dump(mode='python'))
    if (None in (q.reservoir_stagnation_pressure_Pa_abs,q.reservoir_stagnation_temperature_K,q.back_pressure_Pa_abs,q.flow_area_m2,q.integrated_darcy_friction)
        or not q.constant_area_adiabatic_ideal_gas_and_isentropic_entrance_reviewed
        or not q.friction_integral_is_not_automatically_total_K_or_disk_KR_reviewed):
        return FannoReliefLineResult(status='needs_data',issues=('complete reservoir, area and separately reviewed distributed-friction boundary required',))
    eos=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=q.specific_gas_constant_J_kg_K,gamma=q.heat_capacity_ratio)
    g=eos.gamma;rg=eos.specific_gas_constant_J_kg_K;cp=eos.cv_J_kg_K+rg;a=(g-1)/2
    p0=q.reservoir_stagnation_pressure_Pa_abs;t0=q.reservoir_stagnation_temperature_K;k=q.integrated_darcy_friction
    if q.back_pressure_Pa_abs==p0:
        points=tuple(FannoLinePoint(friction_fraction=f,Mach=0,pressure_Pa_abs=p0,temperature_K=t0,density_kg_m3=p0/(rg*t0),velocity_m_s=0,
            mass_flux_kg_m2_s=0,stagnation_pressure_Pa_abs=p0,stagnation_temperature_K=t0) for f in q.samples_at_friction_fraction)
        return FannoReliefLineResult(status='computed',regime='zero_drive',mass_flow_kg_s=0,mass_flux_kg_m2_s=0,profile=points,
            wall_momentum_loss_Pa=0,momentum_residual_Pa=0,maximum_specific_energy_residual_J_kg=0,entropy_increase_J_kg_K=0)
    minimum=q.minimum_inlet_Mach
    if fanno_remaining_darcy_friction(minimum,g)<k:
        return FannoReliefLineResult(status='insufficient_Mach_support',issues=('critical inlet Mach lies below declared numerical support',))
    def root(fun,lo,hi):return brentq(fun,lo,hi,xtol=5e-16,rtol=1e-13,maxiter=q.maximum_root_iterations)
    try:
        mc=1. if k==0 else root(lambda m:fanno_remaining_darcy_friction(m,g)-k,minimum,1.)
        def entrance(m):
            temperature=t0/(1+a*m*m);pressure=p0*(temperature/t0)**(g/(g-1));density=pressure/(rg*temperature)
            point=eos.evaluate(density,eos.internal_energy_from_temperature(temperature));speed=m*point.sound_speed_m_s
            return pressure,temperature,density,speed,density*speed
        def profile_point(m1,fraction,sonic_exit=False):
            p1,t1,rho1,u1,flux=entrance(m1)
            if k==0 or fraction==0:m=m1
            elif sonic_exit and fraction==1:m=1.
            else:
                remaining=fanno_remaining_darcy_friction(m1,g)-k*fraction
                if remaining<=0:m=1.
                else:m=root(lambda v:fanno_remaining_darcy_friction(v,g)-remaining,m1,1.)
            temperature=t0/(1+a*m*m);speed=m*math.sqrt(g*rg*temperature);density=flux/speed;pressure=density*rg*temperature
            state=eos.evaluate(density,eos.internal_energy_from_temperature(temperature))
            return FannoLinePoint(friction_fraction=fraction,Mach=m,pressure_Pa_abs=state.pressure_pa_abs,
                temperature_K=state.temperature_K,density_kg_m3=density,velocity_m_s=speed,mass_flux_kg_m2_s=density*speed,
                stagnation_pressure_Pa_abs=P_stagnation(state.pressure_pa_abs,state.temperature_K,t0,g),
                stagnation_temperature_K=state.temperature_K+speed*speed/(2*cp))
        critical=profile_point(mc,1.,True).pressure_Pa_abs
        choked=q.back_pressure_Pa_abs<=critical
        if choked:m1=mc
        else:
            end_low=profile_point(minimum,1.).pressure_Pa_abs
            if q.back_pressure_Pa_abs>=end_low:
                return FannoReliefLineResult(status='insufficient_Mach_support',critical_back_pressure_Pa_abs=critical,
                    issues=('positive drive is below declared inlet-Mach numerical support; not labelled exact zero flow',))
            m1=root(lambda m:profile_point(m,1.).pressure_Pa_abs-q.back_pressure_Pa_abs,minimum,mc)
        points=tuple(profile_point(m1,f,choked) for f in q.samples_at_friction_fraction)
        first,last=points[0],points[-1];flux=first.mass_flux_kg_m2_s
        wall=0. if k==0 else .5*flux*k*quad(lambda f:profile_point(m1,f,choked).velocity_m_s,0,1,epsabs=1e-7,epsrel=2e-9,limit=150)[0]
        momentum=first.pressure_Pa_abs-last.pressure_Pa_abs-flux*(last.velocity_m_s-first.velocity_m_s)-wall
        energy=max(abs(cp*p.temperature_K+.5*p.velocity_m_s**2-cp*t0) for p in points)
        entropy=cp*math.log(last.temperature_K/first.temperature_K)-rg*math.log(last.pressure_Pa_abs/first.pressure_Pa_abs)
        if entropy< -1e-8 or abs(momentum)>1e-6+2e-7*p0:
            return FannoReliefLineResult(status='not_converged',issues=('momentum or entropy budget exceeds numerical support',))
        return FannoReliefLineResult(status='computed',regime='choked_at_line_exit' if choked else 'subcritical',
            mass_flow_kg_s=flux*q.flow_area_m2,mass_flux_kg_m2_s=flux,critical_back_pressure_Pa_abs=critical,profile=points,
            wall_momentum_loss_Pa=wall,momentum_residual_Pa=momentum,maximum_specific_energy_residual_J_kg=energy,
            entropy_increase_J_kg_K=max(0.,entropy))
    except RuntimeError:
        return FannoReliefLineResult(status='not_converged',issues=('subsonic Fanno pressure/Mach root did not converge',))
