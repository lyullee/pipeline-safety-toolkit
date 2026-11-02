"""Additional uniaxial elastic stress for a prescribed material-line length.

This is a compatibility observer. It does not find an equilibrium installation
profile, predict soil slip, feed material through supports or select an allowable.
"""
import math
from .contracts import Contract, Name, Positive, digest


class CircularProfileArc(Contract):
    radius_m: Positive
    signed_sweep_rad: float


class PrescribedMaterialLine(Contract):
    straight_lengths_m: tuple[Positive, ...] = ()
    circular_arcs: tuple[CircularProfileArc, ...] = ()

    def length_m(self):
        result=math.fsum((*self.straight_lengths_m,*(a.radius_m*abs(a.signed_sweep_rad) for a in self.circular_arcs)))
        if not math.isfinite(result) or result<=0:raise ValueError('positive finite prescribed line length required')
        return result


class LoweringAxialCompatibilityRequest(Contract):
    id: Name
    initial_stress_free_material_line: PrescribedMaterialLine
    final_prescribed_material_line: PrescribedMaterialLine
    young_modulus_pa: Positive
    maximum_reviewed_absolute_engineering_strain: Positive
    signed_initial_uniform_axial_stress_pa: float = 0.
    uniform_linear_elastic_same_material_no_feed_or_slip_reviewed: bool = False
    thermal_and_pressure_state_unchanged_reviewed: bool = False


def evaluate_lowering_axial_compatibility(q: LoweringAxialCompatibilityRequest):
    """Observe uniform engineering extension against the declared reference line.

    The initial reference profile is the strain-free length for this additional
    deformation; any separately supplied existing uniform stress is superposed.
    No chord length is silently substituted for the actual initial material arc.
    Plane-section bending stress and axial support capacity are separate outputs.
    """
    q=LoweringAxialCompatibilityRequest.model_validate(q.model_dump())
    initial=q.initial_stress_free_material_line.length_m();final=q.final_prescribed_material_line.length_m()
    base=dict(request_digest=digest(q),initial_reference_length_m=initial,final_prescribed_length_m=final,
              actual_lowering_profile_equilibrium_predicted=False,field_or_standard_qualified=False)
    if not q.uniform_linear_elastic_same_material_no_feed_or_slip_reviewed or not q.thermal_and_pressure_state_unchanged_reviewed:
        return dict(status='needs_data',**base)
    extension=final-initial;strain=extension/initial
    if abs(strain)>q.maximum_reviewed_absolute_engineering_strain:
        return dict(status='out_of_scope',reason='prescribed extension outside caller reviewed linear-elastic strain support',**base)
    additional=q.young_modulus_pa*strain
    total=q.signed_initial_uniform_axial_stress_pa+additional
    energy=.5*additional*strain
    if not all(math.isfinite(v) for v in (extension,strain,additional,total,energy)):
        raise ValueError('compatibility response exceeds finite support')
    return dict(status='computed',**base,values=dict(signed_length_change_m=extension,
        signed_additional_engineering_strain=strain,signed_additional_axial_stress_pa=additional,
        signed_superposed_uniform_axial_stress_pa=total,additional_elastic_energy_per_reference_volume_j_m3=energy))
