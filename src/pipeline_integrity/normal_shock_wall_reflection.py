"""Calorically perfect gas normal incident shock reflected from a rigid wall."""
import math
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Name

THEORY_SOURCES=('https://www.grc.nasa.gov/www/k-12/airplane/detach.html',)

class NormalShockWallReflectionRequest(Contract):
    ambient_pressure_pa_abs: Positive
    ambient_mass_density_kg_m3: Positive
    heat_capacity_ratio: float = Field(gt=1,le=2)
    incident_overpressure_pa: Nonnegative
    maximum_reviewed_incident_absolute_pressure_ratio: Positive
    gas_state_planar_wave_and_wall_basis_id: Name
    calorically_perfect_planar_shock_and_rigid_normal_wall_reviewed: bool = False

def evaluate_normal_shock_wall_reflection(request: NormalShockWallReflectionRequest):
    """Solve the two local shock jumps with a zero-velocity wall state.

    Incident-wave strength is supplied; no blast source or propagation model
    is inferred. Both jumps obey ideal-gas mass, momentum and energy conditions.
    The reflected pressure jump follows a positive quadratic shock-curve root,
    rather than a pressure-polynomial fit. Oblique reflection, real-gas heat
    capacities, wall compliance, finite blast duration and target damage are
    separate conditions and are not represented by this local wall state.
    """
    q=NormalShockWallReflectionRequest.model_validate(request.model_dump())
    p0=q.ambient_pressure_pa_abs;rho0=q.ambient_mass_density_kg_m3;g=q.heat_capacity_ratio;dp=q.incident_overpressure_pa
    base=dict(basis_id=q.gas_state_planar_wave_and_wall_basis_id,actual_blast_field_or_target_response_qualified=False)
    if not q.calorically_perfect_planar_shock_and_rigid_normal_wall_reviewed:return dict(status='needs_data',**base)
    p1=p0+dp
    if p1/p0>q.maximum_reviewed_incident_absolute_pressure_ratio:return dict(status='out_of_scope',**base)
    if dp==0:return dict(status='computed',**base,incident_pressure_pa_abs=p0,incident_mass_density_kg_m3=rho0,incident_velocity_toward_wall_m_s=0.,
        reflected_pressure_pa_abs=p0,reflected_overpressure_pa=0.,reflected_mass_density_kg_m3=rho0,wall_velocity_m_s=0.,incident_shock_speed_m_s=None,reflected_shock_speed_m_s=None)
    density_ratio=lambda pdown,pup:((g+1)*pdown+(g-1)*pup)/((g-1)*pdown+(g+1)*pup)
    incident_speed=math.sqrt(((g+1)*p1+(g-1)*p0)/(2*rho0))
    u1=dp/(rho0*incident_speed);rho1=rho0*density_ratio(p1,p0)
    A=2/((g+1)*rho1);B=(g-1)/(g+1)*p1
    jump=(u1*u1+math.hypot(u1*u1,2*u1*math.sqrt(A*(p1+B))))/(2*A)
    p2=p1+jump;rho2=rho1*density_ratio(p2,p1)
    reflected_mass_flux=math.sqrt(rho1*((g+1)*p2+(g-1)*p1)/2)
    reflected_speed=u1-reflected_mass_flux/rho1
    return dict(status='computed',**base,incident_pressure_pa_abs=p1,incident_mass_density_kg_m3=rho1,incident_velocity_toward_wall_m_s=u1,
        reflected_pressure_pa_abs=p2,reflected_overpressure_pa=p2-p0,reflected_mass_density_kg_m3=rho2,wall_velocity_m_s=0.,
        incident_shock_speed_m_s=incident_speed,reflected_shock_speed_m_s=reflected_speed,
        computed_wall_velocity_residual_m_s=reflected_speed+reflected_mass_flux/rho2)
