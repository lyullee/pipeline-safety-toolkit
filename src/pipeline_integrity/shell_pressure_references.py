"""Caller-conditioned shell pressure references, distinct from collapse prediction."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field,model_validator

from .contracts import Contract,Name,Nonnegative,Positive
from .nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa


class _PipeGeometry(Contract):
    outside_diameter_m: Positive | None = None
    wall_thickness_m: Positive | None = None

    @model_validator(mode='after')
    def hollow(self):
        if self.outside_diameter_m is not None and self.wall_thickness_m is not None:
            NominalHoopStressInput(differential_pressure_Pa=1.,outside_diameter_m=self.outside_diameter_m,
                                  wall_thickness_m=self.wall_thickness_m)
        return self


class ShortPipeHoopReferenceRequest(_PipeGeometry):
    supplied_compressive_hoop_limit_pa: Positive | None = None
    short_stiffened_regime_reviewed: bool = False


class IntermediateShellReferenceRequest(_PipeGeometry):
    length_m: Positive | None = None
    young_modulus_pa: Positive | None = None
    supplied_correlation_coefficient: Positive | None = None
    intermediate_length_and_coefficient_basis_reviewed: bool = False


class OvalShellInteractionRequest(Contract):
    elastic_reference_pressure_pa: Positive | None = None
    plastic_hoop_reference_pressure_pa: Positive | None = None
    reference_radius_m: Positive | None = None
    radius_basis_id: Name | None = None
    wall_thickness_m: Positive | None = None
    major_diameter_over_nominal_minus_one: Nonnegative | None = None
    reviewed_ovality_upper_bound: Nonnegative | None = None
    relative_equation_residual_tolerance: float = Field(default=1e-10,gt=0,le=1e-3)
    interaction_and_reference_pressure_bases_reviewed: bool = False


class ShellPressureReferenceResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','solver_withheld']
    method_variant: Literal['short_hoop_reference','intermediate_correlation','oval_quadratic_interaction']
    external_over_internal_reference_pressure_pa: Positive | None = None
    nominal_hoop_strain_magnitude: Nonnegative | None = None
    zero_ovality_reference_pressure_pa: Positive | None = None
    normalized_equation_residual: float | None = None
    radius_basis_id: Name | None = None
    issues: tuple[str,...] = ()
    empirical_buckling_or_plastic_collapse_qualified: Literal[False] = False


def _missing(q,names,review):
    result=['missing '+k for k in names if getattr(q,k) is None]
    if not getattr(q,review):result.append('declared pressure-reference regime and parameter basis require review')
    return tuple(result)


def evaluate_short_pipe_hoop_reference(request: ShortPipeHoopReferenceRequest) -> ShellPressureReferenceResult:
    """Invert the existing OD nominal hoop relation at a supplied compression limit."""
    if not isinstance(request,ShortPipeHoopReferenceRequest):raise TypeError('ShortPipeHoopReferenceRequest required')
    q=ShortPipeHoopReferenceRequest.model_validate(request.model_dump(mode='python'))
    base=dict(method_variant='short_hoop_reference')
    issues=_missing(q,('outside_diameter_m','wall_thickness_m','supplied_compressive_hoop_limit_pa'),'short_stiffened_regime_reviewed')
    if issues:return ShellPressureReferenceResult(status='needs_data',issues=issues,**base)
    unit_stress=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=1.,
        outside_diameter_m=q.outside_diameter_m,wall_thickness_m=q.wall_thickness_m))
    pressure=q.supplied_compressive_hoop_limit_pa/unit_stress
    if not math.isfinite(pressure) or pressure<=0:raise ValueError('hoop pressure reference is outside finite positive support')
    return ShellPressureReferenceResult(status='computed',external_over_internal_reference_pressure_pa=pressure,**base)


def evaluate_intermediate_shell_reference(request: IntermediateShellReferenceRequest) -> ShellPressureReferenceResult:
    """Evaluate the supplied-coefficient E*(t/D)^2.5/(L/D) screening relation.

    The coefficient and the intermediate-length regime are supplied. No regime
    boundary, material-specific coefficient, end stiffness or buckling mode is
    selected. Reported strain is only nominal hoop-stress magnitude divided by E.
    """
    if not isinstance(request,IntermediateShellReferenceRequest):raise TypeError('IntermediateShellReferenceRequest required')
    q=IntermediateShellReferenceRequest.model_validate(request.model_dump(mode='python'))
    base=dict(method_variant='intermediate_correlation')
    issues=_missing(q,('outside_diameter_m','wall_thickness_m','length_m','young_modulus_pa','supplied_correlation_coefficient'),
                    'intermediate_length_and_coefficient_basis_reviewed')
    if issues:return ShellPressureReferenceResult(status='needs_data',issues=issues,**base)
    log_pressure=(math.log(q.supplied_correlation_coefficient)+math.log(q.young_modulus_pa)
        +2.5*(math.log(q.wall_thickness_m)-math.log(q.outside_diameter_m))
        -(math.log(q.length_m)-math.log(q.outside_diameter_m)))
    try:pressure=math.exp(log_pressure)
    except OverflowError as exc:raise ValueError('intermediate pressure exceeds finite support') from exc
    if not math.isfinite(pressure) or pressure<=0:raise ValueError('intermediate pressure is outside finite positive support')
    hoop=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=pressure,
        outside_diameter_m=q.outside_diameter_m,wall_thickness_m=q.wall_thickness_m))
    strain=hoop/q.young_modulus_pa
    if not math.isfinite(strain):raise ValueError('nominal strain exceeds finite support')
    return ShellPressureReferenceResult(status='computed',external_over_internal_reference_pressure_pa=pressure,
        nominal_hoop_strain_magnitude=strain,**base)


def evaluate_oval_shell_interaction(request: OvalShellInteractionRequest) -> ShellPressureReferenceResult:
    """Select the lower positive root of the declared quadratic interaction.

    Ovality means D_major/D_nominal-1, not another common ovality definition.
    Both pressure references must share the explicitly declared radius/wall basis.
    A conjugate-root evaluation avoids cancellation near a small positive root.
    No empirical fit or full plastic/imperfection-dependent collapse is inferred.
    """
    if not isinstance(request,OvalShellInteractionRequest):raise TypeError('OvalShellInteractionRequest required')
    q=OvalShellInteractionRequest.model_validate(request.model_dump(mode='python'))
    base=dict(method_variant='oval_quadratic_interaction',radius_basis_id=q.radius_basis_id)
    fields=('elastic_reference_pressure_pa','plastic_hoop_reference_pressure_pa','reference_radius_m',
            'radius_basis_id','wall_thickness_m','major_diameter_over_nominal_minus_one','reviewed_ovality_upper_bound')
    issues=_missing(q,fields,'interaction_and_reference_pressure_bases_reviewed')
    if issues:return ShellPressureReferenceResult(status='needs_data',issues=issues,**base)
    delta=q.major_diameter_over_nominal_minus_one
    if delta>q.reviewed_ovality_upper_bound:
        return ShellPressureReferenceResult(status='out_of_scope',issues=('ovality exceeds supplied reviewed support',),**base)
    elastic=q.elastic_reference_pressure_pa;plastic=q.plastic_hoop_reference_pressure_pa
    limit=min(elastic,plastic)
    if delta==0:
        return ShellPressureReferenceResult(status='computed',external_over_internal_reference_pressure_pa=limit,
            zero_ovality_reference_pressure_pa=limit,normalized_equation_residual=0.,**base)
    amplification=1+6*(q.reference_radius_m/q.wall_thickness_m)*delta
    scale=max(elastic,plastic);u=plastic/scale;v=elastic/scale
    linear=u+amplification*v;constant=u*v
    if not math.isfinite(linear) or linear<=0 or constant<=0:
        raise ValueError('quadratic pressure references exceed numerical scale support')
    ratio=constant/linear
    discriminant=max(0.,1-4*(ratio/linear))
    root=2*ratio/(1+math.sqrt(discriminant))
    pressure=scale*root
    if not math.isfinite(pressure) or pressure<=0:raise ValueError('positive interaction root is below numerical support')
    residual=math.fsum((root*root,-linear*root,constant))/max(root*root,linear*root,constant)
    if abs(residual)>q.relative_equation_residual_tolerance:
        return ShellPressureReferenceResult(status='solver_withheld',normalized_equation_residual=residual,
            issues=('quadratic interaction residual exceeds supplied numerical tolerance',),**base)
    return ShellPressureReferenceResult(status='computed',external_over_internal_reference_pressure_pa=pressure,
        zero_ovality_reference_pressure_pa=limit,normalized_equation_residual=residual,**base)
