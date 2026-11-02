"""Independent pressure-volume work integration for linear liquid compression."""
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.stored_liquid_energy import (
    LinearLiquidCompression, evaluate_linear_liquid_compression,
)


@pytest.mark.parametrize('pressure,volume,modulus',[(1e6,.2,2e9),(7e6,.015,1.6e9),(2e5,4.,3e9)])
def test_energy_matches_separately_integrated_compression_work(pressure,volume,modulus):
    result=evaluate_linear_liquid_compression(LinearLiquidCompression(pressure,volume,modulus))
    displacement=volume*pressure/modulus
    # Independent load-displacement path: pressure grows linearly as liquid
    # volume is removed, and each infinitesimal displaced volume does p*dV work.
    work=solve_ivp(lambda removed,state:[modulus*removed/volume],(0.,displacement),[0.],
                   rtol=1e-11,atol=1e-10,method='DOP853')
    assert work.success
    assert result.stored_elastic_energy_j == pytest.approx(work.y[0,-1],rel=1e-11,abs=1e-10)
    assert result.volume_reduction_m3 == pytest.approx(displacement,rel=1e-14)
    assert modulus*result.volumetric_compression_strain == pytest.approx(pressure)


def test_zero_reference_load_and_rigid_modulus_limit():
    zero=evaluate_linear_liquid_compression(LinearLiquidCompression(0.,1.,2e9))
    assert zero.stored_elastic_energy_j==zero.volume_reduction_m3==zero.volumetric_compression_strain==0.
    finite=evaluate_linear_liquid_compression(LinearLiquidCompression(1e6,1.,2e9))
    stiffer=evaluate_linear_liquid_compression(LinearLiquidCompression(1e6,1.,2e12))
    assert stiffer.stored_elastic_energy_j == pytest.approx(finite.stored_elastic_energy_j/1000)


def test_pressure_volume_similarity_and_work_conjugate_derivative():
    def energy(p,v=.2):return evaluate_linear_liquid_compression(LinearLiquidCompression(p,v,2e9)).stored_elastic_energy_j
    assert energy(2e6)==pytest.approx(4*energy(1e6))
    assert energy(1e6,.6)==pytest.approx(3*energy(1e6))
    p,step=1e6,10.
    volume_reduction=evaluate_linear_liquid_compression(LinearLiquidCompression(p,.2,2e9)).volume_reduction_m3
    assert (energy(p+step)-energy(p-step))/(2*step)==pytest.approx(volume_reduction,rel=1e-10)


@pytest.mark.parametrize('args',[(1e6,0.,2e9),(-1.,1.,2e9),(1.,1.,0.),(True,1.,2e9),(float('inf'),1.,2e9),(2e9,1.,2e9)])
def test_invalid_reference_states_are_rejected(args):
    with pytest.raises(ValueError):LinearLiquidCompression(*args)


def test_nonfinite_computed_work_is_rejected():
    with pytest.raises(ValueError,match='floating-point'):
        evaluate_linear_liquid_compression(LinearLiquidCompression(1e300,1e100,1e303))
