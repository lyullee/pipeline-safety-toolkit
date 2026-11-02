"""Bolt preload and nominal stress balances through the fabrication owner adapter."""
import runpy
import pytest
from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.fabrication_exchange import evaluate_fabrication
REQUESTS,CTX=runpy.run_path('examples/fabrication_methods.py')['make_cases']()
BASE=REQUESTS['fabrication.bolt_preload']

def solve(torque,diameter,nut_factor,area):
 request=BASE.model_copy(update={'parameters':{'torque_N_m':torque,'nominal_diameter_m':diameter,'nut_factor':nut_factor,'tensile_area_m2':area}})
 result=evaluate_fabrication(request,CTX,client=MaterialClient())
 assert result.status=='computed'
 return result.values

@pytest.mark.parametrize('torque,diameter,K,area',[(50.,.01,.2,5e-5),(160.,.02,.16,2.3e-4),(400.,.03,.24,5e-4)])
def test_torque_preload_and_nominal_stress_match_force_balance(torque,diameter,K,area):
 values=solve(torque,diameter,K,area)
 force=torque/(K*diameter)
 assert values['preload_N']==pytest.approx(force,rel=2e-14)
 assert values['nominal_tensile_stress_Pa']==pytest.approx(force/area,rel=2e-14)

def test_bolt_preload_inverse_factor_and_diameter_similarity():
 base=solve(100.,.02,.2,2e-4)['preload_N']
 assert solve(200.,.02,.2,2e-4)['preload_N']==pytest.approx(2*base,rel=2e-14)
 assert solve(100.,.04,.2,2e-4)['preload_N']==pytest.approx(.5*base,rel=2e-14)
 assert solve(100.,.02,.4,2e-4)['preload_N']==pytest.approx(.5*base,rel=2e-14)
