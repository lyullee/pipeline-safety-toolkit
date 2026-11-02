"""Coulomb axial resistance integrated over a supplied cylindrical contact field."""
from __future__ import annotations
import math
import warnings
from typing import Literal,Protocol
from pydantic import Field,model_validator
from scipy.integrate import quad,IntegrationWarning
from .contracts import Contract,Name,Positive,Nonnegative


class ContactPressureProvider(Protocol):
    def normal_pressure_Pa(self, axial_position_m: float, circumference_angle_rad: float) -> float: ...


class AxialContactFrictionRequest(Contract):
    asset_id: Name
    clock_id: Name
    contact_pressure_basis_id: Name
    outside_diameter_m: Positive
    contact_length_m: Positive
    supplied_friction_coefficient: Nonnegative | None
    coefficient_basis: Literal['static','kinetic']
    mode: Literal['sliding','static_bound']
    signed_axial_sliding_direction: Literal[-1,1] | None = None
    uniform_axial_slip_speed_m_s: Positive | None = None
    angle_breakpoints_rad: tuple[float,...] = ()
    axial_breakpoints_m: tuple[float,...] = ()
    absolute_normal_force_tolerance_N: Positive = 1e-7
    relative_quadrature_tolerance: Positive = Field(default=1e-9,lt=1e-2)
    maximum_subdivisions: int = Field(default=200,ge=1,le=10000)
    known_normal_pressure_full_cylindrical_surface_coulomb_and_no_adhesion_reviewed: bool = False

    @model_validator(mode='after')
    def selected_observation(self):
        if self.coefficient_basis!=('static' if self.mode=='static_bound' else 'kinetic'):
            raise ValueError('static and kinetic friction coefficients must match the selected observation')
        if self.mode=='static_bound' and (self.signed_axial_sliding_direction is not None or self.uniform_axial_slip_speed_m_s is not None):
            raise ValueError('static friction bound does not prescribe a sliding direction or speed')
        for points,upper in ((self.angle_breakpoints_rad,2*math.pi),(self.axial_breakpoints_m,self.contact_length_m)):
            if any(p<=0 or p>=upper for p in points) or any(b<=a for a,b in zip(points,points[1:])):
                raise ValueError('quadrature breakpoints must increase strictly inside their physical support')
            if len(points)>=self.maximum_subdivisions:raise ValueError('quadrature subdivision budget must exceed the breakpoint count')
        return self


class AxialContactFrictionResult(Contract):
    status: Literal['computed','needs_data','not_converged']
    asset_id: Name
    clock_id: Name
    integrated_normal_pressure_load_N: Nonnegative | None = None
    axial_friction_capacity_N: Nonnegative | None = None
    mean_axial_resistance_N_m: Nonnegative | None = None
    signed_axial_drag_force_N: float | None = None
    static_axial_force_interval_N: tuple[float,float] | None = None
    friction_dissipation_W: Nonnegative | None = None
    quadrature_reported_error_estimate_N: Nonnegative | None = None
    issues: tuple[str,...] = ()
    soil_contact_pressure_or_transition_length_predicted: Literal[False] = False


def integrate_axial_contact_friction(q: AxialContactFrictionRequest,
    *, contact_pressure_provider: ContactPressureProvider | None) -> AxialContactFrictionResult:
    """Integrate the scalar normal-pressure load; opposing radial vectors do not cancel friction.

    The caller supplies the compressive contact pressure everywhere, using zero
    on noncontact portions. A slip direction identifies actual kinetic drag;
    without slipping the result is only the symmetric Coulomb force bound.
    Soil overburden, lateral earth pressure, adhesion, spring stiffness and
    coating friction data are not inferred from burial depth or pipe weight.
    """
    if not isinstance(q,AxialContactFrictionRequest):raise TypeError('AxialContactFrictionRequest required')
    q=AxialContactFrictionRequest.model_validate(q.model_dump(mode='python'))
    base=dict(asset_id=q.asset_id,clock_id=q.clock_id)
    if (contact_pressure_provider is None or q.supplied_friction_coefficient is None
        or not q.known_normal_pressure_full_cylindrical_surface_coulomb_and_no_adhesion_reviewed
        or (q.mode=='sliding' and q.signed_axial_sliding_direction is None)):
        return AxialContactFrictionResult(status='needs_data',**base,
            issues=('known reviewed pressure field, friction coefficient and selected slip/bound observation required',))
    inner_errors=[];radius=q.outside_diameter_m/2
    def force_per_length(x):
        def integrand(angle):
            p=contact_pressure_provider.normal_pressure_Pa(x,angle)
            if not isinstance(p,(float,int)) or not math.isfinite(p) or p<0:
                raise ValueError('contact pressure must be finite nonnegative Pa, with zero on open contact')
            return p*radius
        value,error=quad(integrand,0,2*math.pi,points=q.angle_breakpoints_rad or None,
            epsabs=q.absolute_normal_force_tolerance_N/(2*q.contact_length_m),
            epsrel=q.relative_quadrature_tolerance,limit=q.maximum_subdivisions)
        inner_errors.append(error);return value
    with warnings.catch_warnings():
        warnings.simplefilter('error',IntegrationWarning)
        try:
            load,outer_error=quad(force_per_length,0,q.contact_length_m,points=q.axial_breakpoints_m or None,
                epsabs=q.absolute_normal_force_tolerance_N/2,epsrel=q.relative_quadrature_tolerance,limit=q.maximum_subdivisions)
        except IntegrationWarning:
            return AxialContactFrictionResult(status='not_converged',**base,issues=('pressure-field quadrature did not converge within its subdivision/error budget',))
    error=outer_error+q.contact_length_m*max(inner_errors,default=0.)
    if error>q.absolute_normal_force_tolerance_N+q.relative_quadrature_tolerance*abs(load):
        return AxialContactFrictionResult(status='not_converged',**base,quadrature_reported_error_estimate_N=error,
            issues=('combined reported pressure-integral error exceeds requested normal-force budget',))
    capacity=q.supplied_friction_coefficient*load;mean=capacity/q.contact_length_m
    drag=None if q.mode=='static_bound' else -q.signed_axial_sliding_direction*capacity
    dissipation=None if q.uniform_axial_slip_speed_m_s is None else capacity*q.uniform_axial_slip_speed_m_s
    if not all(math.isfinite(v) for v in (load,error,capacity,mean,*(() if dissipation is None else (dissipation,)))):
        raise ValueError('contact force ledger exceeds finite numerical support')
    return AxialContactFrictionResult(status='computed',**base,integrated_normal_pressure_load_N=load,
        axial_friction_capacity_N=capacity,mean_axial_resistance_N_m=mean,signed_axial_drag_force_N=drag,
        static_axial_force_interval_N=(-capacity,capacity) if q.mode=='static_bound' else None,
        friction_dissipation_W=dissipation,quadrature_reported_error_estimate_N=error)
