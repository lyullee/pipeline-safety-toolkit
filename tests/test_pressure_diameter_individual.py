"""Independent membrane equilibrium, plane restraint and work conjugacy."""
import math

import numpy as np
import pytest

from pipeline_integrity.pipe_pressure_compliance import PressureDiameterRequest as Request
from pipeline_integrity.pipe_pressure_compliance import evaluate_pressure_diameter_compliance as evaluate

BASE=dict(reference_internal_diameter_m=.4,wall_thickness_m=.008,young_modulus_pa=200e9,
          differential_pressure_increment_pa=5e5,maximum_absolute_hoop_strain=.01,
          thin_wall_linear_elastic_and_operating_stability_reviewed=True)


@pytest.mark.parametrize("condition",["zero_axial_stress","zero_axial_strain"])
@pytest.mark.parametrize("nu",[-.2,0.,.3])
def test_diameter_satisfies_independent_membrane_hooke_system_and_elastic_work(condition,nu):
    q=Request(**BASE,axial_condition=condition,poisson_ratio=nu)
    r=evaluate(q)
    hoop_stress=q.differential_pressure_increment_pa*q.reference_internal_diameter_m/(2*q.wall_thickness_m)
    # Unknowns are hoop strain and axial stress, with the axial boundary supplied separately.
    matrix=[[q.young_modulus_pa,nu],[0.,1.]]
    rhs=[hoop_stress,0. if condition=="zero_axial_stress" else nu*hoop_stress]
    hoop_strain,axial_stress=np.linalg.solve(matrix,rhs)
    assert r.hoop_strain_increment==pytest.approx(hoop_strain,rel=3e-15,abs=1e-20)
    assert r.diameter_increment_m==pytest.approx(q.reference_internal_diameter_m*hoop_strain,rel=3e-15)
    axial_strain=(axial_stress-nu*hoop_stress)/q.young_modulus_pa
    assert (axial_stress if condition=="zero_axial_stress" else axial_strain)==pytest.approx(0.,abs=1e-20)
    wall_volume=math.pi*q.reference_internal_diameter_m*q.wall_thickness_m
    elastic_work=.5*(hoop_stress*hoop_strain+axial_stress*axial_strain)*wall_volume
    cavity_increment=math.pi*q.reference_internal_diameter_m*r.diameter_increment_m/2
    pressure_work=.5*q.differential_pressure_increment_pa*cavity_increment
    assert elastic_work==pytest.approx(pressure_work,rel=3e-15)


@pytest.mark.parametrize("condition",["zero_axial_stress","zero_axial_strain"])
def test_pressure_unloading_and_zero_increment_preserve_signed_linear_response(condition):
    def run(pressure):
        return evaluate(Request(**(BASE|dict(differential_pressure_increment_pa=pressure)),
                                axial_condition=condition,poisson_ratio=.3))
    positive,negative,zero=run(5e5),run(-5e5),run(0.)
    assert negative.diameter_increment_m==-positive.diameter_increment_m
    assert zero.diameter_increment_m==zero.linear_area_fraction_increment==0.
    assert positive.linear_area_fraction_increment==pytest.approx(2*positive.hoop_strain_increment)


@pytest.mark.parametrize("field,multiplier,ratio",[
    ("reference_internal_diameter_m",2.,4.),("wall_thickness_m",2.,.5),("young_modulus_pa",2.,.5)])
def test_geometric_and_material_similarity(field,multiplier,ratio):
    a=evaluate(Request(**BASE,axial_condition="zero_axial_stress"))
    b=evaluate(Request(**(BASE|{field:BASE[field]*multiplier}),axial_condition="zero_axial_stress"))
    assert b.diameter_increment_m/a.diameter_increment_m==pytest.approx(ratio)


@pytest.mark.parametrize("changes",[{"maximum_absolute_hoop_strain":None},
    {"thin_wall_linear_elastic_and_operating_stability_reviewed":False}])
def test_unknown_assumption_or_support_withholds_compliance(changes):
    assert evaluate(Request(**(BASE|changes),axial_condition="zero_axial_stress")).status=="needs_data"


def test_plane_strain_requires_poisson_ratio_and_support_exceedance_is_withheld():
    assert evaluate(Request(**BASE,axial_condition="zero_axial_strain")).status=="needs_data"
    r=evaluate(Request(**(BASE|dict(maximum_absolute_hoop_strain=1e-8)),axial_condition="zero_axial_stress"))
    assert r.status=="out_of_scope" and r.diameter_increment_m is None
