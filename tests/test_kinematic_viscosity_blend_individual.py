"""Coordinate arithmetic checks, not empirical mixture-property validation."""
from decimal import Decimal, localcontext
import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.kinematic_viscosity_blend import (
    KinematicViscosityBlendRequest as Request,
    ViscosityBlendComponent as Component,
    evaluate_kinematic_viscosity_blend as evaluate,
)


def component(id, fraction, cst=None, **kw):
    return Component(id=id, fraction=fraction, temperature_K=313.15, pressure_Pa_abs=101325,
        property_origin="manufactured-coordinate", kinematic_viscosity_m2_s=None if cst is None else cst*1e-6, **kw)


def request(*components, **kw):
    return Request(components=components, fraction_basis=kw.pop("fraction_basis", "mass"),
        temperature_K=313.15, pressure_Pa_abs=101325, state_id="same-state",
        homogeneous_newtonian_liquid_reviewed=kw.pop("homogeneous_newtonian_liquid_reviewed", True), **kw)


@pytest.mark.parametrize("viscosities,weights", [([1., 10.], [.3,.7]), ([.21, 1e5],[.91,.09]),
    ([5., 50., 500.],[.2,.3,.5]), ([.4,1.4,1.8],[.2,.3,.5])])
def test_high_precision_coordinate_inversion(viscosities, weights):
    with localcontext() as ctx:
        ctx.prec=70
        index=sum(Decimal(str(w))*(Decimal(str(v))+Decimal('.8')).ln().ln() for v,w in zip(viscosities,weights))
        expected=(index.exp().exp()-Decimal('.8'))*Decimal('1e-6')
    result=evaluate(request(*(component(str(i),w,v) for i,(v,w) in enumerate(zip(viscosities,weights)))))
    assert result.blended_kinematic_viscosity_m2_s == pytest.approx(float(expected),rel=2e-14)
    assert not result.field_or_standard_qualified
    assert result.issues # Empirical validity remains explicitly unknown.


@pytest.mark.parametrize("nu", [.2001, .5, 2., 100., 1e5])
def test_pure_and_identical_components(nu):
    a=evaluate(request(component('a',1,nu)))
    b=evaluate(request(component('a',.37,nu),component('b',.63,nu)))
    assert a.blended_kinematic_viscosity_m2_s == pytest.approx(nu*1e-6,rel=2e-14)
    assert b.blended_kinematic_viscosity_m2_s == pytest.approx(a.blended_kinematic_viscosity_m2_s,rel=2e-14)


def test_mass_volume_recipe_conversion_with_unequal_densities():
    volume=evaluate(request(component('a',.5,2,density_kg_m3=500),component('b',.5,40,density_kg_m3=1000),fraction_basis='charged_component_volume'))
    mass=evaluate(request(component('a',1/3,2),component('b',2/3,40)))
    assert volume.mass_fractions == pytest.approx({'a':1/3,'b':2/3})
    assert volume.blended_kinematic_viscosity_m2_s == pytest.approx(mass.blended_kinematic_viscosity_m2_s)
    assert volume.blended_dynamic_viscosity_Pa_s is None # No fabricated final density.


def test_dynamic_coordinate_conversion_and_independent_mixture_density():
    a=evaluate(request(component('a',.4,None,dynamic_viscosity_Pa_s=.008,density_kg_m3=800),component('b',.6,2),actual_mixture_density_kg_m3=950))
    b=evaluate(request(component('a',.4,10),component('b',.6,2),actual_mixture_density_kg_m3=750))
    assert a.blended_kinematic_viscosity_m2_s == pytest.approx(b.blended_kinematic_viscosity_m2_s)
    assert a.blended_dynamic_viscosity_Pa_s == pytest.approx(a.blended_kinematic_viscosity_m2_s*950)
    assert b.blended_dynamic_viscosity_Pa_s == pytest.approx(b.blended_kinematic_viscosity_m2_s*750)


def test_missing_density_and_unknown_active_property_preserved():
    a=evaluate(request(component('a',1,10),fraction_basis='charged_component_volume'))
    b=evaluate(request(component('a',1,None,dynamic_viscosity_Pa_s=.01)))
    c=evaluate(request(component('a',1,None)))
    assert all(r.status=='needs_data' and r.blended_kinematic_viscosity_m2_s is None for r in [a,b,c])


def test_zero_component_and_input_permutation():
    a=component('a',.3,1);b=component('b',.7,100);z=component('unknown-zero',0,None)
    x=evaluate(request(a,b,z));y=evaluate(request(z,b,a))
    assert x.blended_kinematic_viscosity_m2_s == y.blended_kinematic_viscosity_m2_s
    assert x.mass_fractions['unknown-zero']==0


@pytest.mark.parametrize('nu',[.2,.1])
def test_log_boundary_rejected(nu):
    with pytest.raises(ValueError,match='0.2 cSt'): evaluate(request(component('a',1,nu)))


@pytest.mark.parametrize('change',[{'temperature_K':323.15},{'pressure_Pa_abs':202650}])
def test_same_state_guard(change):
    c=component('a',1,10).model_copy(update=change)
    with pytest.raises(ValueError,match='same|share'): evaluate(request(c))


@pytest.mark.parametrize('components',[(component('a',.5,1),),(component('a',.5,1),component('a',.5,10))])
def test_fraction_and_identity_guards(components):
    with pytest.raises(ValidationError):request(*components)


def test_condition_review_and_applicability_not_automatic():
    a=evaluate(request(component('a',1,10),homogeneous_newtonian_liquid_reviewed=False))
    assert a.status=='needs_data'
    b=evaluate(request(component('a',1,10),measured_blend_applicability_evidence='external-reviewed-record'))
    assert b.empirical_applicability_evidence=='external-reviewed-record'
    assert not b.field_or_standard_qualified


def test_monotonic_coordinate_and_convex_range():
    results=[evaluate(request(component('a',x,1),component('b',1-x,100))).blended_kinematic_viscosity_m2_s for x in [0,.1,.5,.9,1]]
    assert all(a>b for a,b in zip(results,results[1:]))
    assert all(1e-6*(1-1e-14)<=r<=100e-6*(1+1e-14) for r in results)


def test_coordinate_affine_scaling_cancels():
    # Coordinate definition invariance, not an experimental physical oracle.
    index=[math.log(math.log(v+.8)) for v in [2,50]]
    raw=.3*index[0]+.7*index[1]
    scaled=(.3*(17*index[0]+9)+.7*(17*index[1]+9)-9)/17
    assert raw==pytest.approx(scaled,abs=1e-15)
