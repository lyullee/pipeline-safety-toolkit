"""Observe a reviewed circular elastic strip on an isotropic linear foundation.

The existing frame engine owns every stiffness assembly and solve. This adapter
maps explicit angular line loads and foundation density to an actual polygonal
ring. Soil reaction modulus, bedding contact and empirical design coefficients
are not inferred from burial depth or a downward pressure scalar.
"""
import math
from pydantic import Field
from .contracts import Contract, Name, Positive, RunContext, digest
from .structural import (
    FrameRequest, FrameNode, FrameMember, FrameSection, FrameMaterial,
    FrameSupport, FrameNodeLoad, FrameCombination, solve_frame,
)


class RingLoadHarmonic(Contract):
    mode: int = Field(ge=0, le=12)
    radial_cosine_n_m: float = 0.
    radial_sine_n_m: float = 0.
    tangential_cosine_n_m: float = 0.
    tangential_sine_n_m: float = 0.


class SupportedRingRequest(Contract):
    asset_id: Name
    clock_id: Name
    reference_radius_m: Positive
    section: FrameSection
    bending_surface_distance_m: Positive
    young_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1, lt=.5)
    isotropic_foundation_line_density_n_m2: Positive
    load_harmonics: tuple[RingLoadHarmonic, ...] = Field(min_length=1, max_length=30)
    circumferential_members: int = Field(default=64, ge=16, le=256, multiple_of=4)
    maximum_translation_over_radius: Positive = Field(default=.01, le=.05)
    evidence_id: Name
    linear_elastic_strip_full_contact_isotropic_foundation_and_load_basis_reviewed: bool = False


def evaluate_supported_ring(q: SupportedRingRequest, context: RunContext):
    """Return signed diametric changes and nominal strip bending stress.

    Coordinates are x=R cos(theta), y=R sin(theta), with increasing theta
    counterclockwise and outward radial load positive. Foundation force per
    reference arc length is -k*u in both in-plane Cartesian directions; k has
    N/m² units. It represents bilateral distributed springs, not an inferred
    unilateral soil contact law. Plane motion is constrained to XY.
    """
    q=SupportedRingRequest.model_validate(q.model_dump())
    c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError('ring asset/clock differs from context')
    if q.evidence_id not in {e.id for e in c.evidence} or q.section.evidence_id not in {e.id for e in c.evidence}:
        raise ValueError('actual ring section and load/support evidence required')
    n=q.circumferential_members
    if max(h.mode for h in q.load_harmonics)*8>n:
        raise ValueError('at least eight members per highest load harmonic required')
    base=dict(request_digest=digest(q),soil_calibration_or_contact_inferred=False,
              source_empirical_coefficients_reproduced=False,field_or_standard_qualified=False)
    if not q.linear_elastic_strip_full_contact_isotropic_foundation_and_load_basis_reviewed:
        return dict(status='needs_data',**base)
    radius=q.reference_radius_m
    ds=2*math.pi*radius/n
    nodes=[];loads=[];supports=[]
    for j in range(n):
        theta=2*math.pi*j/n;co=math.cos(theta);si=math.sin(theta);name=f'n{j}'
        nodes.append(FrameNode(id=name,position_m=(radius*co,radius*si,0.)))
        radial=sum(h.radial_cosine_n_m*math.cos(h.mode*theta)+h.radial_sine_n_m*math.sin(h.mode*theta) for h in q.load_harmonics)
        tangent=sum(h.tangential_cosine_n_m*math.cos(h.mode*theta)+h.tangential_sine_n_m*math.sin(h.mode*theta) for h in q.load_harmonics)
        for direction,force in [('FX',(radial*co-tangent*si)*ds),('FY',(radial*si+tangent*co)*ds)]:
            loads.append(FrameNodeLoad(node_id=name,direction=direction,value=force,case_id='angular',evidence_id=q.evidence_id))
        stiffness=q.isotropic_foundation_line_density_n_m2*ds
        supports.append(FrameSupport(node_id=name,fixed_dofs=('DZ','RX','RY'),
            translation_springs_n_m={'DX':stiffness,'DY':stiffness},evidence_id=q.evidence_id))
    frame=FrameRequest(asset_id=q.asset_id,clock_id=q.clock_id,time_s=0.,nodes=tuple(nodes),
        members=tuple(FrameMember(id=f'm{j}',i_node=f'n{j}',j_node=f'n{(j+1)%n}',
            section_id=q.section.id,material_id='strip',sample_fractions=(0.,.5,1.)) for j in range(n)),
        sections=(q.section,),materials=(FrameMaterial(id='strip',young_modulus_pa=q.young_modulus_pa,
            poisson_ratio=q.poisson_ratio,evidence_id=q.evidence_id),),supports=tuple(supports),
        node_loads=tuple(loads),load_cases=('angular',),
        combinations=(FrameCombination(id='case',factors={'angular':1.},evidence_id=q.evidence_id),),
        geometry_evidence_id=q.evidence_id,geometry_and_connectivity_reviewed=True,
        stiffness_and_loads_reviewed=True,supports_reviewed=True,
        linear_small_displacement_applicability_reviewed=True)
    result=solve_frame(frame,c)
    if result['status']!='computed':
        return dict(status='owner_withheld',owner_result=result,**base)
    case=result['combinations']['case']
    translations={j:case['nodes'][f'n{j}']['translation_m'] for j in range(n)}
    maximum=max(math.hypot(*v[:2]) for v in translations.values())
    if maximum/radius>q.maximum_translation_over_radius:
        return dict(status='out_of_scope',owner_result=result,**base)
    moment=max(abs(s['physical_section_moment_local_nm'][2])
               for member in case['members'].values() for s in member['samples'])
    return dict(status='computed',signed_x_diameter_change_m=translations[0][0]-translations[n//2][0],
        signed_y_diameter_change_m=translations[n//4][1]-translations[3*n//4][1],
        maximum_inplane_translation_m=maximum,
        maximum_absolute_nominal_bending_stress_pa=moment*q.bending_surface_distance_m/q.section.iz_m4,
        owner_result=result,**base)
