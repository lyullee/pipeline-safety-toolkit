"""Euler torque/power reference for a reviewed polytropic-head map point."""
import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.compressor_speed_head import CompressorStageHeadReference as Stage, CompressorSpeedHeadRequest as Request, evaluate_compressor_speed_head as evaluate

REFERENCE_RPM=7139.


def euler_polytropic_head(diameter,rpm,inlet_swirl,outlet_swirl,efficiency,mass_rate=2.3):
    omega=rpm*2*math.pi/60;r2=diameter/2;r1=.4*r2
    # Tangential angular-momentum flux gives torque. Its shaft-rate power,
    # with a supplied constant head-efficiency basis, gives the reference head.
    c1=inlet_swirl*omega*r1;c2=outlet_swirl*omega*r2
    torque=mass_rate*(r2*c2-r1*c1)
    fluid_power=torque*omega
    return efficiency*fluid_power/mass_rate,torque,fluid_power


def stages(phase='gas'):
    result=[]
    for i,(diameter,swirl_in,swirl_out,efficiency) in enumerate(((.59,.11,.73,.83),(.47,.07,.79,.79))):
        head=euler_polytropic_head(diameter,REFERENCE_RPM,swirl_in,swirl_out,efficiency)[0]
        result.append(Stage(stage_id=f'stage-{i}',nominal_polytropic_specific_head_j_per_kg=head,
            nominal_head_basis='polytropic_specific_head',impeller_diameter_m=diameter,
            reference_inlet_sound_speed_m_s=519.,target_inlet_sound_speed_m_s=507.,
            reference_inlet_phase=phase,target_inlet_phase=phase,reviewed_tip_mach_support=(.05,.9),
            map_and_inlet_state_basis_id=f'reviewed-gas-map-stage-{i}',
            fixed_geometry_reduced_flow_reynolds_and_head_efficiency_support_reviewed=True))
    return tuple(result)


def request(**changes):
    values=dict(stages=stages(),mode='speed_to_head',reference_shaft_speed_rpm=REFERENCE_RPM,target_shaft_speed_rpm=REFERENCE_RPM*1.14)
    values.update(changes);return Request(**values)


@pytest.mark.parametrize('ratio',[.63,1.,1.14])
@pytest.mark.parametrize('phase',['gas','supercritical_gas','supercritical'])
def test_independent_euler_angular_momentum_and_power_matches_head_and_speed_inverse(ratio,phase):
    target=REFERENCE_RPM*ratio;q=request(stages=stages(phase),target_shaft_speed_rpm=target)
    expected=[]
    for diameter,si,so,eff in ((.59,.11,.73,.83),(.47,.07,.79,.79)):
        head,torque,power=euler_polytropic_head(diameter,target,si,so,eff,mass_rate=2.3*ratio)
        assert power==pytest.approx(torque*target*2*math.pi/60)
        expected.append(head)
    forward=evaluate(q);assert forward.status=='computed'
    assert [s.target_polytropic_specific_head_j_per_kg for s in forward.stages]==pytest.approx(expected,rel=3e-14)
    assert forward.total_polytropic_specific_head_j_per_kg==pytest.approx(math.fsum(expected),rel=3e-14)
    inverse=evaluate(request(stages=q.stages,mode='required_head_to_speed',target_shaft_speed_rpm=None,
        required_total_polytropic_specific_head_j_per_kg=math.fsum(expected)))
    assert inverse.status=='computed' and inverse.target_shaft_speed_rpm==pytest.approx(target,rel=3e-14)
    assert abs(inverse.head_inverse_residual_j_per_kg)<1e-9
    assert not inverse.actual_flow_shaft_power_pressure_ratio_or_efficiency_inferred
    assert not inverse.surge_choke_rotor_limit_or_field_performance_qualified


