"""Fixed-temperature Norton secondary creep power-law identities."""
import pytest
from matintegrity.models.constitutive import NortonCreep, norton_creep


def solve(stress, reference, rate, exponent, duration):
    return norton_creep(NortonCreep(stress_Pa=stress, reference_stress_Pa=reference,
        rate_at_reference_stress_per_s=rate, stress_exponent=exponent,
        exposure_s=duration)).values


@pytest.mark.parametrize("stress,reference,rate,n,duration", [
    (50e6,100e6,2e-9,3.0,3600.0),
    (150e6,100e6,8e-10,4.5,72000.0),
    (250e6,250e6,1e-7,7.0,0.0),
])
def test_norton_creep_rate_and_accumulated_strain_match_power_law(stress,reference,rate,n,duration):
    result=solve(stress,reference,rate,n,duration)
    expected_rate=rate*(stress/reference)**n
    assert result['steady_creep_strain_rate_per_s']==pytest.approx(expected_rate,rel=2e-14)
    assert result['steady_creep_strain']==pytest.approx(expected_rate*duration,rel=2e-14,abs=1e-30)


def test_norton_creep_homogeneity_and_zero_stress_limit():
    base=solve(100e6,100e6,1e-9,4.0,1000.0)
    double_stress=solve(200e6,100e6,1e-9,4.0,1000.0)
    double_time=solve(100e6,100e6,1e-9,4.0,2000.0)
    zero=solve(0.0,100e6,1e-9,4.0,5000.0)
    assert base['steady_creep_strain_rate_per_s']==pytest.approx(1e-9)
    assert double_stress['steady_creep_strain_rate_per_s']==pytest.approx(16*base['steady_creep_strain_rate_per_s'])
    assert double_time['steady_creep_strain']==pytest.approx(2*base['steady_creep_strain'])
    assert zero['steady_creep_strain_rate_per_s']==0.0
    assert zero['steady_creep_strain']==0.0
