"""Selected steady topology laws against independently solved laminar resistors.

Near-reference nitrogen has a pressure span below3e-5 of absolute pressure.
The linear-resistance reference is the bounded constant-property limit of the
actual EOS/isenthalpic Darcy links. This is not a historical delivery-fit match.
"""
import math
import numpy as np
import pytest
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.gas_network import GasBoundary,GasJunction,GasPipe,GasNetwork,solve_gas_network

CONTEXT=RunContext(asset_id='authored-steady-layout',scenario_id='selected-network-laws',clock_id='steady',origin='synthetic',
    evidence=(EvidenceRef(id='authored-law-case',citation='Synthetic short laminar single-gas branches with explicit pressure boundaries',origin='synthetic'),))


def solve_reference(boundaries,edges,withdrawals=None):
    withdrawals=withdrawals or {};unknown=sorted({node for _,i,j,_,_ in edges for node in (i,j)}-set(boundaries));index={node:i for i,node in enumerate(unknown)}
    rho=PropsSI('Dmass','P',1e5,'T',300.,'Nitrogen');mu=PropsSI('V','P',1e5,'T',300.,'Nitrogen')
    resistance={name:128*mu*length/(math.pi*rho*diameter**4) for name,_,_,length,diameter in edges}
    matrix=np.zeros((len(unknown),len(unknown)));rhs=np.array([-withdrawals.get(n,0.) for n in unknown])
    for name,i,j,_,_ in edges:
        conductance=1/resistance[name]
        for n,other in ((i,j),(j,i)):
            if n not in index:continue
            row=index[n];matrix[row,row]+=conductance
            if other in index:matrix[row,index[other]]-=conductance
            else:rhs[row]+=conductance*boundaries[other]
    pressure=dict(boundaries)
    if unknown:pressure.update(zip(unknown,np.linalg.solve(matrix,rhs)))
    flows={name:(pressure[i]-pressure[j])/resistance[name] for name,i,j,_,_ in edges}
    return pressure,flows


def case(boundaries,edges,*,withdrawals=None,segments=1):
    withdrawals=withdrawals or {};pressure,expected=solve_reference(boundaries,edges,withdrawals)
    q=GasNetwork(fluid='Nitrogen',property_basis='HEOS-nitrogen-selected-short-branches',parameter_source_id='authored-law-case',
        boundaries=tuple(GasBoundary(id=n,pressure_pa_abs=p,temperature_k=300.) for n,p in boundaries.items()),
        junctions=tuple(GasJunction(id=n,initial_pressure_pa_abs=p,initial_temperature_k=300.,withdrawal_kg_s=withdrawals.get(n,0.)) for n,p in pressure.items() if n not in boundaries),
        pipes=tuple(GasPipe(id=name,start_node=i,end_node=j,length_m=length,diameter_m=diameter,roughness_m=0.,segments=segments) for name,i,j,length,diameter in edges),
        relative_solver_tolerance=1e-12,mass_tolerance_kg_s=1e-10,static_energy_tolerance_w=1e-6,relative_closure_tolerance=1e-9,momentum_tolerance_pa=1e-7)
    r=solve_gas_network(q,CONTEXT)
    assert r.status=='computed',r.notes
    native=r.native;actual={}
    for name,links in native['pipe_links'].items():
        values=[native['solution']['link_flows'][link]['mass_flow_kg_s'] for link in links]
        assert max(values)-min(values)<1e-10
        actual[name]=sum(values)/len(values)
        assert actual[name]==pytest.approx(expected[name],rel=6e-5,abs=2e-10)
    for n,p in pressure.items():
        assert native['inspected_states'][n]['pressure_pa_abs']==pytest.approx(p,abs=8e-5)
    assert r.checks['junction_mass_closed'] and r.checks['momentum_closed'] and r.checks['junction_static_energy_closed']
    assert max(abs(v) for v in r.checks['node_mass_residual_kg_s'].values())<1e-10 if r.checks['node_mass_residual_kg_s'] else True
    return r,actual,expected


@pytest.mark.parametrize('segments',[1,2])
def test_unequal_series_links_share_flow_and_add_independent_resistance(segments):
    r,a,e=case({'source':100002.,'sink':100000.},[('wide','source','middle',1.,.01),('narrow','middle','sink',1.3,.008)],segments=segments)
    assert a['wide']==pytest.approx(a['narrow'],abs=1e-10)


def test_unequal_parallel_branches_have_common_pressure_drop_and_resistance_split():
    r,a,e=case({'source':100001.,'sink':100000.},[('first','source','sink',1.,.01),('second','source','sink',1.4,.008)])
    expected_ratio=(1.4/.008**4)/(1./.01**4)
    assert a['first']/a['second']==pytest.approx(expected_ratio,rel=2e-8)


