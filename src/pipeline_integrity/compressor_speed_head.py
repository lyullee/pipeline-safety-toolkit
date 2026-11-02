"""Reviewed fixed-geometry polytropic-head/speed similarity, in J/kg.

Nominal stage heads are supplied map data. The operator sums only matched
polytropic heads; it never sums isentropic heads as an interchangeable basis.
No frame table, maximum-stage chart, efficiency, gas EOS or pressure-ratio
prediction is embedded. Sound speeds/phase/map support are injected inputs.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive

GasPhase=Literal['gas','supercritical_gas','supercritical','liquid','two_phase','unknown']


class CompressorStageHeadReference(Contract):
    stage_id: Name
    nominal_polytropic_specific_head_j_per_kg: Positive | None = None
    nominal_head_basis: Literal['polytropic_specific_head','isentropic_specific_head','fluid_specific_work','unknown'] = 'unknown'
    impeller_diameter_m: Positive | None = None
    reference_inlet_sound_speed_m_s: Positive | None = None
    target_inlet_sound_speed_m_s: Positive | None = None
    reference_inlet_phase: GasPhase = 'unknown'
    target_inlet_phase: GasPhase = 'unknown'
    reviewed_tip_mach_support: tuple[Nonnegative, Positive] | None = None
    map_and_inlet_state_basis_id: Name | None = None
    fixed_geometry_reduced_flow_reynolds_and_head_efficiency_support_reviewed: bool = False

    @model_validator(mode='after')
    def ordered_mach(self):
        if self.reviewed_tip_mach_support is not None and self.reviewed_tip_mach_support[0]>self.reviewed_tip_mach_support[1]:
            raise ValueError('tip Mach support must be ordered')
        return self


class CompressorSpeedHeadRequest(Contract):
    stages: tuple[CompressorStageHeadReference,...] = Field(min_length=1)
    mode: Literal['speed_to_head','required_head_to_speed']
    reference_shaft_speed_rpm: Positive | None = None
    target_shaft_speed_rpm: Positive | None = None
    required_total_polytropic_specific_head_j_per_kg: Positive | None = None

    @model_validator(mode='after')
    def one_coordinate(self):
        if self.mode=='speed_to_head' and self.required_total_polytropic_specific_head_j_per_kg is not None:
            raise ValueError('speed-to-head must not independently prescribe the requested head')
        if self.mode=='required_head_to_speed' and self.target_shaft_speed_rpm is not None:
            raise ValueError('head-to-speed must not independently prescribe target speed')
        return self


class CompressorStageHeadResult(Contract):
    stage_id: Name
    target_polytropic_specific_head_j_per_kg: Positive
    reference_tip_speed_m_s: Positive
    target_tip_speed_m_s: Positive
    reference_tip_mach: Positive
    target_tip_mach: Positive
    nominal_polytropic_head_over_tip_speed_squared: Positive
    map_and_inlet_state_basis_id: Name


class CompressorSpeedHeadResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    target_shaft_speed_rpm: Positive | None = None
    target_over_reference_speed_ratio: Positive | None = None
    nominal_total_polytropic_specific_head_j_per_kg: Positive | None = None
    total_polytropic_specific_head_j_per_kg: Positive | None = None
    head_inverse_residual_j_per_kg: float | None = None
    stages: tuple[CompressorStageHeadResult,...] = ()
    actual_flow_shaft_power_pressure_ratio_or_efficiency_inferred: bool = False
    surge_choke_rotor_limit_or_field_performance_qualified: bool = False
    issues: tuple[str,...] = ()


def evaluate_compressor_speed_head(request: CompressorSpeedHeadRequest) -> CompressorSpeedHeadResult:
    """Evaluate one reviewed reduced-map point, or its closed speed inverse.

    Fixed nondimensional polytropic head/geometry gives head proportional to
    shaft-speed squared. Reference heads may differ across stages on the same
    shaft. Maintaining the head coefficient requires the declared gas/reduced
    flow, Reynolds, compressibility and head-efficiency applicability; tip Mach
    screening alone does not establish that similarity or a feasible map point.
    """
    if not isinstance(request,CompressorSpeedHeadRequest):raise TypeError('CompressorSpeedHeadRequest required')
    q=CompressorSpeedHeadRequest.model_validate(request.model_dump(mode='python'))
    missing=[]
    if q.reference_shaft_speed_rpm is None:missing.append('reference shaft speed is required')
    coordinate=q.target_shaft_speed_rpm if q.mode=='speed_to_head' else q.required_total_polytropic_specific_head_j_per_kg
    if coordinate is None:missing.append('selected target speed or polytropic-head observation is missing')
    for s in q.stages:
        if s.nominal_head_basis=='unknown':missing.append(s.stage_id+': nominal specific-head kind is unknown')
        elif s.nominal_head_basis!='polytropic_specific_head':
            return CompressorSpeedHeadResult(status='out_of_scope',issues=('stage heads must share the declared polytropic basis; other head/work kinds require their actual thermodynamic conversion',))
        if s.reference_inlet_phase in ('liquid','two_phase') or s.target_inlet_phase in ('liquid','two_phase'):
            return CompressorSpeedHeadResult(status='out_of_scope',issues=('stage inlet phase is outside the selected single compressible gas/supercritical map domain',))
        if s.reference_inlet_phase=='unknown' or s.target_inlet_phase=='unknown':missing.append(s.stage_id+': reference/target inlet phase is unknown')
        if any(getattr(s,n) is None for n in ('nominal_polytropic_specific_head_j_per_kg','impeller_diameter_m',
                'reference_inlet_sound_speed_m_s','target_inlet_sound_speed_m_s','reviewed_tip_mach_support','map_and_inlet_state_basis_id')):
            missing.append(s.stage_id+': stage nominal head/geometry/gas state and map support are required')
        if not s.fixed_geometry_reduced_flow_reynolds_and_head_efficiency_support_reviewed:
            missing.append(s.stage_id+': reduced-map/head-efficiency applicability must be reviewed')
    if missing:return CompressorSpeedHeadResult(status='needs_data',issues=tuple(missing))
    if len({s.stage_id for s in q.stages})!=len(q.stages):
        return CompressorSpeedHeadResult(status='out_of_scope',issues=('stage identities must be unique on the declared common shaft',))
    try:
        nominal=math.fsum(s.nominal_polytropic_specific_head_j_per_kg for s in q.stages)
        target_speed=(q.target_shaft_speed_rpm if q.mode=='speed_to_head' else
            q.reference_shaft_speed_rpm*math.sqrt(q.required_total_polytropic_specific_head_j_per_kg/nominal))
        ratio=target_speed/q.reference_shaft_speed_rpm
        if not all(math.isfinite(v) and v>0 for v in (nominal,target_speed,ratio)):raise ArithmeticError
        omega_reference=q.reference_shaft_speed_rpm*2*math.pi/60
        omega_target=target_speed*2*math.pi/60
        rows=[]
        for s in q.stages:
            u0=omega_reference*s.impeller_diameter_m/2;u1=omega_target*s.impeller_diameter_m/2
            m0=u0/s.reference_inlet_sound_speed_m_s;m1=u1/s.target_inlet_sound_speed_m_s
            lower,upper=s.reviewed_tip_mach_support
            if not lower<=m0<=upper or not lower<=m1<=upper:
                return CompressorSpeedHeadResult(status='out_of_scope',issues=(s.stage_id+': reference or target tip Mach is outside supplied map support',))
            head=s.nominal_polytropic_specific_head_j_per_kg*ratio**2
            coefficient=s.nominal_polytropic_specific_head_j_per_kg/u0**2
            if not all(math.isfinite(v) and v>0 for v in (u0,u1,m0,m1,head,coefficient)):raise ArithmeticError
            rows.append(CompressorStageHeadResult(stage_id=s.stage_id,target_polytropic_specific_head_j_per_kg=head,
                reference_tip_speed_m_s=u0,target_tip_speed_m_s=u1,reference_tip_mach=m0,target_tip_mach=m1,
                nominal_polytropic_head_over_tip_speed_squared=coefficient,map_and_inlet_state_basis_id=s.map_and_inlet_state_basis_id))
        total=math.fsum(s.target_polytropic_specific_head_j_per_kg for s in rows)
        if not math.isfinite(total) or total<=0:raise ArithmeticError
        return CompressorSpeedHeadResult(status='computed',target_shaft_speed_rpm=target_speed,target_over_reference_speed_ratio=ratio,
            nominal_total_polytropic_specific_head_j_per_kg=nominal,total_polytropic_specific_head_j_per_kg=total,
            head_inverse_residual_j_per_kg=total-q.required_total_polytropic_specific_head_j_per_kg if q.mode=='required_head_to_speed' else None,
            stages=tuple(rows))
    except (ArithmeticError,ValueError):
        return CompressorSpeedHeadResult(status='out_of_scope',issues=('head/speed/impeller coordinate leaves finite positive arithmetic support',))
