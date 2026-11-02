"""Independent common-density mass budget and empirical coordinate invariants."""
import math

import pytest

from pipeline_integrity.liquid_blending import LiquidBlendComponent as Component
from pipeline_integrity.liquid_blending import LiquidBlendRequest as Request
from pipeline_integrity.liquid_blending import evaluate_liquid_blend as evaluate


def calc(method,volumes,values,reviewed=True):
    return evaluate(Request(method=method,components=tuple(Component(id=f"c{i}",volume_m3=v,property_value=x)
        for i,(v,x) in enumerate(zip(volumes,values))),reference_basis="authored_common_state_and_method",
        common_property_basis_and_additive_volumes_reviewed=reviewed))


@pytest.mark.parametrize("volumes,values",[([2.,3.,5.],[.75,.81,.93]),([0.,2.,4.],[1.2,.8,.9]),([1.,3.,2.,4.],[.72,.76,.84,.88])])
def test_specific_gravity_matches_independent_component_mass_budget(volumes,values):
    rho_reference=998.2
    mass=math.fsum(v*sg*rho_reference for v,sg in zip(volumes,values))
    expected_density=mass/math.fsum(volumes)
    result=calc("specific_gravity",volumes,values)
    assert result.blended_specific_gravity*rho_reference==pytest.approx(expected_density,rel=3e-15)
    assert math.fsum(result.volume_fractions.values())==pytest.approx(1.,abs=2e-15)


def test_volume_unit_scaling_and_component_permutation_do_not_change_specific_gravity():
    volumes,values=[2.,3.,5.],[.75,.81,.93]
    a=calc("specific_gravity",volumes,values)
    b=calc("specific_gravity",[v*17 for v in volumes],values)
    c=calc("specific_gravity",list(reversed(volumes)),list(reversed(values)))
    assert a.blended_specific_gravity==pytest.approx(b.blended_specific_gravity,rel=2e-15)
    assert a.blended_specific_gravity==pytest.approx(c.blended_specific_gravity,rel=2e-15)


@pytest.mark.parametrize("values,expected",[([36.,36.,36.],36.),([25.,100.],400/9),([16.,64.],256/9)])
def test_inverse_root_coordinate_has_known_blend_values(values,expected):
    result=calc("inverse_root_SUS_efflux_time",[1.]*len(values),values)
    assert result.blended_SUS_efflux_time_s==pytest.approx(expected,rel=3e-15)


def test_empirical_efflux_relation_scales_with_property_and_stays_in_component_interval():
    volumes,values=[2.,3.,5.],[35.,49.,81.]
    a=calc("inverse_root_SUS_efflux_time",volumes,values)
    b=calc("inverse_root_SUS_efflux_time",volumes,[x*3 for x in values])
    assert b.blended_SUS_efflux_time_s==pytest.approx(3*a.blended_SUS_efflux_time_s,rel=3e-15)
    assert min(values)<=a.blended_SUS_efflux_time_s<=max(values)


@pytest.mark.parametrize("method",["specific_gravity","inverse_root_SUS_efflux_time"])
def test_unknown_or_unreviewed_inputs_are_not_dropped(method):
    assert calc(method,[1.,None],[.8,.9]).status=="needs_data"
    assert calc(method,[1.,0.],[.8,None]).status=="needs_data"
    assert calc(method,[1.,2.],[.8,.9],reviewed=False).status=="needs_data"


def test_no_total_volume_does_not_define_a_blend():
    assert calc("specific_gravity",[0.,0.],[.8,.9]).status=="needs_data"


def test_duplicate_component_identity_is_rejected():
    with pytest.raises(ValueError):
        Request(method="specific_gravity",components=(Component(id="same",volume_m3=1.,property_value=.8),
            Component(id="same",volume_m3=2.,property_value=.9)),reference_basis="common")