def test_split_and_recombine_with_upstream_and_downstream_tails():
    edges=[('supply','source','split',.7,.012),('first','split','join',1.,.01),('second','split','join',1.4,.008),('delivery','join','sink',.8,.011)]
    r,a,e=case({'source':100003.,'sink':100000.},edges)
    assert a['supply']==pytest.approx(a['first']+a['second'],abs=1e-10)
    assert a['delivery']==pytest.approx(a['first']+a['second'],abs=1e-10)


def test_intermediate_prescribed_delivery_subtracts_actual_mass_flow():
    load=4e-6
    r,a,e=case({'source':100003.,'sink':100000.},[('supply','source','takeoff',1.,.01),('remaining','takeoff','sink',1.,.01)],withdrawals={'takeoff':load})
    assert a['supply']-a['remaining']==pytest.approx(load,abs=1e-10)
    link=r.native['withdrawal_links']['takeoff']
    assert r.native['solution']['link_flows'][link]['mass_flow_kg_s']==load


def test_intermediate_pressure_specified_supply_adds_two_actual_mass_flows():
    edges=[('main','source','join',1.,.01),('injection','additional','join',1.2,.008),('delivery','join','sink',1.,.01)]
    r,a,e=case({'source':100003.,'additional':100002.8,'sink':100000.},edges)
    assert a['injection']>0
    assert a['delivery']==pytest.approx(a['main']+a['injection'],abs=1e-10)


@pytest.mark.parametrize('loop_index',[0,1,2])
def test_partial_loop_reuses_the_same_series_parallel_laws(loop_index):
    nodes=['source','one','two','sink']
    edges=[(f'main{i}',nodes[i],nodes[i+1],1.,.01) for i in range(3)]
    edges.append(('loop',nodes[loop_index],nodes[loop_index+1],1.,.008))
    r,a,e=case({'source':100003.,'sink':100000.},edges)
    for i in range(3):
        if i!=loop_index:assert a[f'main{i}']==pytest.approx(a[f'main{loop_index}']+a['loop'],abs=1e-10)


def test_fully_rough_high_re_parallel_loop_limit_matches_independent_quadratic_resistance():
    # Fully rough von Karman law is independent of the native Haaland closure.
    # Its asymptotic mismatch plus finite-Re and density changes are explicitly
    # bounded here; no exact equality with a historical transmission fit is used.
    rho=PropsSI('Dmass','P',1e5,'T',300.,'Nitrogen');mu=PropsSI('V','P',1e5,'T',300.,'Nitrogen')
    relative_roughness=.04;f=1/(-2*math.log10(relative_roughness/3.7))**2
    edges=[('supply','source','split',1.,2.),('first','split','join',1.,1.8),('second','split','join',1.4,1.5),('delivery','join','sink',1.2,2.)]
    resistance={name:f*length/(2*rho*(math.pi*d*d/4)**2*d) for name,_,_,length,d in edges}
    weights={name:resistance[name]**-.5 for name in ('first','second')}
    parallel=1/sum(weights.values())**2
    total=math.sqrt(3./(resistance['supply']+parallel+resistance['delivery']))
    expected={'supply':total,'delivery':total,**{name:total*w/sum(weights.values()) for name,w in weights.items()}}
    q=GasNetwork(fluid='Nitrogen',property_basis='HEOS-nitrogen-fully-rough-limit',parameter_source_id='authored-law-case',
        boundaries=(GasBoundary(id='source',pressure_pa_abs=100003.,temperature_k=300.),GasBoundary(id='sink',pressure_pa_abs=100000.,temperature_k=300.)),
        junctions=(GasJunction(id='split',initial_pressure_pa_abs=100002.5,initial_temperature_k=300.),GasJunction(id='join',initial_pressure_pa_abs=100000.5,initial_temperature_k=300.)),
        pipes=tuple(GasPipe(id=name,start_node=i,end_node=j,length_m=length,diameter_m=d,roughness_m=relative_roughness*d,segments=1) for name,i,j,length,d in edges),
        relative_solver_tolerance=1e-12,mass_tolerance_kg_s=1e-8,static_energy_tolerance_w=1e-5,relative_closure_tolerance=1e-8,momentum_tolerance_pa=1e-7)
    result=solve_gas_network(q,CONTEXT);assert result.status=='computed',result.notes
    observed={name:result.native['solution']['link_flows'][links[0]]['mass_flow_kg_s'] for name,links in result.native['pipe_links'].items()}
    for name,_,_,_,diameter in edges:
        assert observed[name]==pytest.approx(expected[name],rel=.003)
        reynolds=4*abs(observed[name])/(math.pi*diameter*mu)
        assert reynolds>1e5
        # Bound the finite-Re term relative to the roughness term appearing in
        # the explicit turbulent approximation, independently of solver output.
        assert (6.9/reynolds)/(relative_roughness/3.7)**1.11<.01
    assert observed['supply']==pytest.approx(observed['first']+observed['second'],rel=1e-8)
    assert observed['delivery']==pytest.approx(observed['first']+observed['second'],rel=1e-8)
    assert max(d['relative_density_change'] for d in result.checks['segments'])<4e-5
    assert max(d['relative_pressure_change'] for d in result.checks['segments'])<4e-5
