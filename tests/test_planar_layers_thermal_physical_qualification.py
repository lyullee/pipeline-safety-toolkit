"""Bounded planar conduction via owner exchange API and series-resistance oracle."""
import math
import pytest
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job

CTX=RunContext(asset_id='planar-wall',scenario_id='steady-1d',clock_id='snapshot',origin='synthetic',
 evidence=(EvidenceRef(id='wall-input',citation='Authored synthetic layer dimensions and boundary temperatures',origin='synthetic',license_id='MIT'),))

def evaluate(thickness,conductivity,area,delta_t):
 job=prepare_material_job('thermal.planar_layers',{'thickness_m':list(thickness),
     'conductivity_W_m_K':list(conductivity),'area_m2':area,'temperature_difference_K':delta_t},CTX,
     location_id='uniform-planar-wall',evidence_ids=('wall-input',))
 return evaluate_material_job(job,CTX)

@pytest.mark.parametrize('thickness,k,area,delta_t',[
 ((.02,.08,.01),(15.,.8,45.),2.,95.),
 ((.15,.003,.04),(1.2,0.04,12.),.75,60.),
 ((.05,.12),(0.25,8.),4.5,-35.),
])
def test_planar_layers_match_series_resistance_and_interface_energy_closure(thickness,k,area,delta_t):
 receipt=evaluate(thickness,k,area,delta_t)
 values=receipt.native.payload['values']
 layer_r=[t/(conductivity*area) for t,conductivity in zip(thickness,k,strict=True)]
 expected_r=math.fsum(layer_r)
 expected_q=delta_t/expected_r
 assert receipt.native.status=='computed'
 assert values['thermal_resistance_K_W']==pytest.approx(expected_r,rel=2e-14)
 assert values['heat_rate_W']==pytest.approx(expected_q,rel=2e-14,abs=1e-14)
 # Reconstructed interface temperatures close at a common heat rate across all layers.
 interface_drops=[values['heat_rate_W']*r for r in layer_r]
 assert all(drop/r==pytest.approx(values['heat_rate_W'],rel=2e-14,abs=1e-14) for drop,r in zip(interface_drops,layer_r,strict=True))
 assert math.fsum(interface_drops)==pytest.approx(delta_t,rel=2e-14,abs=1e-14)


def test_planar_layer_split_merge_and_area_scaling_invariances():
 base=evaluate((.15,.03),(.8,30.),1.2,80.).native.payload['values']
 split=evaluate((.05,.10,.03),(.8,.8,30.),1.2,80.).native.payload['values']
 doubled_area=evaluate((.15,.03),(.8,30.),2.4,80.).native.payload['values']
 assert split['thermal_resistance_K_W']==pytest.approx(base['thermal_resistance_K_W'],rel=2e-14)
 assert split['heat_rate_W']==pytest.approx(base['heat_rate_W'],rel=2e-14)
 assert doubled_area['thermal_resistance_K_W']==pytest.approx(base['thermal_resistance_K_W']/2,rel=2e-14)
 assert doubled_area['heat_rate_W']==pytest.approx(base['heat_rate_W']*2,rel=2e-14)
