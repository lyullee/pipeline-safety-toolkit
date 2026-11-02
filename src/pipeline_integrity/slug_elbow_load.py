"""Frozen prescribed liquid-slug port states at an elbow, in a common SI frame.

This adapter predicts neither slug formation/speed nor an acoustic pressure pulse.
Pressure and velocity must come from observations or a separately qualified flow
model. Open-port contributions are distinct from a complete transient elbow load:
the latter also needs momentum accumulation, body force and wall interactions.
"""
import math
from .contracts import Contract, Name, Positive, Nonnegative, Calculation
from .port_momentum_load import MomentumPort, MomentumLoadRequest, evaluate_port_momentum_load

Vector = tuple[float, float, float]


class SlugElbowLoadRequest(Contract):
    coordinate_frame_id: Name
    slug_density_kg_m3: Positive
    flow_area_m2: Positive
    signed_path_velocity_m_s: float
    incoming_path_unit_direction: Vector
    outgoing_path_unit_direction: Vector
    incoming_pressure_Pa_abs: Positive
    outgoing_pressure_Pa_abs: Positive
    ambient_pressure_Pa_abs: Positive
    uniform_full_bore_slug_port_states_reviewed: bool = False
    complete_steady_control_volume_reviewed: bool = False
    body_force_on_fluid_N: Vector | None = None
    absolute_mass_balance_tolerance_kg_s: Nonnegative | None = None


def evaluate_slug_elbow_load(request: SlugElbowLoadRequest) -> Calculation:
    """Map equal-area/equal-density signed slug states to the port momentum owner.

    Directions describe the fixed incoming-to-outgoing path; a negative velocity
    reverses the flow without changing the geometry. Pressure tractions are kept
    separate from convective momentum. Only an explicitly reviewed steady CV with
    supplied body force can identify a whole hardware force. No acceleration or
    impact waveform is inferred from a frozen snapshot.
    """
    q = SlugElbowLoadRequest.model_validate(request.model_dump(mode='python'))
    mass_flow = q.slug_density_kg_m3*q.flow_area_m2*q.signed_path_velocity_m_s
    if not math.isfinite(mass_flow):
        raise ValueError('slug mass flow exceeds finite arithmetic')
    if q.signed_path_velocity_m_s != 0 and mass_flow == 0:
        raise ValueError('nonzero slug mass flow underflow is not a stationary state')
    ports = (
        MomentumPort(id='incoming', outward_unit_normal=tuple(-v for v in q.incoming_path_unit_direction),
            area_m2=q.flow_area_m2, pressure_Pa_abs=q.incoming_pressure_Pa_abs,
            outward_mass_flow_kg_s=-mass_flow,
            fluid_velocity_m_s=tuple(q.signed_path_velocity_m_s*v for v in q.incoming_path_unit_direction)),
        MomentumPort(id='outgoing', outward_unit_normal=q.outgoing_path_unit_direction,
            area_m2=q.flow_area_m2, pressure_Pa_abs=q.outgoing_pressure_Pa_abs,
            outward_mass_flow_kg_s=mass_flow,
            fluid_velocity_m_s=tuple(q.signed_path_velocity_m_s*v for v in q.outgoing_path_unit_direction)),
    )
    result = evaluate_port_momentum_load(MomentumLoadRequest(
        coordinate_frame_id=q.coordinate_frame_id, ambient_pressure_Pa_abs=q.ambient_pressure_Pa_abs,
        ports=ports, uniform_port_states_and_common_frame_reviewed=q.uniform_full_bore_slug_port_states_reviewed,
        complete_steady_control_volume_reviewed=q.complete_steady_control_volume_reviewed,
        body_force_on_fluid_N=q.body_force_on_fluid_N,
        absolute_mass_balance_tolerance_kg_s=q.absolute_mass_balance_tolerance_kg_s))
    values = dict(result.values)
    if 'port_contributions' in values:
        rows = values['port_contributions']
        values.update(
            summed_convective_contribution_N=tuple(math.fsum(row['momentum_contribution_N'][i] for row in rows) for i in range(3)),
            summed_pressure_contribution_N=tuple(math.fsum(row['pressure_contribution_N'][i] for row in rows) for i in range(3)),
            signed_path_mass_flow_kg_s=mass_flow,
            slug_formation_predicted=False, slug_velocity_predicted=False,
            acoustic_pressure_predicted=False, transient_momentum_accumulation_included=False)
    return Calculation(values=values, status=result.status, notes=(*result.notes,
        'Prescribed full-bore liquid slug states only; no flow-regime chart, void-fraction speed estimate, condensation or fatigue qualification.'))
