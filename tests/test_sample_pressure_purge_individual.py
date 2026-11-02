"""Independent species-inventory ODE and stage mass/mole conservation."""
import math
from decimal import Decimal,localcontext
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.sample_pressure_purge import (
    PressurePurgeStage as Stage,SamplePressurePurgeRequest as Request,evaluate_sample_pressure_purge)


R=8.31446261815324
def request(**changes):
    args=dict(cylinder_volume_m3=.002,common_temperature_K=300.,initial_pressure_Pa_abs=1e5,
        original_gas_molar_mass_kg_mol=.028,feed_gas_molar_mass_kg_mol=.044,
        stages=(Stage(fill_pressure_Pa_abs=1e6,vent_pressure_after_Pa_abs=1e5),
                Stage(fill_pressure_Pa_abs=8e5,vent_pressure_after_Pa_abs=1.2e5),
                Stage(fill_pressure_Pa_abs=6e5)),
        target_original_gas_mole_fraction=.004,clock_id='sample-clock',property_basis_id='reviewed-ideal-endpoints',
        ideal_fixed_temperature_volume_perfect_mixing_nonselective_vent_and_original_free_feed_reviewed=True)
    return Request(**(args|changes))


@pytest.mark.parametrize('volume,temp',[(.002,300.),(.1,350.),(.00001,280.)])
def test_stage_process_from_independent_species_mass_ODE(volume,temp):
    q=request(cylinder_volume_m3=volume,common_temperature_K=temp);out=evaluate_sample_pressure_purge(q)
    old=q.initial_pressure_Pa_abs*volume/(R*temp);fresh=0.;pressure=q.initial_pressure_Pa_abs
    for point,stage in zip(out.points[1:],q.stages):
        # Integrate inventories versus added/removed mole amount, with no
        # pressure-ratio product or production stage update in the reference.
        incoming=(stage.fill_pressure_Pa_abs-pressure)*volume/(R*temp)
        filled=solve_ivp(lambda amount,y:[0.,1.],(0,incoming),[old,fresh],rtol=1e-12,atol=1e-14)
        old,fresh=filled.y[:,-1]
        if stage.vent_pressure_after_Pa_abs is not None:
            amount_removed=(stage.fill_pressure_Pa_abs-stage.vent_pressure_after_Pa_abs)*volume/(R*temp)
            vent=solve_ivp(lambda amount,y:[-y[0]/sum(y),-y[1]/sum(y)],(0,amount_removed),[old,fresh],rtol=1e-12,atol=1e-14,max_step=amount_removed/50)
            assert vent.success;old,fresh=vent.y[:,-1];pressure=stage.vent_pressure_after_Pa_abs
        else:pressure=stage.fill_pressure_Pa_abs
        assert point.original_gas_moles_mol==pytest.approx(old,rel=2e-10,abs=1e-14)
        assert point.feed_gas_moles_mol==pytest.approx(fresh,rel=2e-10,abs=1e-14)
        assert point.mass_kg==pytest.approx(old*.028+fresh*.044,rel=2e-10)
        assert point.total_moles_mol==pytest.approx(pressure*volume/(R*temp),rel=3e-14)
        assert abs(point.mass_budget_residual_kg)<1e-12
        assert abs(point.original_mole_budget_residual_mol)<1e-12
        assert abs(point.feed_mole_budget_residual_mol)<1e-12


def test_independent_decimal_pressure_product_and_retained_final_fill_target():
    q=request();a=evaluate_sample_pressure_purge(q)
    with localcontext() as c:
        c.prec=60;x=Decimal(1);pressure=Decimal('100000')
        expected=[]
        for s in q.stages:
            x*=pressure/Decimal(str(s.fill_pressure_Pa_abs));expected.append(float(x))
            pressure=Decimal(str(s.vent_pressure_after_Pa_abs or s.fill_pressure_Pa_abs))
    assert [p.original_gas_mole_fraction for p in a.points[1:]]==pytest.approx(expected,rel=2e-14)
    assert a.first_target_met_after_fill_number==3 and a.final_target_met is True
    assert a.points[-1].phase=='filled_and_retained' and a.points[-1].pressure_Pa_abs==6e5


