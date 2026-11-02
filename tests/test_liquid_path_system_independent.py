"""Two-terminal liquid topology with independent radial/energy references."""
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import quad
from scipy.optimize import brentq

from pipeline_integrity.liquid_path_system import ReferencedLiquidLoss as Loss, LiquidPipePathElement as Element, LiquidSeriesPath as Path, LiquidEquivalentReferencePipe as ReferencePipe, LiquidPathSystemRequest as Request, evaluate_liquid_path_system as evaluate


def loss(k=.7,diameter=.025,**changes):
    values=dict(coefficient=k,velocity_reference_diameter_m=diameter,coefficient_basis_id='authored-irreversible-local-head',
        applicable_flow_sign='both',dimensionless_velocity_head_definition_reviewed=True)
    values.update(changes);return Loss(**values)


def paths(local=False):
    return (Path(path_id='a',elements=(Element(diameter_m=.04,length_m=71.,roughness_m=0.,local_losses=(loss(),) if local else ()),)),
            Path(path_id='b',elements=(Element(diameter_m=.061,length_m=29.,roughness_m=0.),)))


def request(**changes):
    values=dict(paths=paths(),boundary='prescribed_pressure_difference',inlet_node_id='upstream',outlet_node_id='downstream',
        outlet_minus_inlet_elevation_m=.2,pressure_basis='mechanical_total_pressure',density_kg_m3=900.,dynamic_viscosity_pa_s=.2,
        liquid_state_basis_id='reviewed-uniform-liquid',inlet_minus_outlet_pressure_pa=6765.197,
        branch_flow_brackets_m3_s=((-0.003,.003),(-.003,.003)),
        no_intermediate_injection_and_common_end_nodes_reviewed=True,actual_single_liquid_state_and_all_loss_bases_reviewed=True)
    values.update(changes);return Request(**values)


def radial_flow_per_gradient(diameter,mu):
    # Pressure force supplies tau(r)=G*r/2; Newtonian shear and no-slip give
    # the independent radial velocity integral. No64/Re or native f is used.
    radius=diameter/2
    def velocity(r):return quad(lambda s:s/(2*mu),r,radius,epsabs=1e-15)[0]
    return quad(lambda r:2*math.pi*r*velocity(r),0,radius,epsabs=1e-15)[0]


def reference_path_friction(path,flow,rho=900.,mu=.2):
    total=0.
    for e in path.elements:
        total+=e.length_m*flow/radial_flow_per_gradient(e.diameter_m,mu)
        for k in e.local_losses:
            area=math.pi*k.velocity_reference_diameter_m**2/4
            total+=math.copysign(.5*rho*k.coefficient*(flow/area)**2,flow) if flow else 0.
    return total


def independent_laminar_resistance(path,mu=.2):
    return math.fsum(e.length_m/radial_flow_per_gradient(e.diameter_m,mu) for e in path.elements)


@pytest.mark.parametrize('flow',[-.00071,-.00013,0.,.00013,.00071])
def test_series_actual_diameters_and_local_velocity_basis_from_radial_force(flow):
    a,b=paths();series=Path(path_id='series',elements=(a.elements[0].model_copy(update={'local_losses':(loss(),)}),
        b.elements[0].model_copy(update={'local_losses':(loss(.9,.048),)})))
    q=request(paths=(series,),boundary='prescribed_total_flow',total_volume_flow_m3_s=flow,inlet_minus_outlet_pressure_pa=None,
        branch_flow_brackets_m3_s=None)
    r=evaluate(q);assert r.status=='computed'
    friction=reference_path_friction(series,flow)
    assert r.paths[0].signed_friction_drop_pa==pytest.approx(friction,rel=3e-13,abs=1e-9)
    assert r.common_inlet_minus_outlet_pressure_pa==pytest.approx(friction+900*q.gravity_m_s2*.2)
    assert r.total_hydraulic_dissipation_W==pytest.approx(friction*flow,rel=3e-13,abs=1e-10)
    for e,row in zip(series.elements,r.paths[0].elements):
        assert row.velocity_m_s==pytest.approx(flow/(math.pi*e.diameter_m**2/4))
        assert row.actual_local_reference_velocities_m_s[0]==pytest.approx(flow/(math.pi*e.local_losses[0].velocity_reference_diameter_m**2/4))


