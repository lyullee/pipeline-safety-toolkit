"""Nominal closed-cap pressure plus elastic bending tensile-budget observer.

This combines a declared membrane pressure coordinate with prescribed elastic
curvature. It is not a restrained axial field, multiaxial yield criterion,
plastic collapse solution, installation analysis or design-code acceptance.
"""
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Name,Positive,Nonnegative,digest
from .nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa


class PressureBendingBudgetRequest(Contract):
    id: Name
    inside_pressure_pa_abs: Nonnegative
    exterior_pressure_pa_abs: Nonnegative
    actual_outside_diameter_m: Positive
    actual_wall_thickness_m: Positive
    pressure_diameter_basis: Literal['outside','mean_from_outside']
    young_modulus_pa: Positive
    supplied_longitudinal_tensile_stress_ceiling_pa: Positive
    signed_prescribed_curvature_per_m: float | None = None
    supplied_allowable_and_membrane_free_closed_caps_elastic_bending_reviewed: bool = False
    stress_ceiling_evidence_id: Name

    @model_validator(mode='after')
    def geometry(self):
        if self.actual_wall_thickness_m>=self.actual_outside_diameter_m/2:raise ValueError('positive hollow circular section required')
        return self


def evaluate_pressure_bending_budget(q: PressureBendingBudgetRequest):
    """Observe nominal longitudinal stress and invert the supplied tensile ceiling.

    The straight pipe has pressure-loaded free closed caps. Axial stress relative
    to the common exterior hydrostatic state is one half of the selected nominal
    hoop stress. The bending-only neutral axis is the circular section centroid;
    tensile-positive fibre strain is kappa*y, with y positive toward positive
    bending tension. Actual outside radius locates the two extreme fibres.
    Only the maximum longitudinal tensile stress is compared with the caller
    ceiling; compression and multiaxial material failure are not assessed.
    """
    q=PressureBendingBudgetRequest.model_validate(q.model_dump())
    base=dict(request_digest=digest(q),field_or_design_acceptance_qualified=False)
    if not q.supplied_allowable_and_membrane_free_closed_caps_elastic_bending_reviewed:return dict(status='needs_data',**base)
    dp=q.inside_pressure_pa_abs-q.exterior_pressure_pa_abs
    if dp<0:return dict(status='out_of_scope',reason='external-pressure-dominated states need another physical basis',**base)
    pressure_stress=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=dp,
        outside_diameter_m=q.actual_outside_diameter_m,wall_thickness_m=q.actual_wall_thickness_m,
        diameter_basis=q.pressure_diameter_basis))/2
    radius=q.actual_outside_diameter_m/2
    gradient=q.young_modulus_pa*radius
    budget=q.supplied_longitudinal_tensile_stress_ceiling_pa-pressure_stress
    if not math.isfinite(gradient) or gradient<=0:raise ValueError('elastic extreme-fibre scale exceeds finite support')
    values=dict(differential_pressure_pa=dp,nominal_closed_cap_axial_stress_pa=pressure_stress,
        pressure_diameter_basis=q.pressure_diameter_basis,bending_neutral_axis='section_centroid',actual_extreme_fibre_distance_m=radius,
        remaining_tensile_stress_budget_pa=budget,compression_or_multiaxial_capacity_assessed=False)
    if budget<0:values.update(inverse_status='no_curvature_satisfies_tensile_ceiling',maximum_absolute_curvature_per_m=None,minimum_absolute_bend_radius_m=None)
    elif budget==0:values.update(inverse_status='straight_only',maximum_absolute_curvature_per_m=0.,minimum_absolute_bend_radius_m=None)
    else:
        maximum=budget/gradient
        if maximum<=0 or not math.isfinite(1/maximum):raise ValueError('curvature inverse exceeds finite support')
        values.update(inverse_status='finite_curvature_interval',maximum_absolute_curvature_per_m=maximum,minimum_absolute_bend_radius_m=1/maximum)
    if q.signed_prescribed_curvature_per_m is not None:
        bending=gradient*q.signed_prescribed_curvature_per_m
        positive=pressure_stress+bending;negative=pressure_stress-bending
        if not all(math.isfinite(v) for v in (bending,positive,negative)):raise ValueError('fibre stress exceeds finite support')
        values.update(signed_bending_extreme_fibre_stress_pa=bending,positive_coordinate_fibre_longitudinal_stress_pa=positive,
            negative_coordinate_fibre_longitudinal_stress_pa=negative,maximum_longitudinal_tensile_stress_pa=max(positive,negative),
            minimum_longitudinal_stress_pa=min(positive,negative),tensile_ceiling_residual_pa=q.supplied_longitudinal_tensile_stress_ceiling_pa-max(positive,negative))
    return dict(status='computed',**base,values=values)
