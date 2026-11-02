"""Supplied-coefficient soil-supported pipe ovalization screening coordinate."""
from .contracts import Contract,Positive,Nonnegative,Name

class BuriedOvalizationRequest(Contract):
    downward_pipe_loading_pressure_pa: Nonnegative
    pipe_diameter_m: Positive
    pipe_wall_thickness_m: Positive
    pipe_young_modulus_pa: Positive
    wall_bending_rigidity_per_unit_axial_width_n_m: Positive
    pipe_reference_radius_m: Positive
    soil_reaction_modulus_pa: Nonnegative | None = None
    supplied_load_distribution_coefficient: Positive | None = None
    supplied_soil_stiffness_coefficient: Positive | None = None
    supplied_wall_bending_recovery_coefficient: Positive | None = None
    maximum_reviewed_diameter_change_fraction: Positive
    pressure_geometry_soil_and_coefficient_basis_id: Name
    small_ovalization_and_soil_coefficient_applicability_reviewed: bool = False

def evaluate_buried_ovalization(request: BuriedOvalizationRequest):
    """Expose a reviewed scalar relation without selecting soil calibration.

    Bending rigidity is per unit axial width (N m), rather than full pipe beam
    rigidity (N m²). Pressure must already represent the reviewed pipe loading
    distribution. The scalar approximation does not infer contact, fatigue life,
    traffic footprint, soil modulus or coefficient applicability from geometry.
    """
    q=BuriedOvalizationRequest.model_validate(request.model_dump())
    if 2*q.pipe_wall_thickness_m>=q.pipe_diameter_m:raise ValueError('wall thickness must leave positive pipe bore')
    base=dict(basis_id=q.pressure_geometry_soil_and_coefficient_basis_id,
        soil_correlation_calibration_contact_field_or_acceptance_qualified=False)
    data=(q.soil_reaction_modulus_pa,q.supplied_load_distribution_coefficient,
          q.supplied_soil_stiffness_coefficient,q.supplied_wall_bending_recovery_coefficient)
    if not q.small_ovalization_and_soil_coefficient_applicability_reviewed or any(v is None for v in data):
        return dict(status='needs_data',**base)
    pipe_stiffness=q.wall_bending_rigidity_per_unit_axial_width_n_m/q.pipe_reference_radius_m**3
    soil_stiffness=q.supplied_soil_stiffness_coefficient*q.soil_reaction_modulus_pa
    fraction=q.supplied_load_distribution_coefficient*q.downward_pipe_loading_pressure_pa/(pipe_stiffness+soil_stiffness)
    if fraction>q.maximum_reviewed_diameter_change_fraction:return dict(status='out_of_scope',**base)
    return dict(status='computed',**base,diameter_change_fraction=fraction,diameter_change_m=fraction*q.pipe_diameter_m,
        nominal_wall_bending_stress_coordinate_pa=q.supplied_wall_bending_recovery_coefficient*q.pipe_young_modulus_pa*fraction*q.pipe_wall_thickness_m/q.pipe_diameter_m,
        pipe_stiffness_coordinate_pa=pipe_stiffness,soil_stiffness_coordinate_pa=soil_stiffness)
