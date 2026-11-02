"""Ideal areal-mass impulse versus constant-flow plastic-work coordinate."""
import math
from .contracts import Contract,Positive,Name

class PlasticImpulseBudgetRequest(Contract):
    wall_thickness_m: Positive
    material_mass_density_kg_m3: Positive
    supplied_flow_stress_pa: Positive | None = None
    supplied_failure_work_strain: Positive | None = None
    pressure_pulse_duration_s: Positive
    supplied_impulse_to_peak_pressure_duration_factor: Positive | None = None
    material_work_strain_pulse_shape_and_unit_basis_id: Name
    uniform_areal_mass_constant_flow_stress_and_strain_work_reviewed: bool = False

def evaluate_plastic_impulse_budget(request: PlasticImpulseBudgetRequest):
    """Return an energy-budget pressure coordinate, not a fragmentation capacity.

    A pulse supplies impulse to uniform areal mass; its initial kinetic energy
    is compared to constant-flow plastic work. Strain/work calibration and pulse
    shape are supplied. Distributed shell inertia, strain hardening, crack
    nucleation, rate effects, pressure-wave structure and actual fragmentation
    are not inferred from this ideal coordinate.
    """
    q=PlasticImpulseBudgetRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.material_work_strain_pulse_shape_and_unit_basis_id,actual_fragmentation_or_failure_strain_calibration_qualified=False)
    if not q.uniform_areal_mass_constant_flow_stress_and_strain_work_reviewed or any(v is None for v in
        (q.supplied_flow_stress_pa,q.supplied_failure_work_strain,q.supplied_impulse_to_peak_pressure_duration_factor)):
        return dict(status='needs_data',**base)
    mass=q.material_mass_density_kg_m3*q.wall_thickness_m
    work=q.supplied_flow_stress_pa*q.supplied_failure_work_strain*q.wall_thickness_m
    impulse=math.sqrt(2*mass*work)
    return dict(status='computed',**base,areal_mass_kg_m2=mass,plastic_work_budget_j_m2=work,
        impulse_budget_pa_s=impulse,peak_pressure_coordinate_pa=impulse/(q.pressure_pulse_duration_s*q.supplied_impulse_to_peak_pressure_duration_factor))
