"""Local elastic driving-energy and supplied impact-energy resistance screen."""
import math
from .contracts import Contract,Positive,Nonnegative,Name

class LocalCrackArrestEnergyRequest(Contract):
    tensile_opening_stress_pa: Nonnegative
    pipe_reference_radius_m: Positive
    elastic_modulus_pa: Positive
    supplied_impact_energy_j: Nonnegative | None = None
    impact_specimen_net_section_area_m2: Positive | None = None
    supplied_impact_to_crack_area_energy_factor: Positive | None = None
    opening_stress_geometry_and_energy_transfer_basis_id: Name
    elastic_driving_coordinate_and_impact_energy_transfer_reviewed: bool = False

def evaluate_local_crack_arrest_energy(request: LocalCrackArrestEnergyRequest):
    """Evaluate a local energy inequality; do not predict dynamic arrest.

    Driving coordinate is uniform uniaxial elastic energy density times a
    circumferential release length. Resistance requires a separately reviewed
    impact-specimen to new-crack-area transfer factor. Decompression history,
    crack speed, actual resistance calibration and material acceptance are not
    inferred, and are not mandatory extra inputs for this scalar screen.
    """
    q=LocalCrackArrestEnergyRequest.model_validate(request.model_dump())
    driving=q.tensile_opening_stress_pa**2/(2*q.elastic_modulus_pa)*(2*math.pi*q.pipe_reference_radius_m)
    base=dict(basis_id=q.opening_stress_geometry_and_energy_transfer_basis_id,driving_energy_coordinate_j_m2=driving,
        actual_dynamic_arrest_or_material_transfer_qualified=False)
    if not q.elastic_driving_coordinate_and_impact_energy_transfer_reviewed or any(v is None for v in
        (q.supplied_impact_energy_j,q.impact_specimen_net_section_area_m2,q.supplied_impact_to_crack_area_energy_factor)):
        return dict(status='needs_data',**base)
    resistance=q.supplied_impact_to_crack_area_energy_factor*q.supplied_impact_energy_j/q.impact_specimen_net_section_area_m2
    return dict(status='computed',**base,supplied_resistance_coordinate_j_m2=resistance,
        strictly_below_supplied_local_resistance=driving<resistance)
