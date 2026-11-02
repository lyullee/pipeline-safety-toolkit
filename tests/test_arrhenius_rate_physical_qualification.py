"""Arrhenius temperature-rate law and similarity checks through the owner adapter."""
import math
import runpy
import pytest
from pipeline_integrity.creep_exchange import evaluate_creep
R=8.31446261815324
CASES,_,CTX=runpy.run_path('examples/creep_methods.py')['make_cases']()
BASE=CASES['kinetics.arrhenius']

def evaluate(rate,Tref,T,E):
 p={'reference_rate':rate,'reference_temperature_K':Tref,'temperature_K':T,'activation_energy_J_mol':E}
 request=BASE.model_copy(update={'parameters':p,'evaluation_temperature_k':T})
 return evaluate_creep(request,CTX)

@pytest.mark.parametrize('rate,Tref,T,E',[(1e-9,900.,1000.,100000.),(3e-8,950.,850.,55000.),(2e-12,1000.,1050.,0.)])
def test_arrhenius_rate_matches_closed_form(rate,Tref,T,E):
 result=evaluate(rate,Tref,T,E)
 factor=math.exp(-E/R*(1/T-1/Tref))
 assert result['status']=='computed'
 assert result['values']['temperature_factor']==pytest.approx(factor,rel=2e-14)
 assert result['values']['rate']==pytest.approx(rate*factor,rel=2e-14)

def test_reference_temperature_identity_and_rate_scaling():
 a=evaluate(2e-9,900.,900.,80000.)
 b=evaluate(8e-9,900.,900.,80000.)
 assert a['values']['temperature_factor']==pytest.approx(1.0,abs=1e-15)
 assert a['values']['rate']==pytest.approx(2e-9,rel=1e-14)
 assert b['values']['rate']==pytest.approx(4*a['values']['rate'],rel=1e-14)
