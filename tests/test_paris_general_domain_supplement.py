"""Independent Paris-region special integrals, geometry refinement and limits."""
import math
import os
from pathlib import Path
import runpy

import matintegrity
import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.fracture_exchange import assess_crack_growth

OLD=runpy.run_path('tests/test_paris_constant_amplitude_physical_qualification.py')
A0=.001
Y=1.2
LOW=10e6
HIGH=30e6
RATE0=1e-9


def make(exponent,blocks=((LOW,HIGH,2e5),),terminal=.004):
    q,c=OLD['make'](blocks,terminal)
    coefficient=RATE0/(Y*(HIGH-LOW)*math.sqrt(math.pi*A0))**exponent
    q=q.model_copy(update={'calibration':q.calibration.model_copy(update={
        'coefficient_m_cycle_per_pa_sqrt_m_power':coefficient,'exponent':exponent}),
        'relative_integration_tolerance':1e-8,'absolute_depth_tolerance_m':1e-13})
    return q,c,coefficient


def values(q,c):
    r=assess_crack_growth(q,c)
    assert r['status']=='computed',r
    return r['native']['native']['payload']['values']


def depth_after(initial,exponent,coefficient,stress_range,cycles):
    lam=coefficient*(Y*stress_range*math.sqrt(math.pi))**exponent
    power=1-exponent/2
    return initial*math.exp(lam*cycles) if power==0 else (initial**power+power*lam*cycles)**(1/power)


def cycles_between(initial,final,exponent,coefficient,stress_range):
    lam=coefficient*(Y*stress_range*math.sqrt(math.pi))**exponent
    power=1-exponent/2
    return math.log(final/initial)/lam if power==0 else (final**power-initial**power)/(power*lam)


def test_selected_immutable_owner_path_is_actual_import():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('exponent',[1.,1.5,2.,3.,4.])
def test_paris_general_exponents_match_separated_depth_integral(exponent):
    q,c,coefficient=make(exponent);v=values(q,c)
    expected=depth_after(A0,exponent,coefficient,HIGH-LOW,2e5)
    assert v['final_depth_m']==pytest.approx(expected,rel=2e-8)
    assert v['cycles_evaluated']==2e5
    assert v['termination_reason']=='requested_blocks_completed'


def test_paris_variable_amplitude_blocks_match_independent_stage_cycle_coordinates():
    blocks=((10e6,30e6,7e4),(5e6,30e6,6e4),(20e6,40e6,8e4))
    q,c,coefficient=make(3.,blocks);v=values(q,c);depth=A0
    for row,(lo,hi,n) in zip(v['block_results'],blocks,strict=True):
        expected=depth_after(depth,3.,coefficient,hi-lo,n)
        assert row['initial_depth_m']==pytest.approx(depth,rel=2e-8)
        assert row['final_depth_m']==pytest.approx(expected,rel=2e-8)
        assert cycles_between(depth,row['final_depth_m'],3.,coefficient,hi-lo)==pytest.approx(n,rel=2e-8)
        depth=expected
    assert v['cycles_evaluated']==sum(b[2] for b in blocks)
    assert v['final_depth_m']==pytest.approx(depth,rel=2e-8)


def test_paris_supplied_geometry_partition_converges_to_independent_depth_ode():
    exponent=3.;q,c,_=make(exponent);coefficient=RATE0/((1+2e5*A0*A0)*(HIGH-LOW)*math.sqrt(math.pi*A0))**exponent
    def derivative(n,state):
        a=float(state[0]);return [coefficient*((1+2e5*a*a)*(HIGH-LOW)*math.sqrt(math.pi*a))**exponent]
    ref=solve_ivp(derivative,(0.,2e5),[A0],method='DOP853',rtol=2e-12,atol=1e-14)
    assert ref.success
    errors=[]
    for cells in (8,16,32,64):
        knots=np.linspace(.0005,.004,cells+1)
        qq=q.model_copy(update={'geometry_depth_m':tuple(map(float,knots)),
            'geometry_factor':tuple(float(1+2e5*a*a) for a in knots),
            'calibration':q.calibration.model_copy(update={'coefficient_m_cycle_per_pa_sqrt_m_power':coefficient})})
        v=values(qq,c);errors.append(abs(v['final_depth_m']-ref.y[0,-1]))
    assert all(a>b>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1]<2e-7


def test_paris_declared_terminal_depth_event_and_cycle_ledger():
    terminal=.0012;q,c,coefficient=make(3.,((LOW,HIGH,1e6),),terminal)
    v=values(q,c);expected=cycles_between(A0,terminal,3.,coefficient,HIGH-LOW)
    assert v['termination_reason']=='declared_depth_limit'
    assert v['final_depth_m']==pytest.approx(terminal,abs=1e-13)
    assert v['cycles_evaluated']==pytest.approx(expected,rel=2e-8)
    assert sum(b['cycles_used'] for b in v['block_results'])==pytest.approx(expected,rel=2e-8)
    assert v['cycles_evaluated']<v['requested_cycles']


def test_paris_provider_defined_toughness_limit_is_distinct_from_depth_limit():
    failure=.0015;q,c,coefficient=make(3.,((LOW,HIGH,1e6),))
    kc=Y*HIGH*math.sqrt(math.pi*failure)
    q=q.model_copy(update={'resistance':q.resistance.model_copy(update={'value_pa_sqrt_m':kc})})
    v=values(q,c);expected=cycles_between(A0,failure,3.,coefficient,HIGH-LOW)
    assert v['termination_reason']=='toughness_limit_reached'
    assert v['final_depth_m']==pytest.approx(failure,abs=2e-12)
    assert v['cycles_evaluated']==pytest.approx(expected,rel=2e-8)
    assert v['final_depth_m']<q.terminal_depth_m
