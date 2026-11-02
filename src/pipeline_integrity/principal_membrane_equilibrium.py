"""Local principal-curvature membrane normal-force balance in coherent SI.

The normal equation alone does not recover a shell stress field. Tangential
balance, compatibility, supports, bending and constitutive laws remain external.
"""
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Name,Nonnegative,Positive,digest


class PrincipalMembraneRequest(Contract):
    id: Name
    pressure_on_negative_normal_side_pa_abs: Nonnegative
    pressure_on_positive_normal_side_pa_abs: Nonnegative
    principal_curvature_1_per_m: float
    principal_curvature_2_per_m: float
    mode: Literal['observe','solve_resultant_1','solve_resultant_2','equal_resultants']
    supplied_resultant_1_n_m: float | None = None
    supplied_resultant_2_n_m: float | None = None
    supplied_uniform_membrane_thickness_m: Positive | None = None
    equilibrium_absolute_tolerance_pa: Nonnegative
    geometry_load_and_membrane_normal_convention_reviewed: bool = False
    additional_constraint_evidence_id: Name | None = None

    @model_validator(mode='after')
    def no_ignored_unknowns(self):
        if self.mode=='solve_resultant_1' and self.supplied_resultant_1_n_m is not None:raise ValueError('unknown resultant 1 must not also be supplied')
        if self.mode=='solve_resultant_2' and self.supplied_resultant_2_n_m is not None:raise ValueError('unknown resultant 2 must not also be supplied')
        if self.mode=='equal_resultants' and (self.supplied_resultant_1_n_m is not None or self.supplied_resultant_2_n_m is not None):raise ValueError('equal-resultant closure is selected instead of supplying either unknown')
        return self


def evaluate_principal_membrane(q: PrincipalMembraneRequest):
    """Observe k1*N1+k2*N2=dp or solve with an explicit extra constraint.

    n points from the negative-normal pressure side toward the positive side.
    Curvature is positive for an outward-normal sphere (dn/ds projected on its
    principal tangent). Tension-positive N in N/m creates inward normal loading;
    dp=P_negative-P_positive is pressure traction along n. Normal reversal swaps
    the pressure sides and negates both curvatures, retaining physical N.
    N/t is an optional uniform-through-thickness stress coordinate, not a
    calibrated material stress or buckling/strength assessment.
    """
    q=PrincipalMembraneRequest.model_validate(q.model_dump())
    base=dict(request_digest=digest(q),global_shell_or_strength_qualified=False)
    if not q.geometry_load_and_membrane_normal_convention_reviewed:return dict(status='needs_data',reason='local geometry/load/normal basis not reviewed',**base)
    dp=q.pressure_on_negative_normal_side_pa_abs-q.pressure_on_positive_normal_side_pa_abs
    k1=q.principal_curvature_1_per_m;k2=q.principal_curvature_2_per_m
    n1=q.supplied_resultant_1_n_m;n2=q.supplied_resultant_2_n_m
    if q.mode=='observe':
        if n1 is None or n2 is None:return dict(status='underdetermined',reason='normal balance alone does not select two resultants; observe requires both',**base)
    else:
        if q.additional_constraint_evidence_id is None:return dict(status='needs_data',reason='selected extra constraint needs caller evidence',**base)
        if q.mode=='solve_resultant_1':
            if n2 is None:return dict(status='needs_data',reason='resultant 2 is required',**base)
            coefficient=k1;target=dp-k2*n2
        elif q.mode=='solve_resultant_2':
            if n1 is None:return dict(status='needs_data',reason='resultant 1 is required',**base)
            coefficient=k2;target=dp-k1*n1
        else:coefficient=k1+k2;target=dp
        if not math.isfinite(target) or not math.isfinite(coefficient):raise ValueError('membrane inverse exceeds finite support')
        if coefficient==0:
            status='underdetermined' if abs(target)<=q.equilibrium_absolute_tolerance_pa else 'inconsistent'
            return dict(status=status,reason='zero selected curvature coefficient cannot determine requested resultant',unbalanced_pressure_pa=target,**base)
        unknown=target/coefficient
        if q.mode=='solve_resultant_1':n1=unknown
        elif q.mode=='solve_resultant_2':n2=unknown
        else:n1=n2=unknown
    first=k1*n1;second=k2*n2;pressure=first+second;residual=pressure-dp
    if not all(math.isfinite(v) for v in (n1,n2,first,second,pressure,residual)):raise ValueError('membrane result exceeds finite support')
    values=dict(resultant_1_n_m=n1,resultant_2_n_m=n2,curvature_1_normal_pressure_pa=first,
        curvature_2_normal_pressure_pa=second,balanced_pressure_pa=pressure,applied_normal_pressure_difference_pa=dp,
        normal_force_residual_pa=residual,equilibrium_within_supplied_tolerance=abs(residual)<=q.equilibrium_absolute_tolerance_pa)
    if q.supplied_uniform_membrane_thickness_m is not None:
        s1=n1/q.supplied_uniform_membrane_thickness_m;s2=n2/q.supplied_uniform_membrane_thickness_m
        if not all(math.isfinite(v) for v in (s1,s2)):raise ValueError('uniform membrane stress exceeds finite support')
        values.update(uniform_membrane_stress_1_pa=s1,uniform_membrane_stress_2_pa=s2)
    return dict(status='computed',**base,values=values)
