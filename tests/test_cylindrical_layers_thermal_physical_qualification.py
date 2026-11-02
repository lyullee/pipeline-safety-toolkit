"""Cylindrical multilayer wall exchange vs exact radial resistance network."""
import math
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.material_exchange import evaluate_material_job,prepare_material_job
CTX=RunContext(asset_id='cylindrical-wall',scenario_id='steady-radial',clock_id='snapshot',origin='synthetic',evidence=(EvidenceRef(id='wall',citation='Authored synthetic dimensions and boundary conditions',origin='synthetic',license_id='MIT'),))

def calc(radii,k,length,fluid,ambient,hi,ho):
 p={'radii_m':list(radii),'conductivity_W_m_K':list(k),'length_m':length,'inner_fluid_temperature_K':fluid,'ambient_temperature_K':ambient,'radiative_environment_temperature_K':ambient,'inner_convection_W_m2_K':hi,'outer_convection_W_m2_K':ho,'outer_emissivity':0.0}
 job=prepare_material_job('thermal.cylindrical_layers',p,CTX,location_id='uniform-cylinder',evidence_ids=('wall',))
 return evaluate_material_job(job,CTX).native.payload

@pytest.mark.parametrize('radii,k,length,fluid,ambient,hi,ho',[
 ((.03,.04,.11),(.16,12.),1.7,500.,300.,25.,18.),
 ((.02,.025,.08,.12),(40.,.045,8.),4.0,260.,320.,70.,12.),
 ((.1,.13),(.8,),.6,700.,310.,100.,5.),
])
def test_cylindrical_layers_match_series_radial_resistance_and_energy_balance(radii,k,length,fluid,ambient,hi,ho):
 result=calc(radii,k,length,fluid,ambient,hi,ho)
 assert result['status']=='computed'
 v=result['values']; area_i=2*math.pi*radii[0]*length; area_o=2*math.pi*radii[-1]*length
 layer_r=[math.log(b/a)/(2*math.pi*length*ki) for a,b,ki in zip(radii[:-1],radii[1:],k,strict=True)]
 r_inner=1/(hi*area_i);r_outer=1/(ho*area_o);total=r_inner+math.fsum(layer_r)+r_outer
 q=(fluid-ambient)/total
 assert v['heat_rate_outward_W']==pytest.approx(q,rel=2e-12,abs=1e-12)
 expected=[fluid-q*r_inner]
 for resistance in layer_r:expected.append(expected[-1]-q*resistance)
 assert v['interface_temperatures_K']==pytest.approx(expected,rel=2e-12,abs=2e-10)
 assert v['outer_balance_residual_W']==pytest.approx(0.,abs=2e-9)
 assert expected[-1]==pytest.approx(ambient+q*r_outer,rel=2e-12,abs=2e-10)


def test_cylindrical_layer_split_and_length_scaling():
 base=calc((.025,.06,.12),(.8,12.),2.,480.,300.,40.,15.)['values']
 split=calc((.025,.04,.06,.12),(.8,.8,12.),2.,480.,300.,40.,15.)['values']
 doubled=calc((.025,.06,.12),(.8,12.),4.,480.,300.,40.,15.)['values']
 assert split['heat_rate_outward_W']==pytest.approx(base['heat_rate_outward_W'],rel=2e-12)
 assert split['interface_temperatures_K'][0]==pytest.approx(base['interface_temperatures_K'][0],rel=2e-12)
 assert doubled['heat_rate_outward_W']==pytest.approx(2*base['heat_rate_outward_W'],rel=2e-12)

