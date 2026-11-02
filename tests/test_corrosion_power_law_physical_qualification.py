"""Empirical power-law penetration identity and scaling through owner bridge."""
import math,runpy
import pytest
from pipeline_integrity.corrosion_wear import evaluate_corrosion_wear
CASES,CTX=runpy.run_path('examples/corrosion_wear.py')['make_cases']()
BASE=CASES['corrosion.power_law']

def calc(c,n,t,tr):
 r=BASE.model_copy(update={'parameters':{'coefficient_m':c,'exponent':n,'exposure_s':t,'reference_time_s':tr}})
 return evaluate_corrosion_wear(r,CTX)

@pytest.mark.parametrize('c,n,t,tr',[(2e-4,.5,400.,100.),(7e-5,.8,729.,81.),(3e-3,1.2,64.,16.)])
def test_penetration_matches_power_law(c,n,t,tr):
 r=calc(c,n,t,tr)
 assert r['status']=='computed'
 assert r['values']['penetration_m']==pytest.approx(c*(t/tr)**n,rel=2e-14)

def test_reference_time_and_zero_exposure_boundaries():
 c=2e-4;n=.5;tr=100.
 assert calc(c,n,tr,tr)['values']['penetration_m']==pytest.approx(c,rel=2e-14)
 assert calc(c,n,0.,tr)['values']['penetration_m']==pytest.approx(0.,abs=0.)

def test_penetration_exposure_similarity_and_support_guard():
 c=3e-4;n=.5;tr=100.
 a=calc(c,n,100.,tr)['values']['penetration_m']
 b=calc(c,n,400.,tr)['values']['penetration_m']
 assert b==pytest.approx(2*a,rel=2e-14)
 rejected=calc(c,n,1001.,tr)
 assert rejected['status']=='out_of_scope' and rejected['native_receipt'] is None
