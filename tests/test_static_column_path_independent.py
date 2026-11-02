"""Independent static column pressure/force references, no pig dynamics."""
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import solve_ivp

from pipeline_integrity.static_column_path import StaticColumnSegment as Segment, StaticNodePressureBounds as Bounds, StaticColumnPathRequest as Request, evaluate_static_column_path as evaluate


def segments(heights=(0.,80.,0.,50.,0.),densities=None):
    if densities is None:densities=[910.]*(len(heights)-1)
    return tuple(Segment(start_node_id=f"n{i}",end_node_id=f"n{i+1}",start_upward_elevation_m=a,
        end_upward_elevation_m=b,density_kg_m3=rho,fluid_state_basis_id=f"state-{i}",
        phase="single_gas" if rho<10 else "single_liquid",constant_density_column_reviewed=True)
        for i,(a,b,rho) in enumerate(zip(heights,heights[1:],densities)))


def request(**changes):
    values=dict(segments=segments(),inlet_pressure_pa=4e6,pressure_reference="absolute",pressure_datum_id="absolute-datum",
        vertical_frame_id="upward-z",topology_and_interface_pressure_continuity_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("heights,densities",[
    ((0.,80.,0.,50.,0.),(910.,910.,910.,910.)),
    ((0.,80.,0.,50.,0.),(910.,3.,910.,3.)),
    ((120.,20.,95.,-10.),(880.,970.,740.)),
    ((-20.,-20.,90.),(10.,740.)),
])
@pytest.mark.parametrize("g",[3.71,9.80665])
def test_piecewise_density_pressure_ode_and_column_traction(heights,densities,g):
    q=request(segments=segments(heights,densities),gravity_m_s2=g)
    r=evaluate(q);assert r.status=="computed"
    p=q.inlet_pressure_pa;expected=[p];force_sum=0.;area=.037
    for a,b,rho in zip(heights,heights[1:],densities):
        # Independent vertical force from a signed oriented fluid prism.
        signed_mass=rho*area*(b-a)
        weight=signed_mass*g
        sol=solve_ivp(lambda s,y:(-rho*g*(b-a),),(0,1),(p,),rtol=1e-12,atol=1e-9)
        p=float(sol.y[0,-1]);expected.append(p);force_sum+=weight
    assert [n.pressure_pa for n in r.nodes]==pytest.approx(expected,rel=2e-14,abs=2e-8)
    assert r.required_inlet_minus_outlet_pressure_pa*area==pytest.approx(force_sum,abs=1e-8)
    assert (r.nodes[0].pressure_pa-r.nodes[-1].pressure_pa)*area==pytest.approx(force_sum,abs=1e-8)


def test_air_and_liquid_topology_not_sum_of_absolute_height_changes():
    uniform=evaluate(request())
    separated=evaluate(request(segments=segments(densities=(910.,3.,910.,3.))))
    assert uniform.signed_gravity_requirement_pa==0
    assert separated.signed_gravity_requirement_pa==pytest.approx((910.-3.)*9.80665*(80.+50.))
    assert separated.signed_gravity_requirement_pa < 910.*9.80665*(80.+80.+50.+50.)
    assert not separated.pig_motion_contact_bypass_or_air_interface_evolution_solved


@pytest.mark.parametrize("offset",[-8e6,0.,13e6])
def test_same_datum_gauge_shift_preserves_requirements_and_available_margin(offset):
    q=request(segments=segments(densities=(910.,3.,910.,3.)),required_terminal_pressure_pa=3e6)
    absolute=evaluate(q)
    gauge=evaluate(q.model_copy(update=dict(inlet_pressure_pa=q.inlet_pressure_pa+offset,
        required_terminal_pressure_pa=q.required_terminal_pressure_pa+offset,pressure_reference="common_gauge",pressure_datum_id="common-gauge")))
    assert gauge.status=="computed"
    assert [n.pressure_pa-offset for n in gauge.nodes]==pytest.approx([n.pressure_pa for n in absolute.nodes],abs=3e-9)
    assert gauge.available_pressure_margin_pa==pytest.approx(absolute.available_pressure_margin_pa,abs=3e-9)


@pytest.mark.parametrize("terminal",[1e6,3e6,4e6])
def test_available_pressure_margin_and_prescribed_resistance(terminal):
    segs=list(segments(densities=(910.,3.,910.,3.)))
    segs[2]=segs[2].model_copy(update=dict(prescribed_resisting_drop_pa=17000.,prescribed_resistance_basis_id="supplied-loss"))
    q=request(segments=tuple(segs),required_terminal_pressure_pa=terminal,prescribed_resistance_applicability_reviewed=True)
    r=evaluate(q)
    expected_required=(910.-3.)*q.gravity_m_s2*130+17000.
    assert r.required_inlet_minus_outlet_pressure_pa==pytest.approx(expected_required)
    assert r.available_pressure_margin_pa==pytest.approx(q.inlet_pressure_pa-terminal-expected_required)
    assert r.required_inlet_pressure_for_terminal_pa==pytest.approx(terminal+expected_required)
    assert r.nodes[-1].pressure_pa-terminal==pytest.approx(r.available_pressure_margin_pa)
    assert r.pressure_budget_includes_prescribed_resistance


def test_pressure_profile_external_bounds_are_supplied_not_code_rules():
    bounds=(Bounds(node_id="n1",lower_pressure_pa=3.5e6,upper_pressure_pa=4.1e6,bounds_basis_id="caller-envelope",external_limit_applicability_reviewed=True),)
    r=evaluate(request(node_pressure_bounds=bounds))
    node=r.nodes[1]
    assert node.pressure_pa<3.5e6 and node.lower_pressure_margin_pa<0
    assert not r.supplied_node_pressure_bounds_all_satisfied
    assert not r.field_or_standard_qualified
    assert r.nodes[0].supplied_pressure_bounds_satisfied is None


def test_external_ambient_water_pressure_has_explicit_surface_absolute_offset():
    q=request(segments=segments((0.,-170.),(1027.,)),inlet_pressure_pa=100300.)
    r=evaluate(q)
    expected_column_force=1027.*.019*170.*q.gravity_m_s2
    assert r.nodes[-1].pressure_pa*.019==pytest.approx(100300.*.019+expected_column_force)
    gauge=evaluate(q.model_copy(update=dict(inlet_pressure_pa=0.,pressure_reference="common_gauge",pressure_datum_id="same-surface-pressure")))
    assert r.nodes[-1].pressure_pa-gauge.nodes[-1].pressure_pa==pytest.approx(100300.)


def test_segment_split_and_path_reversal_preserve_static_gravity_law():
    whole=evaluate(request(segments=segments((10.,90.),(930.,))))
    split=evaluate(request(segments=segments((10.,20.,43.,90.),(930.,930.,930.))))
    reverse=evaluate(request(segments=segments((90.,10.),(930.,)),inlet_pressure_pa=whole.nodes[-1].pressure_pa))
    assert split.nodes[-1].pressure_pa==pytest.approx(whole.nodes[-1].pressure_pa)
    assert reverse.nodes[-1].pressure_pa==pytest.approx(4e6)


@pytest.mark.parametrize("field",["inlet_pressure_pa","pressure_datum_id","vertical_frame_id"])
def test_missing_boundary_or_reference(field):
    assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(density_kg_m3=None),dict(phase="unknown"),dict(fluid_state_basis_id=None),dict(constant_density_column_reviewed=False),
    dict(prescribed_resisting_drop_pa=1.)])