@pytest.mark.parametrize('friction_drop',[-5000.,0.,5000.])
def test_parallel_pressure_boundary_matches_independent_laminar_column_fields(friction_drop):
    q=request(inlet_minus_outlet_pressure_pa=900*9.80665*.2+friction_drop)
    r=evaluate(q);assert r.status=='computed'
    expected=[friction_drop/independent_laminar_resistance(p) for p in q.paths]
    assert [p.volume_flow_m3_s for p in r.paths]==pytest.approx(expected,rel=3e-12,abs=1e-14)
    assert r.total_volume_flow_m3_s==pytest.approx(math.fsum(expected),rel=3e-12,abs=1e-14)
    assert r.total_mass_flow_kg_s==pytest.approx(900*math.fsum(expected),rel=3e-12,abs=1e-11)
    assert r.maximum_branch_total_pressure_residual_pa<1e-5
    assert r.total_hydraulic_dissipation_W==pytest.approx(friction_drop*math.fsum(expected),rel=3e-12,abs=1e-10)


@pytest.mark.parametrize('total',[-.0006,.0006])
def test_parallel_total_flow_inverse_uses_common_pressure_and_conserves_flow(total):
    q=request(boundary='prescribed_total_flow',total_volume_flow_m3_s=total,inlet_minus_outlet_pressure_pa=None,
        common_total_pressure_bracket_pa=(900*9.80665*.2-25000,900*9.80665*.2+25000))
    r=evaluate(q);assert r.status=='computed'
    conductance=sum(1/independent_laminar_resistance(p) for p in q.paths)
    expected_friction=total/conductance
    assert r.common_inlet_minus_outlet_total_pressure_pa==pytest.approx(900*9.80665*.2+expected_friction,rel=2e-12)
    assert r.continuity_residual_m3_s==pytest.approx(0.,abs=1e-12)
    assert sum(p.volume_flow_m3_s for p in r.paths)==pytest.approx(total,abs=1e-12)


@pytest.mark.parametrize('flow',[-.00017,.00017])
def test_static_pressure_keeps_independently_integrated_endpoint_kinetic_energy(flow):
    a,b=paths();series=Path(path_id='series',elements=a.elements+b.elements)
    ain,aout=math.pi*.04**2/4,math.pi*.061**2/4
    def kinetic_flux(area):
        radius=math.sqrt(area/math.pi)
        return quad(lambda r:.5*900*(2*flow/area*(1-(r/radius)**2))**3*2*math.pi*r,0,radius,epsabs=1e-12)[0]
    q=request(paths=(series,),boundary='prescribed_total_flow',total_volume_flow_m3_s=flow,inlet_minus_outlet_pressure_pa=None,
        branch_flow_brackets_m3_s=None,pressure_basis='static_with_endpoint_kinetic_state',
        inlet_endpoint_flow_area_m2=ain,outlet_endpoint_flow_area_m2=aout,
        inlet_endpoint_kinetic_energy_factor=2.,outlet_endpoint_kinetic_energy_factor=2.)
    r=evaluate(q);assert r.status=='computed'
    delta_kinetic_power=kinetic_flux(aout)-kinetic_flux(ain)
    assert r.endpoint_kinetic_pressure_change_pa*flow==pytest.approx(delta_kinetic_power,rel=3e-13)
    assert r.common_inlet_minus_outlet_pressure_pa*flow==pytest.approx(r.total_hydraulic_dissipation_W+900*q.gravity_m_s2*.2*flow+delta_kinetic_power,rel=3e-13)
    inverse=q.model_copy(update=dict(boundary='prescribed_pressure_difference',total_volume_flow_m3_s=None,
        inlet_minus_outlet_pressure_pa=r.common_inlet_minus_outlet_pressure_pa,branch_flow_brackets_m3_s=((-0.003,.003),)))
    assert evaluate(inverse).total_volume_flow_m3_s==pytest.approx(flow,rel=3e-10)


def test_parallel_static_pressure_inverse_uses_common_endpoint_area_not_branch_area():
    base=request();mechanical=evaluate(base);total=mechanical.total_volume_flow_m3_s
    ain,aout=.0007,.0038
    kinetic=900*((total/aout)**2-(total/ain)**2)
    q=request(pressure_basis='static_with_endpoint_kinetic_state',inlet_endpoint_flow_area_m2=ain,outlet_endpoint_flow_area_m2=aout,
        inlet_endpoint_kinetic_energy_factor=2.,outlet_endpoint_kinetic_energy_factor=2.,
        inlet_minus_outlet_pressure_pa=mechanical.common_inlet_minus_outlet_total_pressure_pa+kinetic,
        common_total_pressure_bracket_pa=(900*9.80665*.2+2000,900*9.80665*.2+12000))
    r=evaluate(q);assert r.status=='computed'
    assert r.total_volume_flow_m3_s==pytest.approx(total,rel=3e-10)
    assert r.endpoint_kinetic_pressure_change_pa==pytest.approx(kinetic,rel=3e-10)


