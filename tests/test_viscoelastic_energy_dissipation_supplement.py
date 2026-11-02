"""Thermodynamic work, stored energy and positive dashpot dissipation."""
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.viscoelastic_strain import ConstantStressStrain,evaluate_constant_stress_strain


@pytest.mark.parametrize('stress,instant,delayed,tau,viscosity',[
    (8e6,2e11,1e11,20.,4e13),(2e6,4e9,2e9,5.,2e12),(1e7,5e10,3e10,100.,1e14),
    (1e6,1e10,4e9,.01,4e8),(4e6,8e10,1e10,1000.,1e15),(3e6,6e10,2e10,1.,1e12)])
def test_actual_constitutive_strains_obey_independent_work_energy_dissipation_system(stress,instant,delayed,tau,viscosity):
    x=np.array([0.,.001,.01,.1,1.,3.,10.,30.]);times=tuple(x*tau)
    q=ConstantStressStrain(tensile_stress_pa=stress,instantaneous_modulus_pa=instant,delayed_modulus_pa=delayed,
        retardation_time_s=tau,viscous_resistance_pa_s=viscosity,elapsed_times_s=times)
    actual=evaluate_constant_stress_strain(q).values;ratio=delayed*tau/viscosity
    # y=(normalized delayed strain, viscous strain, delayed dashpot heat,
    # Maxwell dashpot heat, external work). The ODE starts after the ideal
    # stress step; pre-existing instantaneous spring energy is not counted as
    # work performed during the subsequently constant-load interval.
    def ode(t,y):
        rate=1-y[0]
        return [rate,ratio,rate*rate,ratio,rate+ratio]
    reference=solve_ivp(ode,(0.,30.),[0.]*5,method='DOP853',t_eval=x,rtol=1e-12,atol=1e-13)
    assert reference.success
    delayed_normalized=np.array(actual['delayed_strain'])*delayed/stress
    viscous_normalized=np.array(actual['viscous_strain'])*delayed/stress
    assert delayed_normalized==pytest.approx(reference.y[0],rel=2e-10,abs=3e-12)
    assert viscous_normalized==pytest.approx(reference.y[1],rel=2e-10,abs=3e-12)
    stored=.5*delayed_normalized**2
    external=delayed_normalized+viscous_normalized
    dissipation=reference.y[2]+reference.y[3]
    assert external==pytest.approx(stored+dissipation,rel=2e-10,abs=5e-12)
    assert external==pytest.approx(reference.y[4],rel=2e-10,abs=5e-12)
    assert np.min(dissipation)>=0 and all(np.diff(dissipation)>0)
    assert actual['instantaneous_strain']==stress/instant
    # Restore physical strain-energy/work density units Pa=J/m^3.
    physical=stress*stress/delayed
    assert np.all(np.isfinite(physical*(stored+dissipation)))


def test_zero_load_has_no_stored_energy_or_dissipation():
    q=ConstantStressStrain(tensile_stress_pa=0.,instantaneous_modulus_pa=2e11,delayed_modulus_pa=1e11,
        retardation_time_s=10.,viscous_resistance_pa_s=1e13,elapsed_times_s=(0.,10.,100.))
    r=evaluate_constant_stress_strain(q).values
    assert r['instantaneous_strain']==0. and r['total_strain']==[0.,0.,0.]


def test_same_physical_time_order_and_repeats_are_not_reinterpreted_as_a_load_history():
    q=ConstantStressStrain(tensile_stress_pa=8e6,instantaneous_modulus_pa=2e11,delayed_modulus_pa=1e11,
        retardation_time_s=20.,viscous_resistance_pa_s=4e13,elapsed_times_s=(40.,0.,20.,40.))
    r=evaluate_constant_stress_strain(q).values
    assert r['elapsed_times_s']==[40.,0.,20.,40.] and r['total_strain'][0]==r['total_strain'][-1]


def test_common_stress_modulus_viscosity_scaling_preserves_strain_not_energy_units():
    base=dict(tensile_stress_pa=8e6,instantaneous_modulus_pa=2e11,delayed_modulus_pa=1e11,
        retardation_time_s=20.,viscous_resistance_pa_s=4e13,elapsed_times_s=(0.,20.,100.))
    a=evaluate_constant_stress_strain(ConstantStressStrain(**base)).values
    scaled={k:(v*3 if k in ['tensile_stress_pa','instantaneous_modulus_pa','delayed_modulus_pa','viscous_resistance_pa_s'] else v) for k,v in base.items()}
    b=evaluate_constant_stress_strain(ConstantStressStrain(**scaled)).values
    assert a['total_strain']==pytest.approx(b['total_strain'])
