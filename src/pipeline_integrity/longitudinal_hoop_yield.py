"""Compressive hoop limit from a reviewed normalized hoop/axial yield ellipse."""
from pydantic import model_validator
from .contracts import Contract,Name,Positive,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job
from .nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa

class LongitudinalHoopYieldRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    hoop_yield_strength_pa: Positive | None = None
    axial_yield_strength_pa: Positive | None = None
    axial_stress_tension_positive_pa: float | None = None
    outside_diameter_m: Positive | None = None
    wall_thickness_m: Positive | None = None
    yield_basis_evidence_id: Name
    normalized_hoop_axial_yield_ellipse_and_stress_sign_reviewed: bool = False

    @model_validator(mode='after')
    def paired_geometry(self):
        if (self.outside_diameter_m is None)!=(self.wall_thickness_m is None):raise ValueError('nominal pressure geometry requires both diameter and wall')
        if self.outside_diameter_m is not None:
            NominalHoopStressInput(differential_pressure_Pa=1.,outside_diameter_m=self.outside_diameter_m,wall_thickness_m=self.wall_thickness_m)
        return self

class _Unavailable(Exception):pass

def evaluate_longitudinal_hoop_yield(request: LongitudinalHoopYieldRequest,context: RunContext,*,client=None):
    """Invert the existing invariant owner in explicitly virtual strength coordinates.

    Negative normalized hoop stress denotes compression. Dividing each normal
    stress by its supplied directional strength defines the reviewed ellipse;
    unequal strengths do not establish a general anisotropic plasticity law.
    The positive hoop-capacity branch is bracketed beyond its minimum-invariant
    point, so an initially compressive axial yield state is allowed to unload.
    Any optional pressure is an OD nominal hoop-yield reference, not collapse.
    """
    from scipy.optimize import brentq
    q=LongitudinalHoopYieldRequest.model_validate(request.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('yield/context identity differs')
    if q.yield_basis_evidence_id not in {e.id for e in c.evidence}:raise ValueError('yield basis evidence absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),plastic_collapse_curvature_buckling_or_acceptance_qualified=False,
        native_invariant_coordinates_are_virtual_normalized_strengths=True)
    if not q.normalized_hoop_axial_yield_ellipse_and_stress_sign_reviewed or any(v is None for v in
        (q.hoop_yield_strength_pa,q.axial_yield_strength_pa,q.axial_stress_tension_positive_pa)):
        return dict(status='needs_data',**base)
    axial=q.axial_stress_tension_positive_pa/q.axial_yield_strength_pa
    if abs(axial)>1:return dict(status='out_of_scope',issues=('axial load already exceeds supplied directional yield strength',),**base)
    client=MaterialClient() if client is None else client
    calls=0;last=None
    def residual(hoop):
        nonlocal calls,last
        job=prepare_material_job('mechanics.plane_stress',dict(stress_x_Pa=-hoop,stress_y_Pa=axial,shear_xy_Pa=0.),c,
            location_id=q.location_id,evidence_ids=(q.yield_basis_evidence_id,),client=client)
        last=evaluate_material_job(job,c,client=client);calls+=1
        if last.native.status!='computed':raise _Unavailable
        return last.native.payload['values']['von_mises_Pa']-1.
    try:
        hoop=brentq(residual,max(0.,-axial/2),2.,xtol=1e-12,rtol=1e-12)
        error=residual(hoop)
    except _Unavailable:return dict(status='solver_withheld',native_invariant_calls=calls,**base)
    adjusted=hoop*q.hoop_yield_strength_pa;pressure=None
    if q.outside_diameter_m is not None:
        unit=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=1.,outside_diameter_m=q.outside_diameter_m,wall_thickness_m=q.wall_thickness_m))
        pressure=adjusted/unit
    return dict(status='computed',**base,axial_strength_ratio=axial,compressive_hoop_strength_ratio=hoop,
        adjusted_compressive_hoop_yield_strength_pa=adjusted,optional_od_nominal_external_pressure_hoop_yield_reference_pa=pressure,
        normalized_yield_residual=error,native_invariant_calls=calls,native_invariant_receipt=last.model_dump(mode='json'))
