"""Semi-infinite Fickian diffusion profile and uptake analytic oracles."""
import importlib.util
import math
from pathlib import Path
import pytest
from pipeline_integrity.hydrogen_transport import SEMI, evaluate_hydrogen_transport

spec=importlib.util.spec_from_file_location('hydrogen_diffusion_reference_example',Path(__file__).resolve().parents[1]/'examples'/'hydrogen_transport.py')
example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
REQUESTS,CTX=example.make_cases();BASE=REQUESTS[SEMI]

def solve(diffusivity,time,depths,initial=10.0,surface=110.0):
    request=BASE.model_copy(update={
        'parameters':{'diffusivity_m2_s':diffusivity,'exposure_s':time,'depth_m':list(depths),
            'initial_concentration_mol_m3':initial,'surface_concentration_mol_m3':surface},
        'depth_point_ids':tuple(f'z-{i}' for i in range(len(depths))),
    })
    return evaluate_hydrogen_transport(request,CTX)

@pytest.mark.parametrize('diffusivity,time',[(1e-8,4.0),(2.5e-8,1.6)])
def test_constant_surface_profile_matches_exact_erfc_solution(diffusivity,time):
    length=math.sqrt(diffusivity*time)
    depths=[0.0,0.25*length,0.75*length,1.5*length,3.0*length]
    result=solve(diffusivity,time,depths)
    assert result['status']=='computed',result['issues']
    expected=[10.0+100.0*math.erfc(z/(2.0*length)) for z in depths]
    assert result['values']['concentration_mol_m3']==pytest.approx(expected,rel=2e-12,abs=1e-12)
    assert result['values']['concentration_mol_m3'][0]==pytest.approx(110.0)
    assert all(a>=b for a,b in zip(result['values']['concentration_mol_m3'],result['values']['concentration_mol_m3'][1:]))


def test_integrated_excess_equals_closed_form_half_space_uptake():
    diffusivity,time,initial,surface=3.2e-9,12.5,22.0,140.0
    depths=[0.0,1e-5,3e-5,1e-4]
    result=solve(diffusivity,time,depths,initial,surface)
    assert result['status']=='computed',result['issues']
    expected=2.0*(surface-initial)*math.sqrt(diffusivity*time/math.pi)
    assert result['values']['integrated_excess_mol_m2']==pytest.approx(expected,rel=2e-12)
    assert result['values']['surface_flux_mol_m2_s']*(2.0*time)==pytest.approx(expected,rel=2e-12)


def test_diffusion_length_similarity_and_sqrt_time_scaling():
    d=1e-8; t=4.0; depths=[0.0,1e-4,2e-4,4e-4]
    base=solve(d,t,depths)
    same_similarity=solve(4*d,t/4,depths)
    longer=solve(d,4*t,[2*z for z in depths])
    assert base['values']['concentration_mol_m3']==pytest.approx(same_similarity['values']['concentration_mol_m3'],rel=2e-12)
    assert base['values']['concentration_mol_m3']==pytest.approx(longer['values']['concentration_mol_m3'],rel=2e-12)
