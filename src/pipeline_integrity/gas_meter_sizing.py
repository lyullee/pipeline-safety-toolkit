"""Bracketed meter geometry inversion through the existing gas-meter owner."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Positive, RunContext
from .gas_meter import GasMeterGeometry, GasMeterRequest, evaluate_gas_meter


class GasMeterSizingRequest(Contract):
    template: GasMeterRequest
    target_signed_mass_flow_kg_s: float
    secondary_dimension_bracket_m: tuple[Positive, Positive]
    geometry_and_calibration_valid_over_bracket_reviewed: bool = False
    continuous_single_geometry_branch_reviewed: bool = False
    absolute_dimension_tolerance_m: Positive = 1e-10
    relative_dimension_tolerance: float = Field(default=1e-10, ge=1e-14, lt=1)
    absolute_mass_flow_tolerance_kg_s: Positive = 1e-9
    relative_mass_flow_tolerance: float = Field(default=1e-8, ge=0, lt=1)
    maximum_iterations: int = Field(default=100, ge=1, le=10000)

    @model_validator(mode='after')
    def geometry_bracket(self):
        if self.template.geometry is None:
            raise ValueError('meter geometry template is required')
        lo, hi = self.secondary_dimension_bracket_m
        if not lo < hi < self.template.geometry.inner_pipe_diameter_m:
            raise ValueError('dimension bracket must increase strictly inside the pipe')
        if self.target_signed_mass_flow_kg_s == 0:
            raise ValueError('zero flow does not identify a unique meter dimension')
        return self


class GasMeterSizingResult(Contract):
    status: Literal['computed', 'needs_review', 'out_of_scope', 'no_bracket', 'not_converged']
    secondary_dimension_m: Positive | None = None
    mass_flow_residual_kg_s: float | None = None
    forward_record: dict | None = None
    invalid_trial_record: dict | None = None
    forward_calls: int = 0
    issues: tuple[str, ...] = ()
    field_calibration_inferred: Literal[False] = False


class _UnsupportedTrial(Exception):
    def __init__(self, record):
        self.record = record


def evaluate_gas_meter_sizing(request: GasMeterSizingRequest, context: RunContext) -> GasMeterSizingResult:
    """Solve throat, cone or wedge dimension using its original flow geometry.

    Every trial reuses the native geometry/expansibility/Reynolds implementation.
    The supplied bracket must stay inside one reviewed calibration branch.
    """
    from scipy.optimize import brentq
    if not isinstance(request, GasMeterSizingRequest):
        raise TypeError('GasMeterSizingRequest required')
    q = GasMeterSizingRequest.model_validate(request.model_dump())
    c = RunContext.model_validate(context.model_dump())
    if not q.geometry_and_calibration_valid_over_bracket_reviewed or not q.continuous_single_geometry_branch_reviewed:
        return GasMeterSizingResult(status='needs_review', issues=('review geometry, calibration and a continuous single branch over the entire bracket',))
    calls = 0
    def forward(dimension):
        nonlocal calls
        geometry = GasMeterGeometry.model_validate(q.template.geometry.model_copy(
            update={'secondary_dimension_m': dimension}).model_dump())
        trial = GasMeterRequest.model_validate(q.template.model_copy(update={'geometry': geometry}).model_dump())
        record = evaluate_gas_meter(trial, c)
        calls += 1
        if record.get('status') != 'computed' or not record.get('values'):
            raise _UnsupportedTrial(record)
        return record
    def residual(dimension):
        value = forward(dimension)['values']['signed_mass_flow_kg_s'] - q.target_signed_mass_flow_kg_s
        if not math.isfinite(value):
            raise ValueError('meter sizing residual exceeds finite numerical support')
        return value
    try:
        lo, hi = q.secondary_dimension_bracket_m
        flo, fhi = residual(lo), residual(hi)
        if flo == 0:
            root = lo
        elif fhi == 0:
            root = hi
        elif (flo > 0) == (fhi > 0):
            return GasMeterSizingResult(status='no_bracket', forward_calls=calls,
                issues=('target flow is not bracketed; dimensions are not expanded automatically',))
        else:
            root, info = brentq(residual, lo, hi, xtol=q.absolute_dimension_tolerance_m,
                rtol=q.relative_dimension_tolerance, maxiter=q.maximum_iterations,
                full_output=True, disp=False)
            if not info.converged:
                return GasMeterSizingResult(status='not_converged', forward_calls=calls,
                    issues=('geometry root did not converge',))
        record = forward(root)
        error = record['values']['signed_mass_flow_kg_s'] - q.target_signed_mass_flow_kg_s
        okay = abs(error) <= q.absolute_mass_flow_tolerance_kg_s + q.relative_mass_flow_tolerance * abs(q.target_signed_mass_flow_kg_s)
        return GasMeterSizingResult(status='computed' if okay else 'not_converged',
            secondary_dimension_m=root, mass_flow_residual_kg_s=error,
            forward_record=record, forward_calls=calls,
            issues=() if okay else ('final flow did not reach the requested tolerance',))
    except _UnsupportedTrial as exc:
        return GasMeterSizingResult(status='out_of_scope', invalid_trial_record=exc.record,
            forward_calls=calls, issues=('owner rejected a trial; invalid states are not skipped',))