@pytest.mark.parametrize('count',[1,3,7])
def test_stage_count_sum_and_specific_energy_unit_conversion(count):
    # Convert an authored ft*lbf/lbm head using physical force/mass units;
    # this is neither a pump head in metres nor an unconverted torque value.
    metres_per_foot=.3048;newtons_per_pound_force=4.4482216152605;kg_per_pound=.45359237
    specific_energy=7311.*metres_per_foot*newtons_per_pound_force/kg_per_pound
    model=tuple(stages()[0].model_copy(update=dict(stage_id=f'equal-{i}',nominal_polytropic_specific_head_j_per_kg=specific_energy)) for i in range(count))
    result=evaluate(request(stages=model,target_shaft_speed_rpm=REFERENCE_RPM))
    assert result.total_polytropic_specific_head_j_per_kg==pytest.approx(count*specific_energy,rel=2e-15)


@pytest.mark.parametrize('basis',['isentropic_specific_head','fluid_specific_work'])
def test_different_head_kinds_are_not_added_as_polytropic_heads(basis):
    s=list(stages());s[1]=s[1].model_copy(update={'nominal_head_basis':basis})
    assert evaluate(request(stages=tuple(s))).status=='out_of_scope'


@pytest.mark.parametrize('field',['nominal_polytropic_specific_head_j_per_kg','impeller_diameter_m',
    'reference_inlet_sound_speed_m_s','target_inlet_sound_speed_m_s','reviewed_tip_mach_support','map_and_inlet_state_basis_id'])
def test_unknown_stage_map_or_gas_state_is_not_filled_from_air_or_frame_table(field):
    s=list(stages());s[0]=s[0].model_copy(update={field:None})
    assert evaluate(request(stages=tuple(s))).status=='needs_data'


@pytest.mark.parametrize('changes',[dict(reference_inlet_phase='unknown'),dict(target_inlet_phase='unknown'),dict(nominal_head_basis='unknown'),
    dict(fixed_geometry_reduced_flow_reynolds_and_head_efficiency_support_reviewed=False)])
def test_unknown_phase_head_kind_or_applicability_is_preserved(changes):
    s=list(stages());s[0]=s[0].model_copy(update=changes)
    assert evaluate(request(stages=tuple(s))).status=='needs_data'


@pytest.mark.parametrize('phase',['liquid','two_phase'])
def test_liquid_pump_or_two_phase_map_is_not_gas_similarity(phase):
    s=list(stages());s[0]=s[0].model_copy(update={'target_inlet_phase':phase})
    assert evaluate(request(stages=tuple(s))).status=='out_of_scope'


def test_mach_support_bounds_and_duplicate_stage_identity():
    assert evaluate(request(target_shaft_speed_rpm=30000.)).status=='out_of_scope'
    s=list(stages());s[0]=s[0].model_copy(update={'reviewed_tip_mach_support':(.6,.9)})
    assert evaluate(request(stages=tuple(s))).status=='out_of_scope'
    assert evaluate(request(stages=(stages()[0],stages()[0]))).status=='out_of_scope'


def test_missing_selected_coordinate_reference_speed_and_finite_arithmetic():
    assert evaluate(request(target_shaft_speed_rpm=None)).status=='needs_data'
    assert evaluate(request(reference_shaft_speed_rpm=None)).status=='needs_data'
    assert evaluate(request(mode='required_head_to_speed',target_shaft_speed_rpm=None)).status=='needs_data'
    assert evaluate(request(target_shaft_speed_rpm=1e308)).status=='out_of_scope'


def test_invalid_support_boundary_and_replay():
    with pytest.raises(ValidationError):Stage.model_validate(stages()[0].model_dump() | {'reviewed_tip_mach_support':(.9,.1)})
    with pytest.raises(ValidationError):request(reference_shaft_speed_rpm=0.)
    with pytest.raises(ValidationError):request(required_total_polytropic_specific_head_j_per_kg=1.)
    q=request();saved=q.model_dump(mode='python')
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
    assert q.model_dump(mode='python')==saved
