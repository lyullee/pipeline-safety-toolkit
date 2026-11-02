"""Proof-survival inversion of a reviewed surface-flaw failure-pressure curve.

The pressure curve is supplied under a declared material, crack-shape and
loading basis. Its calibration is never replaced by a blunt metal-loss curve.
Ordered growth continues through the existing material crack-growth owner.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, digest
from .fracture_exchange import CrackGrowthRequest, assess_crack_growth


class SurfaceFlawProofRequest(Contract):
    basis_id: Name
    flaw_family_id: Name
    material_condition_id: Name
    pressure_reference: Literal['inside_minus_outside'] = 'inside_minus_outside'
    wall_thickness_m: Positive
    depth_m: tuple[Positive, ...] = Field(min_length=2)
    failure_pressure_pa: tuple[Positive, ...] = Field(min_length=2)
    proof_pressure_pa: Positive
    maximum_operating_pressure_pa: Positive
    monotonic_elastic_proof_and_same_flaw_family_reviewed: bool = False
    failure_pressure_curve_applicability_reviewed: bool = False
    growth_pressure_to_opening_stress_mapping_reviewed: bool = False
    tearing_curve_compatibility_reviewed: bool = False

    @model_validator(mode="after")
    def curve(self):
        if len(self.depth_m) != len(self.failure_pressure_pa):
            raise ValueError("each flaw depth requires one failure pressure")
        if any(b <= a for a, b in zip(self.depth_m, self.depth_m[1:])):
            raise ValueError("flaw depths must strictly increase without sorting")
        if self.depth_m[-1] >= self.wall_thickness_m:
            raise ValueError("surface-flaw curve must remain below through-wall depth")
        if any(b > a for a, b in zip(self.failure_pressure_pa, self.failure_pressure_pa[1:])):
            raise ValueError("failure pressure must not increase with flaw depth")
        if self.maximum_operating_pressure_pa > self.proof_pressure_pa:
            raise ValueError("operating pressure exceeds the declared proof boundary")
        return self


def _upper_depth_at_pressure(q, pressure):
    """Rightmost supported depth whose supplied capacity is at least pressure."""
    if pressure > q.failure_pressure_pa[0]:
        return None, "no_supported_survivor"
    if pressure < q.failure_pressure_pa[-1]:
        return q.depth_m[-1], "upper_depth_censored_by_curve_support"
    # Repeated capacities are valid; choose the deepest equal-capacity knot.
    index = bisect_right([-p for p in q.failure_pressure_pa], -pressure) - 1
    if q.failure_pressure_pa[index] == pressure:
        return q.depth_m[index], "supported_boundary"
    pa, pb = q.failure_pressure_pa[index:index + 2]
    da, db = q.depth_m[index:index + 2]
    return da + (pa - pressure) / (pa - pb) * (db - da), "supported_boundary"


def evaluate_surface_flaw_proof(request: SurfaceFlawProofRequest):
    q = SurfaceFlawProofRequest.model_validate(request.model_dump())
    base = dict(request_digest=digest(q), basis_id=q.basis_id, flaw_family_id=q.flaw_family_id,
                source_curve_or_standard_acceptance_qualified=False,
                proof_induced_plasticity_or_tearing_predicted=False)
    if not (q.monotonic_elastic_proof_and_same_flaw_family_reviewed
            and q.failure_pressure_curve_applicability_reviewed):
        return dict(status="needs_data", **base)
    initial, initial_status = _upper_depth_at_pressure(q, q.proof_pressure_pa)
    terminal, terminal_status = _upper_depth_at_pressure(q, q.maximum_operating_pressure_pa)
    return dict(status="computed", **base, proof_limited_initial_depth_m=initial,
                proof_bound_status=initial_status,
                operating_failure_depth_m=terminal, operating_boundary_status=terminal_status,
                pressure_capacity_interpolation="piecewise_linear_no_extrapolation")


def bind_proof_limited_growth(request: SurfaceFlawProofRequest, template: CrackGrowthRequest):
    """Use a resolved proof-survivor bound as the conservative initial depth.

    The caller must review the same flaw family and pressure-to-opening-stress
    mapping in the growth template. Censored endpoints cannot define a failure
    life. Proof-induced residual stress and retardation need a separate model.
    """
    q = SurfaceFlawProofRequest.model_validate(request.model_dump())
    template = CrackGrowthRequest.model_validate(template.model_dump())
    if (template.flaw_id != q.flaw_family_id or template.wall_thickness_m != q.wall_thickness_m
            or template.material_condition_id != q.material_condition_id):
        raise ValueError("proof and growth flaw identity or wall thickness differ")
    if not q.growth_pressure_to_opening_stress_mapping_reviewed:
        raise ValueError("pressure-to-opening-stress mapping requires explicit review")
    observation = evaluate_surface_flaw_proof(q)
    if observation['status'] != 'computed':
        raise ValueError("reviewed proof-curve observation is required")
    if observation['proof_bound_status'] != 'supported_boundary' or observation['operating_boundary_status'] != 'supported_boundary':
        raise ValueError("uncensored proof and operating failure boundaries are required")
    return CrackGrowthRequest.model_validate({
        **template.model_dump(),
        'initial_depth_m': observation['proof_limited_initial_depth_m'],
        'terminal_depth_m': observation['operating_failure_depth_m'],
    })


def evaluate_proof_limited_growth(request, template, context, *, client=None):
    bound = bind_proof_limited_growth(request, template)
    result = assess_crack_growth(bound, context, client=client)
    return dict(proof_observation=evaluate_surface_flaw_proof(request),
                growth_request_digest=digest(bound), growth_receipt=result)


def bind_proof_limited_tearing(request: SurfaceFlawProofRequest, template):
    """Bind a resolved elastic proof bound to subsequent time-dependent tearing.

    The history starts after the proof hold. Tearing during proof itself requires
    an independently known pre-proof initial depth and direct history evaluation.
    """
    from .time_dependent_tearing import PressureHistoryTearingRequest

    q = SurfaceFlawProofRequest.model_validate(request.model_dump())
    template = PressureHistoryTearingRequest.model_validate(template.model_dump())
    if not q.tearing_curve_compatibility_reviewed:
        raise ValueError('failure-pressure and tearing-driving/resistance compatibility requires review')
    if (template.flaw_family_id != q.flaw_family_id
            or template.material_condition_id != q.material_condition_id
            or template.wall_thickness_m != q.wall_thickness_m):
        raise ValueError('proof and tearing flaw/material/wall identities differ')
    if max(template.differential_pressure_pa) > q.maximum_operating_pressure_pa:
        raise ValueError('tearing history exceeds the proof-curve operating-pressure boundary')
    observation = evaluate_surface_flaw_proof(q)
    if (observation['status'] != 'computed'
            or observation['proof_bound_status'] != 'supported_boundary'
            or observation['operating_boundary_status'] != 'supported_boundary'):
        raise ValueError('uncensored reviewed proof and operating boundaries are required')
    return PressureHistoryTearingRequest.model_validate({
        **template.model_dump(),
        'initial_depth_m': observation['proof_limited_initial_depth_m'],
        'rupture_depth_m': observation['operating_failure_depth_m'],
        'initial_peak_pressure_pa': q.proof_pressure_pa,
    })


def evaluate_proof_limited_tearing(request, template):
    from .time_dependent_tearing import evaluate_pressure_history_tearing

    bound = bind_proof_limited_tearing(request, template)
    return dict(proof_observation=evaluate_surface_flaw_proof(request),
                tearing_request_digest=digest(bound),
                tearing_receipt=evaluate_pressure_history_tearing(bound))
