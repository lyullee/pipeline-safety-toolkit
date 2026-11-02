"""Pointwise series reduction with explicit local-velocity and water-Cv bases.

Each coefficient describes an already reviewed, nonflashing single-liquid
quadratic loss at a common actual flow. This coordinate adapter predicts no
Reynolds correction, valve opening characteristic or coefficient calibration.
The water reference density is an input to a US-gallon/minute, one-psi Cv,
not an assumed density of the actual process liquid.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive

US_GALLON_PER_MINUTE_M3_S = 0.003785411784 / 60.0
PSI_PA = 6894.757293168361


class LiquidResistanceCoordinate(Contract):
    component_id: Name
    kind: Literal['velocity_head_K', 'water_Cv']
    coefficient: Positive | None = None
    actual_velocity_reference_diameter_m: Positive | None = None
    cv_reference_water_density_kg_m3: Positive | None = None
    applicable_flow_sign: Literal['positive', 'negative', 'both']
    basis_id: Name | None = None
    coefficient_and_regime_reviewed: bool = False

    @model_validator(mode='after')
    def matching_coordinates(self):
        if self.kind == 'velocity_head_K' and self.cv_reference_water_density_kg_m3 is not None:
            raise ValueError('a K input must not carry a water-Cv reference')
        if self.kind == 'water_Cv' and self.actual_velocity_reference_diameter_m is not None:
            raise ValueError('Cv needs no invented local reference diameter')
        return self


class LiquidResistanceReductionRequest(Contract):
    components: tuple[LiquidResistanceCoordinate, ...] = Field(min_length=1)
    output_velocity_reference_diameter_m: Positive | None = None
    output_cv_reference_water_density_kg_m3: Positive | None = None
    actual_volume_flow_m3_s: float | None = None
    actual_density_kg_m3: Positive | None = None
    common_flow_and_single_liquid_regime_reviewed: bool = False

    @model_validator(mode='after')
    def finite_and_unique(self):
        if self.actual_volume_flow_m3_s is not None and not math.isfinite(self.actual_volume_flow_m3_s):
            raise ValueError('actual flow must be finite')
        if len({c.component_id for c in self.components}) != len(self.components):
            raise ValueError('component ids must be unique')
        return self


class LiquidResistanceReductionResult(Contract):
    state: Literal['computed', 'needs_data', 'out_of_scope']
    reason: str
    equivalent_mass_scaled_conductance_m2: Positive | None = None
    equivalent_velocity_head_K: Positive | None = None
    equivalent_water_Cv: Positive | None = None
    component_inlet_minus_outlet_pressure_pa: tuple[float, ...] = ()
    inlet_minus_outlet_pressure_pa: float | None = None
    irreversible_power_w: Nonnegative | None = None
    pointwise_only: bool = True
    field_or_standard_qualified: bool = False


def reduce_liquid_resistance_coordinates(request: LiquidResistanceReductionRequest) -> LiquidResistanceReductionResult:
    """Reduce reviewed components without replacing their velocity references.

    Conductance C is defined by |Q|=C*sqrt(|dP|/rho). Optional pressures are
    irreversible mechanical-total losses; endpoint kinetic/elevation changes
    belong to a separate control-volume budget. A zero prescribed flow has
    zero pressure/power while the supplied coefficient coordinate persists.
    """
    def fail(state, reason):
        return LiquidResistanceReductionResult(state=state, reason=reason)
    if not request.common_flow_and_single_liquid_regime_reviewed:
        return fail('needs_data', 'common flow and applicable liquid regime require review')
    conductances=[]
    for c in request.components:
        if c.coefficient is None or c.basis_id is None or not c.coefficient_and_regime_reviewed:
            return fail('needs_data', 'each actual coefficient and its applicable basis require review')
        q=request.actual_volume_flow_m3_s
        if q is not None and ((q > 0 and c.applicable_flow_sign == 'negative') or (q < 0 and c.applicable_flow_sign == 'positive')):
            return fail('out_of_scope', 'a component coefficient is not applicable in the actual flow direction')
        if c.kind == 'velocity_head_K':
            if c.actual_velocity_reference_diameter_m is None:
                return fail('needs_data', 'K needs its actual local velocity-reference diameter')
            area=math.pi/4*c.actual_velocity_reference_diameter_m**2
            C=area*math.sqrt(2.0/c.coefficient)
        else:
            if c.cv_reference_water_density_kg_m3 is None:
                return fail('needs_data', 'water-Cv needs the declared reference-water density')
            C=c.coefficient*US_GALLON_PER_MINUTE_M3_S*math.sqrt(c.cv_reference_water_density_kg_m3/PSI_PA)
        if not math.isfinite(C) or C <= 0:
            return fail('out_of_scope', 'a conductance is outside representable positive arithmetic')
        conductances.append(C)
    low=min(conductances)
    equivalent=low/math.sqrt(math.fsum((low/c)**2 for c in conductances))
    K=None;cv=None
    if request.output_velocity_reference_diameter_m is not None:
        area=math.pi/4*request.output_velocity_reference_diameter_m**2
        K=2*(area/equivalent)**2
    if request.output_cv_reference_water_density_kg_m3 is not None:
        cv=equivalent/(US_GALLON_PER_MINUTE_M3_S*math.sqrt(request.output_cv_reference_water_density_kg_m3/PSI_PA))
    if equivalent <= 0 or not math.isfinite(equivalent) or any(x is not None and (not math.isfinite(x) or x <= 0) for x in (K,cv)):
        return fail('out_of_scope', 'derived resistance coordinate is outside representable arithmetic')
    drops=();dp=None;power=None
    if request.actual_volume_flow_m3_s is not None:
        if request.actual_density_kg_m3 is None:
            return fail('needs_data', 'actual flow pressure output requires actual liquid density')
        from fluids.core import dP_from_K
        q=request.actual_volume_flow_m3_s
        # K=2 and V=Q/C is the native general loss primitive coordinate,
        # not a fictitious pipe diameter or circular flow model.
        magnitudes=[dP_from_K(K=2.0,rho=request.actual_density_kg_m3,V=q/c) for c in conductances]
        drops=tuple(math.copysign(x,q) if q else 0.0 for x in magnitudes)
        dp=math.fsum(drops);power=dp*q
        if not all(math.isfinite(x) for x in (*drops,dp,power)) or (q and (dp == 0 or power == 0)):
            return fail('out_of_scope', 'nonzero pressure or power is not representable')
    return LiquidResistanceReductionResult(state='computed',reason='reviewed pointwise series coordinates',equivalent_mass_scaled_conductance_m2=equivalent,equivalent_velocity_head_K=K,equivalent_water_Cv=cv,component_inlet_minus_outlet_pressure_pa=drops,inlet_minus_outlet_pressure_pa=dp,irreversible_power_w=power)