def test_unknown_constitutive_state_or_loss(changes):
    s=segments()[0].model_copy(update=changes)
    assert evaluate(request(segments=(s,))).status=="needs_data"


def test_unknown_topology_and_unreviewed_pressure_limits():
    assert evaluate(request(topology_and_interface_pressure_continuity_reviewed=False)).status=="needs_data"
    b=Bounds(node_id="n1",upper_pressure_pa=4e6,bounds_basis_id="not-reviewed")
    assert evaluate(request(node_pressure_bounds=(b,))).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(start_node_id="other"),dict(start_upward_elevation_m=81.),dict(end_node_id="n0")])
def test_inconsistent_interface_or_repeated_node_is_not_repaired(changes):
    s=list(segments());s[1]=s[1].model_copy(update=changes)
    assert evaluate(request(segments=tuple(s))).status=="out_of_scope"


def test_absolute_state_exit_and_invalid_bounds_or_contract():
    assert evaluate(request(inlet_pressure_pa=0.)).status=="out_of_scope"
    assert evaluate(request(inlet_pressure_pa=100000.)).status=="out_of_scope"
    with pytest.raises(ValidationError):Bounds(node_id="n0",lower_pressure_pa=3.,upper_pressure_pa=2.,bounds_basis_id="bad")
    q=request()
    with pytest.raises(ValidationError):evaluate(q.model_copy(update={"gravity_m_s2":-1.}))
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
