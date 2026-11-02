"""First elastic von-Mises threshold via the existing closed-cylinder solver."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .adapters import MaterialClient
from .contracts import Contract, Nonnegative, Positive, Pressure, RunContext, digest
from .material_exchange import PressureSection, evaluate_pressure_section


class ElasticCylinderFirstYieldRequest(Contract):
    section: PressureSection
    yield_stress_pa: Positive | None = None
    maximum_excess_pressure_pa: Positive
    pressure_absolute_tolerance_pa: Positive = 1e-4
    pressure_relative_tolerance: float = Field(default=1e-10,ge=1e-14,le=1e-3)
    maximum_root_iterations: int = Field(default=80,ge=1,le=1000)
    yield_absolute_tolerance_pa: Nonnegative = 1e-3
    yield_relative_tolerance: float = Field(default=1e-7,ge=0,le=1e-2)
    homogeneous_small_strain_elastic_first_yield_reviewed: bool = False

    @model_validator(mode='after')
    def unresolved_internal_pressure(self):
        if self.section.internal_pressure is not None:
            raise ValueError('leave internal pressure unset for pressure-threshold inversion')
        return self


class ElasticCylinderFirstYieldResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','threshold_not_bracketed','solver_withheld']
    request_digest: str
    context_digest: str
    predicted_first_yield_excess_pressure_pa: Nonnegative | None = None
    predicted_internal_pressure_pa_abs: Positive | None = None
    controlling_radius_m: Positive | None = None
    peak_von_mises_stress_pa: Nonnegative | None = None
    yield_residual_pa: float | None = None
    root_iterations: int | None = None
    native_section_evaluations: int = 0
    pressure_section_record: dict | None = None
    issues: tuple[str,...] = ()
    elastic_threshold_only: Literal[True] = True
    plastic_collapse_or_burst_predicted: Literal[False] = False


class _SectionUnavailable(Exception):
    def __init__(self,record):self.record=record


def _equivalent_stresses(record):
    values=[row['native']['payload']['values'] for row in record['material_records']]
    return [math.hypot(v['hoop_stress_Pa']-v['axial_stress_Pa'],
                       v['axial_stress_Pa']-v['radial_stress_Pa'],
                       v['radial_stress_Pa']-v['hoop_stress_Pa'])/math.sqrt(2.) for v in values]


def evaluate_elastic_cylinder_first_yield(request: ElasticCylinderFirstYieldRequest,
                                          context: RunContext,*,client=None) -> ElasticCylinderFirstYieldResult:
    """Invert an elastic stress solution; do not extrapolate it into plastic collapse.

    The cylinder is homogeneous, axisymmetric and pressure-loaded at both closed
    ends. External absolute pressure stays fixed while the internal pressure is
    increased. In this declared Lamé branch equivalent stress decreases with
    radius, so bore and outside tractions suffice during the scalar search; the
    final receipt returns every originally requested radial output. This uses
    actual annular geometry rather than a thin-wall membrane approximation.
    """
    if not isinstance(request,ElasticCylinderFirstYieldRequest):
        raise TypeError('ElasticCylinderFirstYieldRequest required')
    q=ElasticCylinderFirstYieldRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    section=q.section
    if (section.asset_id,section.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError('section and context asset/clock differ')
    base=dict(request_digest=digest(q),context_digest=digest(c))
    if not section.pressure_loaded_closed_ends:
        return ElasticCylinderFirstYieldResult(status='out_of_scope',**base,
            issues=('this threshold inversion is restricted to pressure-loaded closed ends',))
    issues=[]
    if q.yield_stress_pa is None:issues.append('missing caller-supplied yield stress')
    if section.external_pressure is None:issues.append('missing external pressure reference')
    if not q.homogeneous_small_strain_elastic_first_yield_reviewed:
        issues.append('homogeneous small-strain elastic first-yield assumptions require review')
    if issues:return ElasticCylinderFirstYieldResult(status='needs_data',issues=tuple(issues),**base)
    external=section.external_pressure.absolute_pa()
    high=external+q.maximum_excess_pressure_pa
    if not math.isfinite(high) or high==external:
        return ElasticCylinderFirstYieldResult(status='solver_withheld',**base,
            issues=('pressure increment is outside finite resolved absolute-pressure support',))
    client=MaterialClient() if client is None else client
    search_section=section.model_copy(update={'evaluation_radii_m':(section.inner_radius_m,section.outer_radius_m)})
    cache={}
    def profile(excess,full=False):
        selected=section if full else search_section
        key=(float(excess),tuple(selected.evaluation_radii_m))
        if key not in cache:
            state=selected.model_copy(update={'internal_pressure':Pressure(
                value=external+excess,unit='Pa',reference='absolute')})
            record=evaluate_pressure_section(state,c,client=client)
            if record['status']!='computed':raise _SectionUnavailable(record)
            equivalent=_equivalent_stresses(record)
            if not equivalent or not all(math.isfinite(v) for v in equivalent):
                raise ValueError('nonfinite elastic equivalent stress')
            cache[key]=(record,equivalent)
        return cache[key]
    def objective(excess):return max(profile(excess)[1])-q.yield_stress_pa
    try:
        if objective(0.)>=0:
            return ElasticCylinderFirstYieldResult(status='solver_withheld',**base,
                native_section_evaluations=len(cache),issues=('zero-differential elastic state already exceeds supplied threshold',))
        upper_residual=objective(q.maximum_excess_pressure_pa)
        if upper_residual<0:
            return ElasticCylinderFirstYieldResult(status='threshold_not_bracketed',**base,
                native_section_evaluations=len(cache),issues=('supplied maximum excess pressure does not bracket first yield',))
        if upper_residual==0:
            root=q.maximum_excess_pressure_pa;iterations=0
        else:
            from scipy.optimize import brentq
            root,info=brentq(objective,0.,q.maximum_excess_pressure_pa,
                xtol=q.pressure_absolute_tolerance_pa,rtol=q.pressure_relative_tolerance,
                maxiter=q.maximum_root_iterations,full_output=True,disp=False)
            if not info.converged:
                return ElasticCylinderFirstYieldResult(status='solver_withheld',**base,
                    native_section_evaluations=len(cache),issues=('pressure root did not converge',))
            iterations=info.iterations
        record,equivalent=profile(root,full=True)
    except _SectionUnavailable as exc:
        return ElasticCylinderFirstYieldResult(status='solver_withheld',**base,
            native_section_evaluations=len(cache),pressure_section_record=exc.record,
            issues=('existing elastic section solver withheld a required trial',))
    peak=max(equivalent);index=equivalent.index(peak)
    residual=peak-q.yield_stress_pa
    if abs(residual)>q.yield_absolute_tolerance_pa+q.yield_relative_tolerance*q.yield_stress_pa:
        return ElasticCylinderFirstYieldResult(status='solver_withheld',**base,
            native_section_evaluations=len(cache),pressure_section_record=record,
            issues=('root stress residual exceeds supplied numerical tolerance',))
    return ElasticCylinderFirstYieldResult(status='computed',**base,
        predicted_first_yield_excess_pressure_pa=root,predicted_internal_pressure_pa_abs=external+root,
        controlling_radius_m=section.evaluation_radii_m[index],peak_von_mises_stress_pa=peak,
        yield_residual_pa=residual,root_iterations=iterations,native_section_evaluations=len(cache),
        pressure_section_record=record)
