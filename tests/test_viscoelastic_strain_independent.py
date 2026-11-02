"""Constitutive ODE, initial/long-time limits and stress/time scaling."""
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.viscoelastic_strain import ConstantStressStrain, evaluate_constant_stress_strain


def case(**changes):
    return ConstantStressStrain(**(dict(tensile_stress_pa=2e6,
        instantaneous_modulus_pa=2e9,delayed_modulus_pa=1e9,
        retardation_time_s=4.0,viscous_resistance_pa_s=8e10,
        elapsed_times_s=(0.0,0.1,1.0,4.0,10.0,40.0))|changes))


@pytest.mark.parametrize("stress,tau", [(2e6,4.0),(8e5,0.2),(1e7,20.0)])
def test_strain_matches_independently_integrated_element_odes(stress,tau):
    q=case(tensile_stress_pa=stress,retardation_time_s=tau)
    r=evaluate_constant_stress_strain(q).values
    def derivative(t,state):
        return [(q.tensile_stress_pa-q.delayed_modulus_pa*state[0])/(q.delayed_modulus_pa*q.retardation_time_s),
                q.tensile_stress_pa/q.viscous_resistance_pa_s]
    solution=solve_ivp(derivative,(0.0,40.0),[0.0,0.0],t_eval=q.elapsed_times_s,
                       method="DOP853",rtol=1e-11,atol=1e-14)
    assert solution.success
    assert r["delayed_strain"] == pytest.approx(solution.y[0],rel=1e-9,abs=1e-12)
    assert r["viscous_strain"] == pytest.approx(solution.y[1],rel=1e-10,abs=1e-12)
    assert r["total_strain"] == pytest.approx(q.tensile_stress_pa/q.instantaneous_modulus_pa+solution.y[0]+solution.y[1],rel=1e-9,abs=1e-12)


def test_zero_time_short_time_and_long_time_limits():
    q=case(elapsed_times_s=(0.0,4e-8,400.0))
    r=evaluate_constant_stress_strain(q).values
    assert r["total_strain"][0] == q.tensile_stress_pa/q.instantaneous_modulus_pa
    assert r["delayed_strain"][0] == r["viscous_strain"][0] == 0
    initial_delayed_rate=q.tensile_stress_pa/(q.delayed_modulus_pa*q.retardation_time_s)
    assert r["delayed_strain"][1]/q.elapsed_times_s[1] == pytest.approx(initial_delayed_rate,rel=1e-8)
    assert r["delayed_strain"][2] == pytest.approx(q.tensile_stress_pa/q.delayed_modulus_pa,rel=1e-14)


def test_stress_linearity_and_zero_stress():
    base=evaluate_constant_stress_strain(case()).values
    scaled=evaluate_constant_stress_strain(case(tensile_stress_pa=6e6)).values
    zero=evaluate_constant_stress_strain(case(tensile_stress_pa=0.0)).values
    assert scaled["total_strain"] == pytest.approx([3*x for x in base["total_strain"]])
    assert zero["total_strain"] == [0.0]*6


def test_coordinate_order_and_common_time_viscosity_scaling():
    q=case(elapsed_times_s=(4.0,0.0,4.0,1.0))
    a=evaluate_constant_stress_strain(q).values
    b=evaluate_constant_stress_strain(case(elapsed_times_s=tuple(7*t for t in q.elapsed_times_s),
        retardation_time_s=7*q.retardation_time_s,viscous_resistance_pa_s=7*q.viscous_resistance_pa_s)).values
    assert a["elapsed_times_s"] == list(q.elapsed_times_s)
    assert a["total_strain"] == pytest.approx(b["total_strain"])


@pytest.mark.parametrize("changes", [dict(retardation_time_s=0.0),dict(tensile_stress_pa=-1.0),dict(elapsed_times_s=(-1.0,)),dict(viscous_resistance_pa_s=float("inf"))])
def test_invalid_material_or_time_coordinates_are_rejected(changes):
    with pytest.raises(ValueError):case(**changes)
