"""Reviewed right-angle branch limit/burst pressure correlation coordinates."""
import math
import sys
from .contracts import Contract,Positive,Name
from .nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa

class BranchPressureCoordinateRequest(Contract):
    header_outside_diameter_m: Positive
    branch_outside_diameter_m: Positive
    header_wall_thickness_m: Positive
    branch_wall_thickness_m: Positive
    supplied_header_yield_stress_pa: Positive | None = None
    supplied_header_ultimate_stress_pa: Positive | None = None
    supplied_numerator_coefficients: tuple[float,float,float,float] | None = None
    supplied_denominator_coefficients: tuple[float,float,float,float] | None = None
    supplied_burst_multiplier: Positive | None = None
    supplied_burst_normalized_limit_exponent: Positive | None = None
    right_angle_geometry_material_and_coefficient_basis_id: Name
    right_angle_unreinforced_branch_correlation_support_reviewed: bool = False

def evaluate_branch_pressure_coordinate(request: BranchPressureCoordinateRequest):
    """Expose geometry correlation independently of a local stress-field solver.

    Coefficient tuples belong to the explicitly reviewed geometry/material law;
    no reinforcement, oblique nozzle correction, weld quality or design-standard
    acceptance is inferred. The yield and ultimate nominal-pressure references
    reuse the existing outside-diameter stress owner.
    """
    q=BranchPressureCoordinateRequest.model_validate(request.model_dump())
    D=q.header_outside_diameter_m;d=q.branch_outside_diameter_m;T=q.header_wall_thickness_m;t=q.branch_wall_thickness_m
    if d>D or 2*T>=D or 2*t>=d:raise ValueError('reviewed branch geometry requires positive bores and branch diameter no larger than header')
    base=dict(basis_id=q.right_angle_geometry_material_and_coefficient_basis_id,actual_local_stress_limit_load_or_burst_calibration_qualified=False)
    if not q.right_angle_unreinforced_branch_correlation_support_reviewed or any(v is None for v in
        (q.supplied_header_yield_stress_pa,q.supplied_numerator_coefficients,q.supplied_denominator_coefficients)):
        return dict(status='needs_data',**base)
    u=t/T;v=d/D;z=v*math.sqrt(D/T);a,b,c,e=q.supplied_numerator_coefficients;f,g,h,j=q.supplied_denominator_coefficients
    numerator=(a*u*u+b*u*v+c)*z+e;denominator=f*z*z+(g*v*v+h)*z+j
    if not all(math.isfinite(x) and x>0 for x in (numerator,denominator)):
        return dict(status='out_of_scope',reason='correlation polynomials leave positive finite support',**base)
    normalized=numerator/denominator
    if not math.isfinite(normalized) or normalized<=0:
        return dict(status='out_of_scope',reason='normalized pressure leaves positive finite support',**base)
    stress_per_pa=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=1.,outside_diameter_m=D,wall_thickness_m=T,diameter_basis='outside'))
    pressure_log=math.log(normalized)+math.log(q.supplied_header_yield_stress_pa)-math.log(stress_per_pa)
    if not math.log(sys.float_info.min)<=pressure_log<=math.log(sys.float_info.max):
        return dict(status='out_of_scope',reason='positive pressure coordinate is not numerically representable',**base)
    result=dict(status='computed',**base,normalized_limit_pressure_coordinate=normalized,
        limit_pressure_coordinate_pa=math.exp(pressure_log),burst_status='needs_data')
    if all(v is not None for v in (q.supplied_header_ultimate_stress_pa,q.supplied_burst_multiplier,q.supplied_burst_normalized_limit_exponent)):
        burst_log=(math.log(q.supplied_burst_multiplier)+q.supplied_burst_normalized_limit_exponent*math.log(normalized)
                   +math.log(q.supplied_header_ultimate_stress_pa)-math.log(stress_per_pa))
        if math.log(sys.float_info.min)<=burst_log<=math.log(sys.float_info.max):
            result.update(burst_status='computed',burst_pressure_coordinate_pa=math.exp(burst_log))
        else:
            result.update(burst_status='out_of_scope',burst_issue='positive burst coordinate is not numerically representable')
    return result
