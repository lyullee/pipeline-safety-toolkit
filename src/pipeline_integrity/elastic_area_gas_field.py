"""Evolving ideal-gas/elastic-area field through the existing conservative FV.

Constant reference area, fixed axial length, fixed exterior pressure and wall
bath temperature are required. An exact line-coordinate thermodynamic adapter
includes fluid, elastic and exterior-pressure potential energy. The FV owner
coordinates are explicitly not material density/pressure/internal energy.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Literal
import numpy as np
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative,digest
from .gas_acoustic_pipe import (GasThermodynamicPoint,IdealGasInternalEnergyEOS,GasAcousticPipeSettings,
    GasPipeBoundary,GasPipeBoundaryStage,gas_conservative_state,solve_gas_acoustic_pipe)


@dataclass(frozen=True)
class ElasticAreaLineEOS:
    """Exact conservative line EOS for a reviewed linear pressure-area law.

    A=A0+C(p-pe), Psi=C(p²-pe²)/2, Pi=A0*p+C(p-pe)²/2.
    M=rho*A and U=M*e+Psi. The owner's rho=M/A0 and p=Pi/A0
    are normalized line coordinates; T is physical, sound speed is the coupled
    wave speed and cv is d(U/M)/dT at fixed M. Physical phase remains ideal gas.
    """
    fluid_eos: IdealGasInternalEnergyEOS
    reference_area_m2: float
    area_compliance_m2_pa: float
    exterior_pressure_pa_abs: float
    maximum_area_fractional_change: float

    def __post_init__(self):
        if not isinstance(self.fluid_eos,IdealGasInternalEnergyEOS):raise TypeError('calorically perfect IdealGasInternalEnergyEOS required')
        values=(self.reference_area_m2,self.area_compliance_m2_pa,self.exterior_pressure_pa_abs,self.maximum_area_fractional_change)
        if not all(math.isfinite(v) for v in values) or self.reference_area_m2<=0 or min(self.area_compliance_m2_pa,self.exterior_pressure_pa_abs)<0 or not 0<self.maximum_area_fractional_change<=.05:
            raise ValueError('finite A0>0,C>=0,pe>=0 and explicit area support ceiling in (0,.05] required')

    def physical_point(self,pressure_pa_abs,temperature_K):
        p=float(pressure_pa_abs);t=float(temperature_K);a0=self.reference_area_m2;c=self.area_compliance_m2_pa;pe=self.exterior_pressure_pa_abs
        if not all(math.isfinite(v) for v in (p,t)) or p<=0 or t<=0 or p<pe:raise ValueError('physical ideal-gas state must have T>0 and tensile p>=pe>=0')
        dp=p-pe;fraction=c*dp/a0
        if not math.isfinite(fraction) or fraction>self.maximum_area_fractional_change:raise ValueError('declared linear-wall area-strain support exceeded')
        area=a0+c*dp;rho=p/(self.fluid_eos.specific_gas_constant_J_kg_K*t)
        fluid=self.fluid_eos.evaluate(rho,self.fluid_eos.internal_energy_from_temperature(t))
        elastic=.5*c*dp*dp;external=pe*c*dp;psi=elastic+external;pi=a0*p+elastic
        speed=fluid.sound_speed_m_s/math.sqrt(1+rho*c*fluid.sound_speed_m_s**2/area)
        cv=self.fluid_eos.cv_J_kg_K*(area+self.fluid_eos.gamma*p*c)/(area+p*c)
        result=dict(pressure_pa_abs=p,temperature_K=t,area_m2=area,fluid_density_kg_m3=rho,
            mass_per_length_kg_m=rho*area,fluid_specific_internal_energy_J_kg=self.fluid_eos.internal_energy_from_temperature(t),
            elastic_energy_J_m=elastic,exterior_pressure_potential_J_m=external,wall_plus_exterior_potential_J_m=psi,
            momentum_pressure_potential_N=pi,effective_wave_speed_m_s=speed,physical_fluid_sound_speed_m_s=fluid.sound_speed_m_s,
            effective_line_cv_J_kg_K=cv,physical_fluid_cv_J_kg_K=self.fluid_eos.cv_J_kg_K,area_fractional_change=fraction)
        if not all(math.isfinite(v) for v in result.values()) or min(area,rho,speed,cv,pi)<=0:raise ValueError('line state exceeds finite positive support')
        return result

    def recover_physical(self,owner_density,owner_specific_internal_energy):
        density=float(owner_density);e=float(owner_specific_internal_energy);a0=self.reference_area_m2;c=self.area_compliance_m2_pa;pe=self.exterior_pressure_pa_abs;gamma=self.fluid_eos.gamma
        if not math.isfinite(density) or density<=0 or not math.isfinite(e):raise ValueError('finite positive line mass density and finite line energy required')
        mass=density*a0
        thermal_line=mass*(e-self.fluid_eos.internal_energy_reference_J_kg)
        rhs=thermal_line-a0*pe/(gamma-1)
        if not math.isfinite(rhs) or rhs<0:raise ValueError('line energy is below the tensile-pressure support; no clipping/extrapolation')
        linear=(a0+gamma*c*pe)/(gamma-1);quadratic=c*(gamma+1)/(2*(gamma-1))
        radical=math.hypot(linear,2*math.sqrt(quadratic)*math.sqrt(rhs))
        dp=2*rhs/(linear+radical)
        p=pe+dp;area=a0+c*dp;t=p*area/(mass*self.fluid_eos.specific_gas_constant_J_kg_K)
        state=self.physical_point(p,t)
        if not math.isfinite(dp):raise ValueError('pressure inversion exceeds finite support')
        return state

    def evaluate(self,density_kg_m3,internal_energy_J_kg):
        q=self.recover_physical(density_kg_m3,internal_energy_J_kg)
        return GasThermodynamicPoint(q['momentum_pressure_potential_N']/self.reference_area_m2,
            q['temperature_K'],q['effective_wave_speed_m_s'],q['effective_line_cv_J_kg_K'],'gas')

    def physical_to_owner_conservative(self,pressure_pa_abs,temperature_K,velocity_m_s):
        p,t,v=np.broadcast_arrays(np.asarray(pressure_pa_abs,dtype=float),np.asarray(temperature_K,dtype=float),np.asarray(velocity_m_s,dtype=float))
        points=[self.physical_point(x,y) for x,y in zip(p.flat,t.flat)]
        density=np.asarray([q['mass_per_length_kg_m']/self.reference_area_m2 for q in points]).reshape(p.shape)
        energy=np.asarray([q['fluid_specific_internal_energy_J_kg']+q['wall_plus_exterior_potential_J_m']/q['mass_per_length_kg_m'] for q in points]).reshape(p.shape)
        return gas_conservative_state(density,v,energy)


class PhysicalElasticPipeBoundary(Contract):
    kind: Literal['periodic','reflective','prescribed_physical_PT_velocity_ghost']
    pressure_pa_abs: Positive | None = None
    temperature_K: Positive | None = None
    velocity_m_s: float | None = None

    @model_validator(mode='after')
    def all_ghost_fields(self):
        supplied=(self.pressure_pa_abs is not None,self.temperature_K is not None,self.velocity_m_s is not None)
        if self.kind=='prescribed_physical_PT_velocity_ghost':
            if not all(supplied):raise ValueError('physical PT and velocity all required for prescribed ghost')
        elif any(supplied):raise ValueError('only prescribed physical ghosts accept state values')
        return self


class ElasticAreaGasFieldRequest(Contract):
    id: Name
    reference_pipe_numerics: GasAcousticPipeSettings
    area_compliance_m2_pa: Nonnegative
    exterior_pressure_pa_abs: Nonnegative
    fixed_wall_temperature_K: Positive
    constant_heat_conductance_per_length_W_m_K: Nonnegative
    maximum_reviewed_area_fractional_change: Positive = Field(le=.05)
    initial_pressure_pa_abs: tuple[Positive,...] = Field(min_length=2,max_length=1000000)
    initial_temperature_K: tuple[Positive,...] = Field(min_length=2,max_length=1000000)
    initial_velocity_m_s: tuple[float,...] = Field(min_length=2,max_length=1000000)
    left: PhysicalElasticPipeBoundary
    right: PhysicalElasticPipeBoundary
    wall_constitutive_evidence_id: Name
    fixed_length_quasistatic_linear_tensile_wall_and_constant_bath_reviewed: bool = False

    @model_validator(mode='after')
    def aligned_and_unambiguous(self):
        if len({len(self.initial_pressure_pa_abs),len(self.initial_temperature_K),len(self.initial_velocity_m_s)})!=1:raise ValueError('aligned physical cell arrays required')
        s=self.reference_pipe_numerics
        factors=s.darcy_friction_factor if isinstance(s.darcy_friction_factor,tuple) else (s.darcy_friction_factor,)
        heat=s.wall_heat_transfer_W_m2_K if isinstance(s.wall_heat_transfer_W_m2_K,tuple) else (s.wall_heat_transfer_W_m2_K,)
        if any(v!=0 for v in (*factors,*heat)) or s.signed_axial_gravity_m_s2!=0 or s.ambient_temperature_K is not None or s.stop_when_maximum_pressure_below_pa_abs is not None:
            raise ValueError('reference numerics must have zero native drag/gravity/heat and no native pressure event; use declared physical line heat closure')
        if (self.left.kind=='periodic')!=(self.right.kind=='periodic'):raise ValueError('periodic ends must be paired')
        return self


@dataclass(frozen=True)
class PhysicalElasticPipeSnapshot:
    time_s: float
    line_conservative_mass_momentum_energy: np.ndarray
    physical: dict[str,np.ndarray]


@dataclass(frozen=True)
class ElasticAreaGasFieldResult:
    request_digest: str
    initial: PhysicalElasticPipeSnapshot
    final: PhysicalElasticPipeSnapshot
    observations: tuple[PhysicalElasticPipeSnapshot,...]
    conservative_line_ledger: object
    owner_line_coordinate_result: object
    coordinate_and_domain_receipt: dict


def solve_elastic_area_gas_field(q: ElasticAreaGasFieldRequest,*,fluid_eos: IdealGasInternalEnergyEOS):
    """Evolve physical P/T/u/A using the unchanged shared conservative FV.

    A0 is the actual circular reference area at fixed Tw and pe. Its reference
    diameter is not the evolving diameter. Native source geometry is used only
    to encode exactly the declared constant conductance per physical length:
    h_ref=U_line*D_ref/(4*A0). Constant physical perimeter*h is not inferred.
    Horizontal, frictionless flow is the first selected domain. Prescribed
    conservative ghosts are not characteristic reservoir/nozzle boundaries.
    """
    q=ElasticAreaGasFieldRequest.model_validate(q.model_dump())
    if not q.fixed_length_quasistatic_linear_tensile_wall_and_constant_bath_reviewed:raise ValueError('reviewed fixed-length quasi-static wall and heat basis required')
    s=q.reference_pipe_numerics;a0=math.pi*s.inner_diameter_m**2/4
    eos=ElasticAreaLineEOS(fluid_eos,a0,q.area_compliance_m2_pa,q.exterior_pressure_pa_abs,q.maximum_reviewed_area_fractional_change)
    initial=eos.physical_to_owner_conservative(q.initial_pressure_pa_abs,q.initial_temperature_K,q.initial_velocity_m_s)
    def boundary(b):
        if b.kind!='prescribed_physical_PT_velocity_ghost':return GasPipeBoundary(kind=b.kind)
        state=eos.physical_to_owner_conservative(b.pressure_pa_abs,b.temperature_K,b.velocity_m_s)
        return GasPipeBoundary(kind='reservoir',reservoir_conservative=tuple(float(x) for x in state))
    h=q.constant_heat_conductance_per_length_W_m_K*s.inner_diameter_m/(4*a0)
    native_settings=s.model_copy(update=dict(wall_heat_transfer_W_m2_K=h,ambient_temperature_K=q.fixed_wall_temperature_K))
    native=solve_gas_acoustic_pipe(initial,native_settings,eos=eos,boundary_stages=(GasPipeBoundaryStage(start_time_s=s.start_time_s,left=boundary(q.left),right=boundary(q.right)),))
    def restore(state,time):
        mass_density=state[:,0];velocity=state[:,1]/mass_density;energy=state[:,2]/mass_density-.5*velocity**2
        points=[eos.recover_physical(r,e) for r,e in zip(mass_density,energy)]
        physical={key:np.asarray([p[key] for p in points]) for key in points[0]}
        physical['velocity_m_s']=velocity.copy()
        physical['fluid_internal_energy_J_m']=physical['mass_per_length_kg_m']*physical['fluid_specific_internal_energy_J_kg']
        physical['fluid_kinetic_energy_J_m']=.5*physical['mass_per_length_kg_m']*velocity**2
        physical['line_total_energy_J_m']=a0*state[:,2]
        for values in physical.values():values.setflags(write=False)
        line=(a0*state).copy();line.setflags(write=False)
        return PhysicalElasticPipeSnapshot(float(time),line,physical)
    initial_snapshot=restore(initial,s.start_time_s);final=restore(native.conservative,native.step_times_s[-1])
    observations=tuple(restore(o.conservative,o.time_s) for o in native.observations)
    receipt=dict(reference_area_m2=a0,reference_inner_diameter_m=s.inner_diameter_m,
        ideal_fluid_parameters=dict(specific_gas_constant_J_kg_K=fluid_eos.specific_gas_constant_J_kg_K,gamma=fluid_eos.gamma,internal_energy_reference_J_kg=fluid_eos.internal_energy_reference_J_kg),
        area_compliance_m2_pa=q.area_compliance_m2_pa,exterior_pressure_pa_abs=q.exterior_pressure_pa_abs,fixed_wall_temperature_K=q.fixed_wall_temperature_K,maximum_reviewed_area_fractional_change=q.maximum_reviewed_area_fractional_change,
        area_law='A=A0+C*(p-pe), constant A0/C/pe at fixed wall temperature and axial length',
        normalized_owner_coordinates='rho_owner=M/A0; p_owner=Pi/A0; e_owner=(M*e_fluid+Psi)/M. None are material rho/p/e.',
        physical_phase='calorically perfect ideal gas; owner phase label refers to restored physical state',
        wave_and_cv='Owner sound speed is coupled-wave speed; owner cv is the line energy derivative at fixed M, not material sound speed/cv.',
        potential_identity='Psi=elastic+C*pe*(p-pe); Pi=A0*p+C*(p-pe)^2/2; Psi+Pi=p*A; dPsi=p*dA; dPi=A*dp',
        momentum_budget='Canonical momentum flux J*u+Pi absorbs wall-side geometric pressure force. It is not pure p*A boundary traction.',
        energy_budget='Line total energy is fluid internal+axial kinetic+elastic+exterior-pressure potential. Consistent energy flux equals physical fluid enthalpy flux pointwise; FV ledgers use numerical conservative face fluxes.',
        fixed_heat_conductance_per_length_W_m_K=q.constant_heat_conductance_per_length_W_m_K,native_reference_heat_transfer_W_m2_K=h,
        heat_basis='Prescribed U_per_length*(Tw-T); no wall heat capacity or fixed actual-perimeter*h inference.',
        restrictions=['Constant axial length/reference wall temperature and quasi-static area response; no wall inertia, axial motion, buckling, thermal expansion field or material calibration.',
            'Explicit user-reviewed small-area ceiling at most0.05 is an API support cap, not a universal physical linearity or safety limit.',
            'Horizontal/frictionless selected branch; no built-in physical pressure outflow/nozzle/reverse-air or arbitrary real-fluid EOS mapping.',
            'Other general coupled wall/fluid/soil/temperature/composition source variants remain unimplemented by this narrow field.'],
        field_or_material_or_standard_acceptance_qualified=False)
    return ElasticAreaGasFieldResult(digest(dict(request=q.model_dump(),ideal_fluid_parameters=receipt['ideal_fluid_parameters'])),initial_snapshot,final,observations,native.ledger,native,receipt)