def test_native_turbulent_colebrook_parallel_paths_match_independent_friction_roots():
    rho,mu=870.,.0012
    geometry=(Path(path_id='rough-a',elements=(Element(diameter_m=.071,length_m=411.,roughness_m=.000071),)),
              Path(path_id='rough-b',elements=(Element(diameter_m=.11,length_m=293.,roughness_m=.00011),)))
    def independent_pressure(e,flow):
        area=math.pi*e.diameter_m**2/4;velocity=flow/area;re=rho*velocity*e.diameter_m/mu
        f=brentq(lambda f:1/math.sqrt(f)+2*math.log10(e.roughness_m/e.diameter_m/3.7+2.51/(re*math.sqrt(f))),.005,.2,xtol=1e-15)
        return rho*f*e.length_m/e.diameter_m*velocity**2/2
    expected=[brentq(lambda flow:independent_pressure(p.elements[0],flow)-30000,.0007,.03,xtol=1e-14) for p in geometry]
    q=request(paths=geometry,density_kg_m3=rho,dynamic_viscosity_pa_s=mu,friction_method='Colebrook',
        inlet_minus_outlet_pressure_pa=rho*9.80665*.2+30000,branch_flow_brackets_m3_s=((.0007,.03),(.0007,.03)))
    r=evaluate(q);assert r.status=='computed'
    assert [p.volume_flow_m3_s for p in r.paths]==pytest.approx(expected,rel=3e-10)
    assert all(p.elements[0].reynolds>4000 for p in r.paths)


def test_nonlinear_local_loss_root_refinement_preserves_coarse_case():
    geometry=Path(path_id='nonlinear',elements=(Element(diameter_m=.04,length_m=71.,roughness_m=0.,local_losses=(loss(73.,.01),)),))
    truth=.00017371;target=reference_path_friction(geometry,truth)+900*9.80665*.2
    errors=[]
    for tolerance in (5e-6,1e-8,1e-12):
        q=request(paths=(geometry,),inlet_minus_outlet_pressure_pa=target,branch_flow_brackets_m3_s=((.000001,.001),),
            flow_root_absolute_tolerance_m3_s=tolerance,pressure_residual_absolute_tolerance_pa=10000.)
        r=evaluate(q);assert r.status=='computed';errors.append(abs(r.total_volume_flow_m3_s-truth))
    assert errors[-1]<1e-11 and errors[-1]<errors[0]/100


def test_unknown_state_reference_velocity_or_static_endpoint_state_is_not_defaulted():
    assert evaluate(request(density_kg_m3=None)).status=='needs_data'
    assert evaluate(request(pressure_basis='static_with_endpoint_kinetic_state')).status=='needs_data'
    p=Path(path_id='unknown-local',elements=(Element(diameter_m=.04,length_m=71.,roughness_m=0.,local_losses=(loss(velocity_reference_diameter_m=None),)),))
    assert evaluate(request(paths=(p,),branch_flow_brackets_m3_s=((-0.003,.003),))).status=='needs_data'


def test_transition_direction_zero_resistance_and_invalid_topology_guards():
    p=Path(path_id='transition',elements=(Element(diameter_m=.04,length_m=71.,roughness_m=0.),))
    flow=3000*math.pi*.04*.001/(4*1000)
    q=request(paths=(p,),density_kg_m3=1000.,dynamic_viscosity_pa_s=.001,boundary='prescribed_total_flow',
        total_volume_flow_m3_s=flow,inlet_minus_outlet_pressure_pa=None,branch_flow_brackets_m3_s=None)
    assert evaluate(q).status=='out_of_scope'
    assert evaluate(q.model_copy(update={'transition_policy':'use_declared_correlation'})).status=='computed'
    zero=Path(path_id='zero-resistance',elements=(Element(diameter_m=.04,length_m=0.,roughness_m=0.),))
    assert evaluate(request(paths=(zero,),branch_flow_brackets_m3_s=((-0.003,.003),))).status=='out_of_scope'
    assert evaluate(request(inlet_node_id='same',outlet_node_id='same')).status=='out_of_scope'
    directional=Path(path_id='directional',elements=(Element(diameter_m=.04,length_m=71.,roughness_m=0.,local_losses=(loss(applicable_flow_sign='positive'),)),))
    q=request(paths=(directional,),boundary='prescribed_total_flow',total_volume_flow_m3_s=-.0001,inlet_minus_outlet_pressure_pa=None,branch_flow_brackets_m3_s=None)
    assert evaluate(q).status=='out_of_scope'


