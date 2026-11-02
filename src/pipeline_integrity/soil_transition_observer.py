"""Constant supplied axial soil resistance between restrained and free pipe.

Reuses existing radial elasticity and uniform thermal-restraint owners. The
constant resistance and transition idealization are reviewed caller data; no
burial, soil-pressure, friction calibration or anchor capacity is predicted.
"""
import math
from .contracts import Contract, Name, Positive, RunContext, digest
from .fully_restrained_cylinder import RestrainedCylinderRequest,evaluate_fully_restrained_cylinder


class SoilTransitionRequest(Contract):
    id: Name
    cylinder: RestrainedCylinderRequest
    supplied_constant_axial_resistance_n_m: Positive | None = None
    resistance_evidence_id: Name | None = None
    uniform_section_constant_opposing_resistance_far_field_transition_reviewed: bool = False


def evaluate_soil_transition(q: SoilTransitionRequest, context: RunContext, *, client=None):
    """Observe the signed far-field compatibility gap and constant-q transition.

    Coordinate x runs from the zero-strain end toward the free closed-cap end.
    Stress uses the owner's zero-exterior-pressure datum. The free end is not
    force-free: its axial material resultant balances the actual pressure cap.
    A negative free strain reverses displacement and soil traction. The same
    model cannot resolve a finite available embedment or variable/slipping soil.
    """
    q=SoilTransitionRequest.model_validate(q.model_dump())
    c=RunContext.model_validate(context.model_dump())
    response=evaluate_fully_restrained_cylinder(q.cylinder,c,client=client)
    base=dict(request_digest=digest(q),field_or_standard_qualified=False,
              soil_resistance_predicted=False,anchor_capacity_predicted=False,
              restrained_cylinder_response=response)
    if response['status']!='computed':return dict(status=response['status'],**base)
    pressure=response['native_pressure_receipt']['native']['payload']['values']
    free_stress=pressure['axial_stress_Pa']
    constrained=response['axial_stress_tension_positive_pa']
    gap=free_stress-constrained
    area=math.pi*(q.cylinder.outer_radius_m**2-q.cylinder.inner_radius_m**2)
    force=gap*area
    free_strain=gap/q.cylinder.elastic_modulus_pa
    values=dict(free_closed_cap_axial_stress_pa=free_stress,restrained_axial_stress_pa=constrained,
        free_minus_restrained_stress_pa=gap,free_closed_cap_axial_strain=free_strain,
        free_minus_restrained_material_force_n=force,anchored_required_positive_end_restraint_force_n=-force)
    if q.supplied_constant_axial_resistance_n_m is None or q.resistance_evidence_id is None or not q.uniform_section_constant_opposing_resistance_far_field_transition_reviewed:
        return dict(status='needs_data',values=values,**base)
    if q.resistance_evidence_id not in {e.id for e in c.evidence}:
        raise ValueError('soil-resistance evidence absent from context')
    length=abs(force)/q.supplied_constant_axial_resistance_n_m
    movement=free_strain*length/2
    traction=-math.copysign(q.supplied_constant_axial_resistance_n_m,force) if force else 0.
    values.update(transfer_length_m=length,signed_terminal_displacement_m=movement,
                  signed_distributed_soil_force_n_m=traction,
                  absolute_integrated_soil_force_n=abs(traction)*length)
    if not all(math.isfinite(v) for v in values.values()):raise ValueError('transition exceeds finite support')
    return dict(status='computed',values=values,**base)
