"""Multi-population H-trap equilibrium vs independent scalar mass-balance root."""
import copy
import math
import runpy
import pytest
from pipeline_integrity.hydrogen_transport import EQUILIBRIUM, evaluate_hydrogen_transport

R=8.31446261815324
REQUESTS,CTX=runpy.run_path('examples/hydrogen_transport.py')['make_cases']()
BASE=REQUESTS[EQUILIBRIUM]

def trap_fraction(theta,binding_ratio):
    return binding_ratio*theta/(1+(binding_ratio-1)*theta)

def independent_lattice_occupancy(total,n_lattice,trap_specs):
    lo,hi=0.0,1.0
    for _ in range(120):
        mid=(lo+hi)/2
        mass=n_lattice*mid+sum(n*trap_fraction(mid,b) for n,b in trap_specs)
        if mass<total:lo=mid
        else:hi=mid
    return (lo+hi)/2

def request_for(theta,temp,ratios):
    p=copy.deepcopy(BASE.parameters)
    n_lattice=1200.0
    sites=[80.0,45.0]
    p['lattice_site_density_mol_m3']=n_lattice
    p['lattice_diffusivity_m2_s']=2.5e-8
    p['temperature_K']=temp
    p['maximum_supported_lattice_occupancy']=0.8
    traps=[]
    for i,(site_density,ratio) in enumerate(zip(sites,ratios)):
        trap=copy.deepcopy(p['traps'][0])
        trap.update(trap_id=f'population-{i}',site_density_mol_m3=site_density,
            binding_energy_J_mol=R*temp*math.log(ratio),maximum_supported_occupancy=0.9999)
        traps.append(trap)
    p['traps']=traps
    p['total_hydrogen_atoms_mol_m3']=n_lattice*theta+sum(n*trap_fraction(theta,b) for n,b in zip(sites,ratios))
    return BASE.model_copy(update={'parameters':p,'declared_temperature_K':temp}),n_lattice,sites

def evaluate(request):
    record=evaluate_hydrogen_transport(request,CTX)
    assert record['status']=='computed',record['issues']
    return record['values']

@pytest.mark.parametrize('theta,temp,ratios',[(0.03,295.0,(1.0,4.0)),(0.17,300.0,(6.0,15.0)),(0.35,305.0,(2.5,9.0))])
def test_multiple_trap_populations_match_independent_langmuir_mass_root(theta,temp,ratios):
    request,n_lattice,sites=request_for(theta,temp,ratios)
    values=evaluate(request)
    expected=independent_lattice_occupancy(request.parameters['total_hydrogen_atoms_mol_m3'],n_lattice,list(zip(sites,ratios)))
    assert values['lattice_occupancy']==pytest.approx(expected,rel=3e-11,abs=2e-14)
    assert values['lattice_hydrogen_atoms_mol_m3']==pytest.approx(n_lattice*expected,rel=3e-11)
    for row,site,binding in zip(values['traps'],sites,ratios):
        expected_trap=trap_fraction(expected,binding)
        assert row['occupancy']==pytest.approx(expected_trap,rel=3e-11,abs=2e-14)
        assert row['hydrogen_atoms_mol_m3']==pytest.approx(site*expected_trap,rel=3e-11)
    total=sum([values['lattice_hydrogen_atoms_mol_m3'],*[row['hydrogen_atoms_mol_m3'] for row in values['traps']]])
    assert total==pytest.approx(request.parameters['total_hydrogen_atoms_mol_m3'],abs=2e-8)
    assert abs(values['mass_balance_residual_mol_m3'])<2e-8

def test_capacity_exceedance_is_withheld():
    request,_,sites=request_for(0.2,300.0,(3.0,7.0))
    p=copy.deepcopy(request.parameters)
    p['total_hydrogen_atoms_mol_m3']=p['lattice_site_density_mol_m3']+sum(sites)+1.0
    result=evaluate_hydrogen_transport(request.model_copy(update={'parameters':p}),CTX)
    assert result['status']=='out_of_scope'
    assert not result['values']