def test_unbracketed_inconsistent_and_missing_boundary_are_explicit():
    assert evaluate(request(branch_flow_brackets_m3_s=None)).status=='needs_data'
    assert evaluate(request(inlet_minus_outlet_pressure_pa=None)).status=='needs_data'
    assert evaluate(request(branch_flow_brackets_m3_s=((.002,.003),(.002,.003)))).status=='out_of_scope'
    with pytest.raises(ValidationError):request(branch_flow_brackets_m3_s=((.003,.002),(.002,.003)))
    with pytest.raises(ValidationError):request(total_volume_flow_m3_s=.001)


def test_request_replay_immutability_and_forged_contract():
    q=request();saved=q.model_dump(mode='python')
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
    assert q.model_dump(mode='python')==saved
    with pytest.raises(ValidationError):evaluate(q.model_copy(update={'dynamic_viscosity_pa_s':-1.}))


@pytest.mark.parametrize('flow',[-.00017,.00017])
def test_series_pointwise_reference_length_includes_actual_local_losses(flow):
    a,b=paths(local=True);geometry=Path(path_id='series',elements=a.elements+b.elements)
    reference=ReferencePipe(diameter_m=.052,roughness_m=0.,reference_basis_id='actual-straight-reference-pipe')
    q=request(paths=(geometry,),boundary='prescribed_total_flow',total_volume_flow_m3_s=flow,inlet_minus_outlet_pressure_pa=None,
        branch_flow_brackets_m3_s=None,equivalent_reference_pipe=reference)
    r=evaluate(q);assert r.status=='computed'
    friction=reference_path_friction(geometry,flow)
    reference_gradient=flow/radial_flow_per_gradient(reference.diameter_m,.2)
    assert r.equivalent_reference_status=='computed_at_reported_total_flow'
    assert r.equivalent_reference_pipe_length_m==pytest.approx(friction/reference_gradient,rel=3e-13)
    assert r.pointwise_friction_secant_resistance_pa_s_m3==pytest.approx(abs(friction/flow),rel=3e-13)
    assert r.resistance_interpretation=='pointwise_at_reported_flow_not_frozen_global_curve'


@pytest.mark.parametrize('friction_drop',[-5000.,5000.])
def test_parallel_pointwise_equivalence_matches_independent_conductance(friction_drop):
    reference=ReferencePipe(diameter_m=.052,roughness_m=0.,reference_basis_id='actual-reference')
    q=request(inlet_minus_outlet_pressure_pa=900*9.80665*.2+friction_drop,equivalent_reference_pipe=reference)
    r=evaluate(q);assert r.status=='computed'
    expected_resistance=1/sum(1/independent_laminar_resistance(p) for p in q.paths)
    assert r.pointwise_friction_secant_resistance_pa_s_m3==pytest.approx(expected_resistance,rel=3e-11)
    assert r.equivalent_reference_pipe_length_m==pytest.approx(expected_resistance*radial_flow_per_gradient(reference.diameter_m,.2),rel=3e-11)


def test_zero_flow_equivalence_and_optional_reference_failure_do_not_invent_values():
    reference=ReferencePipe(diameter_m=.052,roughness_m=0.,reference_basis_id='actual-reference')
    q=request(paths=(paths()[0],),boundary='prescribed_total_flow',total_volume_flow_m3_s=0.,inlet_minus_outlet_pressure_pa=None,
        branch_flow_brackets_m3_s=None,equivalent_reference_pipe=reference)
    zero=evaluate(q);assert zero.status=='computed'
    assert zero.equivalent_reference_status=='undefined_zero_flow'
    assert zero.pointwise_friction_secant_resistance_pa_s_m3 is None and zero.equivalent_reference_pipe_length_m is None
    bad=ReferencePipe(diameter_m=.04,roughness_m=.008,reference_basis_id='unsupported-reference')
    r=evaluate(q.model_copy(update={'total_volume_flow_m3_s':.0001,'equivalent_reference_pipe':bad}))
    assert r.status=='computed' and r.equivalent_reference_status=='out_of_scope'
    assert r.equivalent_reference_pipe_length_m is None
    assert evaluate(q.model_copy(update={'total_volume_flow_m3_s':1e-200})).status=='out_of_scope'
