"""Existing elastic eigenbuckling with a declared thermal-compression preload."""
import math,runpy
import pytest
from pipeline_integrity.buckling import BucklingRequest,evaluate_buckling,buckling_boundary_metadata

MAKE=runpy.run_path('examples/buckling.py')['make_case']

@pytest.mark.parametrize('flexural_modulus,compressive_modulus,alpha,temperature_rise',
    [(50e9,30e9,20e-6,25.),(80e9,60e9,8e-6,60.),(200e9,200e9,12e-6,40.)])
def test_guide_span_critical_temperature_converges_to_independent_euler_thermal_equilibrium(flexural_modulus,compressive_modulus,alpha,temperature_rise):
    errors=[]
    for elements in (2,4,8,16):
        q,c=MAKE(reviewed=True,elements=elements)
        section=q.frame.sections[0]
        thermal_force=compressive_modulus*section.area_m2*alpha*temperature_rise
        material=q.frame.materials[0].model_copy(update={'young_modulus_pa':flexural_modulus})
        load=q.frame.node_loads[0].model_copy(update={'value':-thermal_force})
        frame=q.frame.model_copy(update={'materials':(material,),'node_loads':(load,)})
        q=BucklingRequest.model_validate(q.model_copy(update={'frame':frame}).model_dump(mode='python'))
        c=c.model_copy(update={'metadata':{'buckling_boundary':buckling_boundary_metadata(q)}})
        result=evaluate_buckling(q,c)
        assert result['status']=='computed_diagnostic',result['issues']
        factor=result['solution']['critical_positive_load_factor']
        expected_critical_force=math.pi**2*flexural_modulus*section.iy_m4/4**2
        expected_temperature=expected_critical_force/(compressive_modulus*section.area_m2*alpha)
        assert factor*thermal_force==pytest.approx(expected_critical_force,rel=.008 if elements==2 else .001 if elements==4 else .0003)
        errors.append(abs(factor*temperature_rise/expected_temperature-1))
        assert all(m['original_free_dof_equilibrium_passed'] for m in result['solution']['modes'])
        assert result['shell_collapse_plasticity_or_imperfection_solved'] is False
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<2e-5
