"""Small-strain axisymmetric thermoelastic cylinder with declared axial boundary."""
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Name

class CylindricalThermalStressRequest(Contract):
    radial_temperature_positions_m: tuple[Positive,...] = Field(min_length=2,max_length=10000)
    temperature_k: tuple[Positive,...] = Field(min_length=2,max_length=10000)
    stress_free_reference_temperature_k: Positive
    young_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=.5)
    thermal_expansion_coefficient_per_k: Nonnegative
    material_property_supported_temperature_k: tuple[Positive,Positive]
    inner_pressure_traction_pa: Nonnegative
    outer_pressure_traction_pa: Nonnegative
    axial_boundary: Literal['prescribed_axial_strain','prescribed_axial_force','closed_end_pressure_thrust']
    supplied_axial_strain: float | None = None
    supplied_axial_force_tension_positive_n: float | None = None
    query_radii_m: tuple[Positive,...] = Field(min_length=2,max_length=10000)
    maximum_sampled_absolute_small_strain: Positive
    relative_boundary_residual_tolerance: float = Field(default=1e-9,gt=0,le=1e-3)
    maximum_linear_system_condition_number: Positive = 1e12
    strain_check_radial_points: int = Field(default=201,ge=3,le=10000)
    geometry_temperature_traction_axial_and_material_basis_id: Name
    axisymmetric_constant_property_small_strain_thermoelasticity_reviewed: bool = False

