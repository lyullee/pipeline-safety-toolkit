"""Uniform thin-wall thermoelastic area observations using existing owners.

The pressure response and temperature-curve integration are delegated unchanged.
This first-order local constitutive observation does not advance a fluid PDE or
establish absolute pressure, prestress, buckling, support or material validity.
"""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Positive
from .pipe_pressure_compliance import (
    PressureDiameterRequest,PressureDiameterResult,StrainSupport,
    evaluate_pressure_diameter_compliance)
from .thermal_expansion import ExpansionCurve,integrate_thermal_expansion


class ThermoelasticAreaRequest(Contract):
    pressure: PressureDiameterRequest
    reference_temperature_K: Positive
    temperature_K: Positive
    thermal_expansion_curve: ExpansionCurve | None = None
    coefficient_basis: Literal['free_linear_engineering','free_area_engineering','free_volumetric_engineering']
    maximum_absolute_total_hoop_strain: StrainSupport | None = None
    uniform_temperature_isotropic_linear_thermoelasticity_reviewed: bool = False


class ThermoelasticAreaResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_result: PressureDiameterResult | None = None
    reference_area_m2: Positive | None = None
    free_linear_thermal_strain: float | None = None
    mechanical_hoop_strain: float | None = None
    constrained_thermal_hoop_strain: float | None = None
    total_hoop_strain: float | None = None
    linear_pressure_area_fraction: float | None = None
    linear_thermal_area_fraction: float | None = None
    linear_total_area_fraction: float | None = None
    linear_area_increment_m2: float | None = None
    linearized_area_m2: Positive | None = None
    linear_diameter_increment_m: float | None = None
    issues: tuple[str,...] = ()
    finite_strain_geometry_claimed: Literal[False] = False


def evaluate_thermoelastic_area(request: ThermoelasticAreaRequest) -> ThermoelasticAreaResult:
    """Add compatible thermal hoop strain to the shared pressure observation.

    A free linear tangent coefficient integrates directly to thermal eigenstrain;
    a free area engineering coefficient integrates to twice that eigenstrain
    under the reviewed isotropic infinitesimal-strain relation. A volumetric
    coefficient is not silently divided by three. Zero axial stress uses the
    free hoop thermal strain; zero total axial strain increases it by (1+nu)
    through the axial thermal reaction. The latter is not a freely heated pipe.
    """
    if not isinstance(request,ThermoelasticAreaRequest):raise TypeError('ThermoelasticAreaRequest required')
    q=ThermoelasticAreaRequest.model_validate(request.model_dump(mode='python'))
    issues=[]
    if not q.uniform_temperature_isotropic_linear_thermoelasticity_reviewed:
        issues.append('uniform temperature and isotropic small-strain thermoelasticity require review')
    if q.maximum_absolute_total_hoop_strain is None:issues.append('missing total hoop-strain support')
    if q.thermal_expansion_curve is None:issues.append('missing thermal expansion curve')
    elif not q.thermal_expansion_curve.tangent_small_engineering_strain_basis_reviewed:
        issues.append('tangent engineering-strain coefficient basis requires review')
    if issues:return ThermoelasticAreaResult(status='needs_data',issues=tuple(issues))
    if q.coefficient_basis=='free_volumetric_engineering':
        return ThermoelasticAreaResult(status='out_of_scope',issues=(
            'volumetric coefficient does not establish a free radial material law; no factor-of-three conversion inferred',))
    mechanical=evaluate_pressure_diameter_compliance(q.pressure)
    if mechanical.status!='computed':
        return ThermoelasticAreaResult(status=mechanical.status,pressure_result=mechanical,issues=mechanical.issues)
    curve=q.thermal_expansion_curve
    if min(q.reference_temperature_K,q.temperature_K)<curve.knots[0].temperature_K or max(q.reference_temperature_K,q.temperature_K)>curve.knots[-1].temperature_K:
        return ThermoelasticAreaResult(status='out_of_scope',pressure_result=mechanical,
            issues=('temperature exceeds supplied coefficient support',))
    integrated=integrate_thermal_expansion(curve,q.reference_temperature_K,q.temperature_K)['free_engineering_strain']
    free=integrated/(2 if q.coefficient_basis=='free_area_engineering' else 1)
    multiplier=1 if q.pressure.axial_condition=='zero_axial_stress' else 1+q.pressure.poisson_ratio
    thermal=multiplier*free;total=mechanical.hoop_strain_increment+thermal
    if abs(total)>q.maximum_absolute_total_hoop_strain:
        return ThermoelasticAreaResult(status='out_of_scope',pressure_result=mechanical,
            issues=('reviewed total hoop-strain support exceeded',))
    area=math.pi*q.pressure.reference_internal_diameter_m**2/4
    fraction=2*total;delta=area*fraction;updated=area+delta
    if not all(math.isfinite(x) for x in (free,thermal,total,area,fraction,delta,updated)):
        raise ValueError('thermoelastic area exceeds finite support')
    if updated<=0:
        return ThermoelasticAreaResult(status='out_of_scope',pressure_result=mechanical,
            issues=('linear area approximation does not leave a positive area',))
    return ThermoelasticAreaResult(status='computed',pressure_result=mechanical,reference_area_m2=area,
        free_linear_thermal_strain=free,mechanical_hoop_strain=mechanical.hoop_strain_increment,
        constrained_thermal_hoop_strain=thermal,total_hoop_strain=total,
        linear_pressure_area_fraction=mechanical.linear_area_fraction_increment,
        linear_thermal_area_fraction=2*thermal,linear_total_area_fraction=fraction,
        linear_area_increment_m2=delta,linearized_area_m2=updated,
        linear_diameter_increment_m=q.pressure.reference_internal_diameter_m*total)


