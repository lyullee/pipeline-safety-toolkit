"""Series/parallel reduction of explicitly frozen pipe power-law coefficients.

This is a geometry/resistance preprocessing utility, not another network flow
solver. Each branch obeys DeltaPhi=(K L/D^m) sign(Q)|Q|^n with one declared
potential/flow basis. State-dependent friction, heat and junction physics remain
with the existing pipe/network owners and must not be frozen without review.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive


class FrozenPipeResistance(Contract):
    length_m: Positive
    diameter_m: Positive
    supplied_loss_scale: Positive


class PipeResistanceReductionRequest(Contract):
    branches: tuple[FrozenPipeResistance,...] = Field(min_length=1,max_length=10000)
    topology: Literal['series','parallel']
    potential_basis: Literal['pressure_drop_pa','absolute_pressure_squared_pa2']
    flow_basis: Literal['mass_kg_s','reference_volume_m3_s']
    coefficient_basis_id: Name
    flow_exponent: Positive = 2.
    diameter_exponent: Positive = 5.
    reference_loss_scale: Positive
    reference_diameter_m: Positive | None = None
    reference_length_m: Positive | None = None
    common_frozen_exponents_and_coefficient_units_reviewed: bool = False

    @model_validator(mode='after')
    def reference_geometry(self):
        if self.topology=='series':
            if self.reference_diameter_m is None or self.reference_length_m is not None:raise ValueError('series reduction requires only a reference diameter')
        elif self.reference_length_m is None or self.reference_diameter_m is not None:raise ValueError('parallel reduction requires only a reference length')
        return self


class PipeResistanceReductionResult(Contract):
    status: Literal['computed','needs_data']
    equivalent_resistance: Positive | None = None
    branch_resistances: tuple[Positive,...] = ()
    equivalent_length_m: Positive | None = None
    equivalent_diameter_m: Positive | None = None
    signed_total_flow_fractions: tuple[Positive,...] = ()
    issues: tuple[str,...] = ()


def _positive_exp(value):
    try:result=math.exp(value)
    except OverflowError as error:raise ValueError('resistance reduction exceeds finite support') from error
    if not math.isfinite(result) or result<=0:raise ValueError('resistance reduction leaves finite positive support')
    return result


def _logsumexp(values):
    maximum=max(values)
    return maximum+math.log(math.fsum(math.exp(v-maximum) for v in values))


def reduce_pipe_resistances(request:PipeResistanceReductionRequest):
    """Reduce common-exponent laws, preserving coefficient and flow conventions.

    In series, the same signed flow crosses each branch. In parallel, each branch
    shares DeltaPhi and receives its reported fraction of either signed total
    flow. Intermediate supplies/deliveries invalidate a single series equivalent.
    """
    q=PipeResistanceReductionRequest.model_validate(request.model_dump(mode='python'))
    if not q.common_frozen_exponents_and_coefficient_units_reviewed:
        return PipeResistanceReductionResult(status='needs_data',issues=('coefficient units, common exponents, property support and frozen-flow law require review',))
    logs=[math.log(p.supplied_loss_scale)+math.log(p.length_m)-q.diameter_exponent*math.log(p.diameter_m) for p in q.branches]
    resistances=tuple(_positive_exp(v) for v in logs)
    if q.topology=='series':
        equivalent=_logsumexp(logs)
        length=_positive_exp(equivalent-math.log(q.reference_loss_scale)+q.diameter_exponent*math.log(q.reference_diameter_m))
        return PipeResistanceReductionResult(status='computed',branch_resistances=resistances,equivalent_resistance=_positive_exp(equivalent),equivalent_length_m=length)
    weights=[-v/q.flow_exponent for v in logs];normalizer=_logsumexp(weights)
    equivalent=-q.flow_exponent*normalizer
    diameter=_positive_exp((math.log(q.reference_loss_scale)+math.log(q.reference_length_m)-equivalent)/q.diameter_exponent)
    fractions=tuple(_positive_exp(v-normalizer) for v in weights)
    return PipeResistanceReductionResult(status='computed',branch_resistances=resistances,equivalent_resistance=_positive_exp(equivalent),
        equivalent_diameter_m=diameter,signed_total_flow_fractions=fractions)
