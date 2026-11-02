"""Exact m=2 constant-amplitude Paris growth and cycle partition invariance."""
import math,runpy
import pytest
from pipeline_integrity.fracture_exchange import assess_crack_growth

GEOMETRY_FACTOR=1.2
C=1e-22
A0=1e-3
LOW=10e6
HIGH=30e6

def make(blocks,terminal=4e-3):
    req=runpy.run_path('examples/fracture_screen.py')['make_growth']()
    calibration=req.calibration.model_copy(update={
        'coefficient_m_cycle_per_pa_sqrt_m_power':C,'exponent':2.0,
    })
    adapted=tuple(req.blocks[0].model_copy(update={
        'id':f'b{i}','minimum_opening_stress_pa':lo,
        'maximum_opening_stress_pa':hi,'cycles':n,
    }) for i,(lo,hi,n) in enumerate(blocks))
    req=req.model_copy(update={'initial_depth_m':A0,'terminal_depth_m':terminal,
        'geometry_factor':(GEOMETRY_FACTOR,GEOMETRY_FACTOR),'blocks':adapted,
        'calibration':calibration})
    ctx=runpy.run_path('examples/fracture_screen.py')['context']()
    return req,ctx

def run(blocks):
    req,ctx=make(blocks)
    rec=assess_crack_growth(req,ctx)
    assert rec['status']=='computed'
    return rec['native']['native']['payload']['values']

def exact(n):
    coefficient=C*(GEOMETRY_FACTOR*(HIGH-LOW)*math.sqrt(math.pi))**2
    return A0*math.exp(coefficient*n)

def test_m2_paris_crack_depth_matches_exact_exponential_solution():
    n=200000.
    values=run([(LOW,HIGH,n)])
    assert values['final_depth_m']==pytest.approx(exact(n),rel=2e-6)
    assert values['cycles_evaluated']==pytest.approx(n)
    assert values['termination_reason']=='requested_blocks_completed'

def test_same_stress_range_cycle_partition_preserves_exact_growth():
    one=run([(LOW,HIGH,200000.)])
    split=run([(LOW,HIGH,80000.),(LOW,HIGH,120000.)])
    assert one['final_depth_m']==pytest.approx(split['final_depth_m'],rel=2e-6)
    assert one['final_depth_m']==pytest.approx(exact(200000.),rel=2e-6)
