"""Invert a supplied bending-stress ceiling through the existing frame owner.

The model is a prismatic circular pipe with uniform downward load and explicitly
selected equal end restraints. This module adds an inverse workflow, not a new
beam solver or a material/code allowable selection rule.
"""
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .structural import FrameRequest,FrameNode,FrameMember,FrameMaterial,FrameSupport,FrameLineLoad,FrameCombination,annular_frame_section,solve_frame


class UDLSpanSizingRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    outside_diameter_m: Positive
    inside_diameter_m: Nonnegative
    young_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=.5)
    downward_uniform_load_n_m: Positive
    supplied_bending_stress_ceiling_pa: Positive
    equal_end_restraint: Literal['pinned','clamped','supplied_equal_rotational_springs']
    supplied_each_end_rotation_stiffness_nm_rad: Nonnegative | None = None
    span_bracket_m: tuple[Positive,Positive]
    absolute_root_length_tolerance_m: Positive = 1e-8
    evidence_id: Name
    section_material_loading_restraints_and_ceiling_reviewed: bool = False

    @model_validator(mode='after')
    def valid(self):
        if self.inside_diameter_m>=self.outside_diameter_m:raise ValueError('positive circular wall required')
        if self.span_bracket_m[1]<=self.span_bracket_m[0]:raise ValueError('increasing positive span bracket required')
        if self.equal_end_restraint=='supplied_equal_rotational_springs':
            if self.supplied_each_end_rotation_stiffness_nm_rad is None:raise ValueError('actual rotational stiffness required')
        elif self.supplied_each_end_rotation_stiffness_nm_rad is not None:
            raise ValueError('a finite spring must not be supplied as an ideal pin or clamp')
        return self


def evaluate_udl_span_sizing(q: UDLSpanSizingRequest,context: RunContext):
    """Search within the caller bracket, preserving actual owner scope failures.

    Global X is the pipe axis, Y is upward. All transverse translations vanish
    at the ends; both rotations are pinned, clamped or attached to equal reviewed
    springs. Symmetry places all extrema of the quadratic bending moment at an
    end or midspan. This is why three actual physical-section samples suffice;
    an arbitrary beam/load history must not use this peak-observation shortcut.
    """
    from scipy.optimize import brentq
    q=UDLSpanSizingRequest.model_validate(q.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('span/context identity differs')
    if q.evidence_id not in {e.id for e in c.evidence}:raise ValueError('review evidence absent from context')
    base=dict(request_digest=digest(q),field_or_standard_qualified=False,allowable_selected_by_model=False,
              source_legacy_support_condition_inferred=False)
    if not q.section_material_loading_restraints_and_ceiling_reviewed:return dict(status='needs_data',**base)
    section=annular_frame_section('section',q.outside_diameter_m,q.inside_diameter_m,q.evidence_id)
    material=FrameMaterial(id='material',young_modulus_pa=q.young_modulus_pa,poisson_ratio=q.poisson_ratio,evidence_id=q.evidence_id)
    cache={};evaluations=[]
    def observe(length):
        if length in cache:return cache[length]
        fixed=('DX','DY','DZ','RX','RY')+(('RZ',) if q.equal_end_restraint=='clamped' else ())
        k=q.supplied_each_end_rotation_stiffness_nm_rad
        springs={'RZ':k} if k else {}
        supports=tuple(FrameSupport(node_id=n,fixed_dofs=fixed,rotation_springs_nm_rad=springs,evidence_id=q.evidence_id) for n in ['left','right'])
        spec=FrameRequest(asset_id=q.asset_id,clock_id=q.clock_id,time_s=0.,
            nodes=(FrameNode(id='left',position_m=(0.,0.,0.)),FrameNode(id='right',position_m=(length,0.,0.))),
            members=(FrameMember(id='span',i_node='left',j_node='right',section_id=section.id,material_id=material.id,sample_fractions=(0.,.5,1.)),),
            sections=(section,),materials=(material,),supports=supports,load_cases=('uniform',),
            line_loads=(FrameLineLoad(member_id='span',global_direction='FY',start_n_m=-q.downward_uniform_load_n_m,end_n_m=-q.downward_uniform_load_n_m,case_id='uniform',evidence_id=q.evidence_id),),
            combinations=(FrameCombination(id='case',factors={'uniform':1.},evidence_id=q.evidence_id),),
            geometry_evidence_id=q.evidence_id,geometry_and_connectivity_reviewed=True,stiffness_and_loads_reviewed=True,
            supports_reviewed=True,linear_small_displacement_applicability_reviewed=True)
        result=solve_frame(spec,c)
        if result['status']!='computed':
            evaluations.append(dict(span_m=length,status=result['status']))
            raise _OwnerWithheld(result)
        frame=result['combinations']['case'];samples=frame['members']['span']['samples']
        maximum=max(abs(s['physical_section_moment_local_nm'][2]) for s in samples)
        stress=maximum*(q.outside_diameter_m/2)/section.iz_m4
        if not math.isfinite(stress):raise ValueError('section response exceeds finite support')
        observation=dict(span_m=length,maximum_absolute_physical_bending_moment_nm=maximum,
                         maximum_absolute_nominal_bending_stress_pa=stress,
                         supplied_ceiling_residual_pa=q.supplied_bending_stress_ceiling_pa-stress,
                         maximum_absolute_transverse_deflection_m=abs(samples[1]['local_translation_m'][1]),
                         actual_frame_result=result)
        cache[length]=observation;evaluations.append({k:v for k,v in observation.items() if k!='actual_frame_result'})
        return observation
    try:
        low,high=q.span_bracket_m;a=observe(low);b=observe(high)
        if a['supplied_ceiling_residual_pa']<0 or b['supplied_ceiling_residual_pa']>0:
            return dict(status='bracket_does_not_bound_supported_ceiling_crossing',evaluations=evaluations,**base)
        root=brentq(lambda length:-observe(length)['supplied_ceiling_residual_pa'],low,high,xtol=q.absolute_root_length_tolerance_m,rtol=1e-12)
        selected=observe(root)
    except _OwnerWithheld as failure:
        return dict(status='solver_withheld',owner_result=failure.result,evaluations=evaluations,**base)
    return dict(status='computed',maximum_span_under_supplied_ceiling_m=root,selected_observation=selected,evaluations=evaluations,**base)


class _OwnerWithheld(Exception):
    def __init__(self,result):self.result=result
