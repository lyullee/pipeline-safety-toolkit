"""Uniform radial shell pulse using derived modal properties and existing propagator."""
import math
from pydantic import Field
from .contracts import Contract,Name,Nonnegative,Positive
from .cylindrical_modal_projection import CylindricalModalProjectionRequest,evaluate_cylindrical_modal_projection
from .piecewise_force_response import GeneralizedResponseRequest,evaluate_generalized_response,pressure_stages_to_generalized_force

class RadialPressureStage(Contract):
    start_s: Nonnegative
    internal_minus_external_pressure_pa: Nonnegative | None

class CylindricalRadialPulseRequest(Contract):
    shell: CylindricalModalProjectionRequest
    pressure_stages: tuple[RadialPressureStage,...] = Field(min_length=1,max_length=10000)
    output_times_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=10000)
    modal_damping_ratio: Nonnegative | None = None
    maximum_reviewed_absolute_radial_strain: Positive | None = Field(default=None,le=.01)
    pressure_and_uniform_radial_linear_mode_basis_id: Name
    held_uniform_pressure_radial_mode_and_elastic_strain_support_reviewed: bool = False

def evaluate_cylindrical_radial_pressure_pulse(request: CylindricalRadialPulseRequest):
    """Keep shell mass/stiffness/pressure area physically linked to one mode.

    Uniform pressure excites the axially uniform radial coordinate. Axial/head
    motion, wave propagation/reflections, local knuckle bending and nonlinear
    membrane/burst behavior are excluded. Output strain is sampled radial
    displacement over mean radius; no continuous extrema certificate is issued.
    """
    q=CylindricalRadialPulseRequest.model_validate(request.model_dump())
    if not q.held_uniform_pressure_radial_mode_and_elastic_strain_support_reviewed or q.modal_damping_ratio is None or q.maximum_reviewed_absolute_radial_strain is None or any(p.internal_minus_external_pressure_pa is None for p in q.pressure_stages):
        return dict(status='needs_data')
    projection=evaluate_cylindrical_modal_projection(q.shell)
    if projection['status']!='computed':return dict(status=projection['status'],modal_projection=projection)
    mode=projection['modes'][0]
    force=pressure_stages_to_generalized_force(tuple((p.start_s,p.internal_minus_external_pressure_pa) for p in q.pressure_stages),
        generalized_pressure_area_m2=mode['generalized_uniform_pressure_area_m2'],pressure_mode_mapping_basis_id=q.pressure_and_uniform_radial_linear_mode_basis_id,reviewed=True)
    request=GeneralizedResponseRequest(mass_kg=mode['modal_mass_kg'],stiffness_n_m=mode['modal_stiffness_n_m'],
        damping_n_s_m=2*q.modal_damping_ratio*math.sqrt(mode['modal_mass_kg']*mode['modal_stiffness_n_m']),force_stages=force,
        output_times_s=q.output_times_s,load_and_coordinate_basis_id=q.pressure_and_uniform_radial_linear_mode_basis_id,
        linear_generalized_coordinate_and_held_force_basis_reviewed=True)
    response=evaluate_generalized_response(request)
    if response.status!='computed':return dict(status=response.status,modal_projection=projection,response=response.model_dump(mode='json'))
    maximum=max(abs(p.displacement_m)/q.shell.mean_radius_m for p in response.points)
    return dict(status='computed' if maximum<=q.maximum_reviewed_absolute_radial_strain else 'out_of_scope',
        modal_projection=projection,response=response.model_dump(mode='json'),maximum_sampled_absolute_radial_strain=maximum,
        continuous_extrema_full_shell_or_burst_acceptance_qualified=False)
