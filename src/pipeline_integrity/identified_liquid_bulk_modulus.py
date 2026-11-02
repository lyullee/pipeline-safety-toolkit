"""Local isothermal/acoustic moduli of an explicitly identified liquid EOS.

Gravity alone does not identify a crude-oil composition or its PVT model. This
observer never reconstructs a generic oil EOS from an API-gravity coordinate.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Positive
from .thermo import CheckedProvider,CoolPropProvider,StateRequest


class LiquidBulkModulusRequest(Contract):
    fluid_model: Name | None
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    pressure_Pa_abs: Positive | None
    temperature_K: Positive | None
    derivative_basis: Literal['isothermal','isentropic','both'] | None
    material_assignment_id: Name | None
    modeled_material_kind: Literal['characterized_liquid_EOS','identified_hydrocarbon_surrogate','caller_calibrated_liquid_provider']
    identified_material_and_local_liquid_PT_domain_reviewed: bool = False
    reference_API_gravity: float | None = None
    pressure_probe_step_Pa: Positive | None = None
    derivative_relative_tolerance: Positive = Field(default=1e-5,le=.01)


class LiquidBulkModulusResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','not_converged']
    material_assignment_id: Name | None
    derivative_basis: str | None
    density_kg_m3: Positive | None = None
    isothermal_compressibility_Pa_inv: Positive | None = None
    isothermal_bulk_modulus_Pa: Positive | None = None
    isentropic_bulk_modulus_Pa: Positive | None = None
    density_pressure_derivative_kg_m3_Pa: Positive | None = None
    pressure_probe_step_used_Pa: Positive | None = None
    derivative_relative_change: float | None = None
    acoustic_to_isothermal_modulus_ratio: Positive | None = None
    cp_over_cv: Positive | None = None
    issues: tuple[str,...] = ()
    generic_crude_from_API_gravity_inferred: Literal[False] = False
    pipe_wall_wave_speed_or_field_calibration_qualified: Literal[False] = False


def observe_identified_liquid_bulk_modulus(q: LiquidBulkModulusRequest, *, property_provider=None) -> LiquidBulkModulusResult:
    """Observe tangent bulk moduli with a reviewed material/property assignment.

    KT=rho/(d rho/dP at constant T); KS=rho*a² for the unbounded fluid. A
    five-point centered density derivative is checked against a halved step.
    Neither is automatically substituted into a flexible-pipe acoustic model.
    Defined surrogate-EOS checks are not validation of an uncharacterized crude.
    """
    q=LiquidBulkModulusRequest.model_validate(q.model_dump(mode='python'))
    base=dict(material_assignment_id=q.material_assignment_id,derivative_basis=q.derivative_basis)
    if None in (q.fluid_model,q.pressure_Pa_abs,q.temperature_K,q.derivative_basis,q.material_assignment_id) or not q.identified_material_and_local_liquid_PT_domain_reviewed:
        return LiquidBulkModulusResult(status='needs_data',**base,issues=('identified material/EOS, explicit PT and thermal derivative basis required; API gravity alone is insufficient',))
    provider=CheckedProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False));identity=None
    def state(p):
        nonlocal identity
        s=provider.resolve(StateRequest(fluid=q.fluid_model,backend=q.backend,inputs={'P':p,'T':q.temperature_K}))
        if s.phase not in {'liquid','supercritical_liquid'}:raise ValueError('all local derivative probes must remain on the reviewed liquid branch')
        marker=(s.provider_version,s.provider_revision)
        if identity is not None and identity!=marker:raise ValueError('property provider identity changed across pressure probes')
        identity=marker
        if not math.isclose(s.pressure_pa_abs,p,rel_tol=1e-10,abs_tol=1e-6) or not math.isclose(s.temperature_k,q.temperature_K,rel_tol=1e-10):raise ValueError('property provider changed requested pressure/temperature')
        return s
    p=q.pressure_Pa_abs
    h=q.pressure_probe_step_Pa if q.pressure_probe_step_Pa is not None else min(max(1000.,p*.001),p/8)
    if p-2*h<=0:return LiquidBulkModulusResult(status='out_of_scope',**base,issues=('positive absolute pressures required throughout centered derivative support',))
    try:
        center=state(p)
        def derivative(step):
            rm2=state(p-2*step).density_kg_m3;rm1=state(p-step).density_kg_m3
            rp1=state(p+step).density_kg_m3;rp2=state(p+2*step).density_kg_m3
            return ((rm2-rp2)+8*(rp1-rm1))/(12*step)
        coarse=derivative(h);fine=derivative(h/2)
    except (ValueError,RuntimeError) as exc:return LiquidBulkModulusResult(status='out_of_scope',**base,issues=(str(exc),))
    if fine<=0 or not math.isfinite(fine):return LiquidBulkModulusResult(status='out_of_scope',**base,issues=('positive stable finite density-pressure response required',))
    change=abs(fine-coarse)/fine
    if change>q.derivative_relative_tolerance:return LiquidBulkModulusResult(status='not_converged',**base,derivative_relative_change=change,issues=('density-pressure derivative did not agree under step refinement',))
    rho=center.density_kg_m3;kt=rho/fine;ks=None if center.speed_of_sound_m_s is None else rho*center.speed_of_sound_m_s**2
    ratio=None if ks is None else ks/kt;cp_cv=None if center.cp_j_kg_k is None or center.cv_j_kg_k is None else center.cp_j_kg_k/center.cv_j_kg_k
    data=dict(density_kg_m3=rho,isothermal_compressibility_Pa_inv=fine/rho,isothermal_bulk_modulus_Pa=kt,
        isentropic_bulk_modulus_Pa=ks,density_pressure_derivative_kg_m3_Pa=fine,pressure_probe_step_used_Pa=h/2,
        derivative_relative_change=change,acoustic_to_isothermal_modulus_ratio=ratio,cp_over_cv=cp_cv)
    if q.derivative_basis in {'isentropic','both'} and ks is None:
        return LiquidBulkModulusResult(status='needs_data',**base,**data,issues=('isothermal response is known; EOS sound speed required for isentropic modulus',))
    return LiquidBulkModulusResult(status='computed',**base,**data)