class ClosedEndThermoelasticRequest(Contract):
    unconstrained_base: ThermoelasticAreaRequest
    maximum_absolute_mechanical_axial_strain: StrainSupport | None = None
    maximum_absolute_total_axial_strain: StrainSupport | None = None
    closed_end_pressure_force_and_no_external_axial_restraint_reviewed: bool = False


class ClosedEndThermoelasticResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    unconstrained_base_result: ThermoelasticAreaResult | None = None
    mechanical_hoop_strain: float | None = None
    mechanical_axial_strain: float | None = None
    total_hoop_strain: float | None = None
    total_axial_strain: float | None = None
    linear_area_fraction_increment: float | None = None
    linear_volume_fraction_increment: float | None = None
    linear_pressure_volume_fraction: float | None = None
    linear_thermal_volume_fraction: float | None = None
    linear_area_increment_m2: float | None = None
    issues: tuple[str,...] = ()
    finite_strain_geometry_claimed: Literal[False] = False


def evaluate_closed_end_thermoelastic_geometry(request: ClosedEndThermoelasticRequest) -> ClosedEndThermoelasticResult:
    """Separate area and volume under closed-end pressure force, free extension.

    The unchanged zero-axial-stress pressure owner supplies the hoop-stress/E
    baseline. Closed ends impose axial stress equal to one half of hoop stress;
    isotropic Hooke compatibility then changes both hoop and axial strain. Free
    thermal eigenstrain is added to each direction, hence the first-order volume
    increment includes three such contributions while area includes two. This
    explicitly declared geometry does not reinterpret a volume coefficient as
    an area coefficient or infer end loading from a generic 'free ends' label.
    """
    q=ClosedEndThermoelasticRequest.model_validate(request.model_dump(mode='python'))
    b=q.unconstrained_base;issues=[]
    if not q.closed_end_pressure_force_and_no_external_axial_restraint_reviewed:
        issues.append('closed-end pressure force without external axial restraint requires review')
    if b.pressure.axial_condition!='zero_axial_stress':
        return ClosedEndThermoelasticResult(status='out_of_scope',issues=('closed-end geometry requires the zero-axial-stress pressure baseline',))
    if b.pressure.poisson_ratio is None:issues.append('Poisson ratio required for closed-end compatibility')
    if q.maximum_absolute_mechanical_axial_strain is None or q.maximum_absolute_total_axial_strain is None:
        issues.append('mechanical and total axial-strain support required')
    if issues:return ClosedEndThermoelasticResult(status='needs_data',issues=tuple(issues))
    base=evaluate_thermoelastic_area(b)
    if base.status!='computed':return ClosedEndThermoelasticResult(status=base.status,unconstrained_base_result=base,issues=base.issues)
    nu=b.pressure.poisson_ratio;e0=base.mechanical_hoop_strain;s=base.free_linear_thermal_strain
    hoop=(1-nu/2)*e0;axial=(.5-nu)*e0;ht=hoop+s;at=axial+s
    if abs(hoop)>b.pressure.maximum_absolute_hoop_strain or abs(ht)>b.maximum_absolute_total_hoop_strain or abs(axial)>q.maximum_absolute_mechanical_axial_strain or abs(at)>q.maximum_absolute_total_axial_strain:
        return ClosedEndThermoelasticResult(status='out_of_scope',unconstrained_base_result=base,
            issues=('closed-end mechanical/total hoop or axial support exceeded',))
    area_fraction=2*ht;volume_fraction=area_fraction+at
    if min(1+area_fraction,1+volume_fraction)<=0:
        return ClosedEndThermoelasticResult(status='out_of_scope',unconstrained_base_result=base,
            issues=('linear area or volume approximation is nonpositive',))
    return ClosedEndThermoelasticResult(status='computed',unconstrained_base_result=base,
        mechanical_hoop_strain=hoop,mechanical_axial_strain=axial,total_hoop_strain=ht,total_axial_strain=at,
        linear_area_fraction_increment=area_fraction,linear_volume_fraction_increment=volume_fraction,
        linear_pressure_volume_fraction=2*hoop+axial,linear_thermal_volume_fraction=3*s,
        linear_area_increment_m2=base.reference_area_m2*area_fraction)