def evaluate_cylindrical_thermal_stress(request: CylindricalThermalStressRequest):
    """Solve the linear boundary-value problem by exact temperature moments.

    Temperature is a caller-supplied radial snapshot, interpolated with PCHIP.
    Isotropic elastic properties and thermal expansion are constant over the
    explicitly supported temperature interval. The axial strain is uniform;
    prescribed total axial force is enforced by the integrated stress identity.
    The PCHIP polynomial temperature moment is integrated analytically; only
    three homogeneous/axial coefficients require a small native linear solve.
    Tractions are common-reference pressures. No transient heat conduction,
    plasticity, bending/ends, temperature-dependent properties or field material
    qualification is inferred from this generalized-plane-strain idealization.
    """
    import numpy as np
    from scipy.interpolate import PchipInterpolator,PPoly
    q=CylindricalThermalStressRequest.model_validate(request.model_dump())
    radii=np.asarray(q.radial_temperature_positions_m);temperature=np.asarray(q.temperature_k)
    if len(radii)!=len(temperature) or np.any(np.diff(radii)<=0):raise ValueError('temperature radii must increase with matching values')
    ri=float(radii[0]);R=float(radii[-1]);E=q.young_modulus_pa;nu=q.poisson_ratio;alpha=q.thermal_expansion_coefficient_per_k
    if any(r<ri or r>R for r in q.query_radii_m):raise ValueError('query radius outside cylinder')
    lower,upper=q.material_property_supported_temperature_k
    if lower>=upper:raise ValueError('material temperature support must increase')
    base=dict(basis_id=q.geometry_temperature_traction_axial_and_material_basis_id,actual_material_field_or_source_family_qualified=False)
    if min(*temperature,q.stress_free_reference_temperature_k)<lower or max(*temperature,q.stress_free_reference_temperature_k)>upper:return dict(status='out_of_scope',**base)
    if not q.axisymmetric_constant_property_small_strain_thermoelasticity_reviewed:return dict(status='needs_data',**base)
    force=None;ez=None
    if q.axial_boundary=='prescribed_axial_strain':
        if q.supplied_axial_strain is None:return dict(status='needs_data',**base)
        ez=q.supplied_axial_strain
    elif q.axial_boundary=='prescribed_axial_force':
        if q.supplied_axial_force_tension_positive_n is None:return dict(status='needs_data',**base)
        force=q.supplied_axial_force_tension_positive_n
    else:force=math.pi*(ri*ri*q.inner_pressure_traction_pa-R*R*q.outer_pressure_traction_pa)
    profile_x=radii/R;x0=ri/R
    if np.any(np.diff(profile_x)<=16*np.spacing(profile_x[1:])):
        raise ValueError('temperature-coordinate spacing is below reviewed floating-point support')
    T=PchipInterpolator(profile_x,temperature,extrapolate=False)
    coefficients=T.c.copy();coefficients[-1]-=q.stress_free_reference_temperature_k
    weighted=np.zeros((5,len(profile_x)-1));weighted[:-1]=coefficients;weighted[1:]+=coefficients*profile_x[:-1]
    primitive=PPoly(weighted,profile_x,extrapolate=False).antiderivative();initial_moment=float(primitive(x0))
    moment=lambda x:primitive(x)-initial_moment
    lam=nu/((1+nu)*(1-2*nu));twoG=1/(1+nu);A=lam+twoG;thermal=alpha/(1-2*nu)
    beta=(1+nu)/(1-nu)*alpha
    def stresses(x,y,e):
        radial=y[1];hoop=y[0]/x;theta=T(x)-q.stress_free_reference_temperature_k
        common=lam*(radial+hoop+e)-thermal*theta
        return twoG*radial+common,twoG*hoop+common,twoG*e+common
    def state(x,c):
        a,b,_=c;I=moment(x);theta=T(x)-q.stress_free_reference_temperature_k
        return np.array((a*x+b/x+beta*I/x,a-b/x**2+beta*(theta-I/x**2)))
    def axial_force_scaled(c):
        return math.pi*(1-x0*x0)*(2*lam*c[0]+A*c[2])+2*math.pi*(lam*beta-thermal)*float(moment(1.))
    def observations(c):
        return np.array([stresses(x0,state(x0,c),c[2])[0],stresses(1.,state(1.,c),c[2])[0],
            c[2] if ez is not None else axial_force_scaled(c)])
    zero=observations((0.,0.,0.));matrix=np.column_stack([observations(unit)-zero for unit in np.eye(3)])
    condition=float(np.linalg.cond(matrix))
    if not math.isfinite(condition) or condition>q.maximum_linear_system_condition_number:return dict(status='solver_withheld',**base,linear_system_condition_number=condition)
    target=np.array([-q.inner_pressure_traction_pa/E,-q.outer_pressure_traction_pa/E,ez if ez is not None else force/(E*R*R)])
    c=np.linalg.solve(matrix,target-zero);residual=float(np.max(np.abs(observations(c)-target)))
    if residual>q.relative_boundary_residual_tolerance:return dict(status='solver_withheld',**base,maximum_normalized_boundary_residual=residual)
    ez=float(c[2]);query_x=np.asarray(q.query_radii_m)/R
    sample_x=np.unique(np.concatenate((profile_x,np.linspace(x0,1,q.strain_check_radial_points),query_x)))
    y=state(sample_x,c);maximum=float(max(np.max(np.abs(y[1])),np.max(np.abs(y[0]/sample_x)),abs(ez)))
    y=state(query_x,c);sr,st,sz=stresses(query_x,y,ez);temperatures=T(query_x)
    rows=[]
    for i,r in enumerate(q.query_radii_m):
        rows.append(dict(radius_m=r,temperature_k=float(temperatures[i]),radial_displacement_m=float(R*y[0,i]),radial_strain=float(y[1,i]),
            hoop_strain=float(y[0,i]/query_x[i]),axial_strain=ez,radial_stress_tension_positive_pa=float(E*sr[i]),
            hoop_stress_tension_positive_pa=float(E*st[i]),axial_stress_tension_positive_pa=float(E*sz[i])))
    return dict(status='computed' if maximum<=q.maximum_sampled_absolute_small_strain else 'out_of_scope',**base,rows=rows,
        axial_force_tension_positive_n=float(axial_force_scaled(c)*E*R*R),uniform_axial_strain=ez,
        maximum_sampled_absolute_strain=maximum,continuous_strain_extrema_certified=False,temperature_segments=len(profile_x)-1,
        maximum_normalized_boundary_residual=residual,linear_system_condition_number=condition,
        solution_method='Exact piecewise-polynomial temperature moment and three-coefficient linear boundary solve',temperature_interpolation='PCHIP on supplied radial snapshot')
