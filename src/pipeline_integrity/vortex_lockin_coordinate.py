"""Supplied-calibration lock-in amplitude and bending-stress coordinates."""
import math
import sys
from .contracts import Contract,Positive,Nonnegative,Name
from .mechanical_screening_quantities import MassDampingIndexRequest,evaluate_mass_damping_index

class VortexLockinCoordinateRequest(Contract):
    obstruction_outside_diameter_m: Positive
    strouhal_number: Positive
    damping_ratio: Nonnegative
    obstruction_and_displaced_fluid_mass_kg_m: Positive
    fluid_density_kg_m3: Positive
    supplied_amplitude_numerator: Positive | None = None
    supplied_amplitude_denominator_offset: Positive | None = None
    supplied_bending_recovery_multiplier: Positive | None = None
    elastic_modulus_pa: Positive | None = None
    bending_second_moment_m4: Positive | None = None
    free_span_m: Positive | None = None
    section_modulus_m3: Positive | None = None
    geometry_added_mass_damping_and_lockin_calibration_basis_id: Name
    lockin_amplitude_and_bending_recovery_support_reviewed: bool = False

def evaluate_vortex_lockin_coordinate(request: VortexLockinCoordinateRequest):
    """Use a reviewed lock-in correlation, without predicting lock-in onset.

    Added-fluid mass, viscous damping convention and empirical coefficients are
    explicit. The source-purpose mass-damping index reuses the existing scalar
    owner. A bending coefficient is not an inferred eigenmode or actual stress
    field, and the resulting stress coordinate is not a fatigue assessment.
    """
    q=VortexLockinCoordinateRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.geometry_added_mass_damping_and_lockin_calibration_basis_id,actual_lockin_amplitude_stress_or_calibration_qualified=False)
    values=(q.supplied_amplitude_numerator,q.supplied_amplitude_denominator_offset,q.supplied_bending_recovery_multiplier,
        q.elastic_modulus_pa,q.bending_second_moment_m4,q.free_span_m,q.section_modulus_m3)
    if not q.lockin_amplitude_and_bending_recovery_support_reviewed or any(v is None for v in values):return dict(status='needs_data',**base)
    damping=evaluate_mass_damping_index(MassDampingIndexRequest(span_mass_kg_m=q.obstruction_and_displaced_fluid_mass_kg_m,
        supplied_air_damping_parameter=2*math.pi*q.damping_ratio,damping_parameter_definition='two-pi-times-viscous-damping-ratio',
        surrounding_density_kg_m3=q.fluid_density_kg_m3,outside_diameter_m=q.obstruction_outside_diameter_m,parameter_and_mass_basis_reviewed=True))
    C=damping.mass_damping_index
    if C==0 and q.damping_ratio>0:
        return dict(status='out_of_scope',reason='positive mass damping is not numerically resolved',**base)
    offset_log=.5*math.log(q.supplied_amplitude_denominator_offset)
    if C==0:
        denominator_log=offset_log
    else:
        damping_log=math.log(2*math.pi)+2*math.log(q.strouhal_number)+math.log(C)
        higher=max(offset_log,damping_log);lower=min(offset_log,damping_log)
        denominator_log=higher+.5*math.log1p(math.exp(2*(lower-higher)))
    amplitude_log=math.log(q.obstruction_outside_diameter_m)+math.log(q.supplied_amplitude_numerator)-denominator_log
    stress_log=(math.log(q.supplied_bending_recovery_multiplier)+math.log(q.elastic_modulus_pa)
                +math.log(q.bending_second_moment_m4)+amplitude_log-2*math.log(q.free_span_m)-math.log(q.section_modulus_m3))
    if any(not math.log(sys.float_info.min)<=v<=math.log(sys.float_info.max) for v in (amplitude_log,stress_log)):
        return dict(status='out_of_scope',reason='positive lock-in response is not numerically representable',**base)
    amplitude=math.exp(amplitude_log)
    return dict(status='computed',**base,mass_damping_index=C,lockin_displacement_amplitude_coordinate_m=amplitude,
        bending_stress_coordinate_pa=math.exp(stress_log))