def test_venting_alone_changes_inventory_not_composition():
    filled=evaluate_sample_pressure_purge(request(stages=(Stage(fill_pressure_Pa_abs=1e6),)))
    vented=evaluate_sample_pressure_purge(request(stages=(Stage(fill_pressure_Pa_abs=1e6,vent_pressure_after_Pa_abs=1e5),)))
    a,b=filled.points[-1],vented.points[-1]
    assert a.original_gas_mole_fraction==pytest.approx(b.original_gas_mole_fraction)
    assert b.mass_kg==pytest.approx(a.mass_kg/10)
    assert b.original_gas_moles_mol==pytest.approx(a.original_gas_moles_mol/10)


@pytest.mark.parametrize('scale',[.01,100.])
def test_cylinder_volume_extensivity_preserves_fraction_and_first_cycle(scale):
    a=evaluate_sample_pressure_purge(request());b=evaluate_sample_pressure_purge(request(cylinder_volume_m3=.002*scale))
    assert a.first_target_met_after_fill_number==b.first_target_met_after_fill_number
    for x,y in zip(a.points,b.points):
        assert y.mass_kg==pytest.approx(x.mass_kg*scale)
        assert y.original_gas_mole_fraction==pytest.approx(x.original_gas_mole_fraction)


def test_zero_target_remains_unattainable_despite_fraction_underflow():
    q=request(stages=tuple(Stage(fill_pressure_Pa_abs=1e7,vent_pressure_after_Pa_abs=1e5) for _ in range(200)),target_original_gas_mole_fraction=0.)
    a=evaluate_sample_pressure_purge(q)
    assert a.points[-1].original_gas_mole_fraction==0. # Below finite float range.
    assert a.points[-1].original_fraction_underflowed is True
    assert a.points[-1].log_original_gas_mole_fraction==pytest.approx(200*math.log(.01))
    assert a.final_target_met is False and a.first_target_met_after_fill_number is None


@pytest.mark.parametrize('fraction,target,met',[(0.,0.,True),(.1,.1,True),(.2,.1,False)])
def test_no_stages_and_initial_target_event(fraction,target,met):
    a=evaluate_sample_pressure_purge(request(stages=(),initial_original_gas_mole_fraction=fraction,target_original_gas_mole_fraction=target))
    assert len(a.points)==1 and a.final_target_met is met
    assert a.first_target_met_after_fill_number==(0 if met else None)


def test_no_pressure_change_and_unrequested_target():
    a=evaluate_sample_pressure_purge(request(stages=(Stage(fill_pressure_Pa_abs=1e5,vent_pressure_after_Pa_abs=1e5),),target_original_gas_mole_fraction=None))
    assert a.final_target_met is None
    assert a.points[1].original_gas_mole_fraction==1 and a.points[1].added_feed_moles_mol==0


def test_same_chemistry_molar_mass_does_not_erase_original_provenance():
    a=evaluate_sample_pressure_purge(request(feed_gas_molar_mass_kg_mol=.028))
    assert a.points[-1].original_gas_mole_fraction<1
    assert a.points[-1].mass_kg==pytest.approx(a.points[-1].total_moles_mol*.028)


def test_unreviewed_nonideal_or_selective_process_is_withheld():
    a=evaluate_sample_pressure_purge(request(ideal_fixed_temperature_volume_perfect_mixing_nonselective_vent_and_original_free_feed_reviewed=False))
    assert a.status=='needs_data' and not a.points


@pytest.mark.parametrize('changes',[{'initial_pressure_Pa_abs':0},{'common_temperature_K':0},
    {'initial_original_gas_mole_fraction':1.1},{'feed_gas_molar_mass_kg_mol':None},
    {'stages':(Stage(fill_pressure_Pa_abs=8e4),)},
    {'stages':(Stage(fill_pressure_Pa_abs=1e6,vent_pressure_after_Pa_abs=2e6),)},
    {'stages':(Stage(fill_pressure_Pa_abs=1e6),Stage(fill_pressure_Pa_abs=9e5))}])
def test_bad_absolute_states_missing_molar_basis_and_reversed_process_rejected(changes):
    with pytest.raises(ValueError):evaluate_sample_pressure_purge(request(**changes))
