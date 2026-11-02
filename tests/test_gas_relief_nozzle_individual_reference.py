"""Independent isentropic gas-nozzle flux and rounded sizing-coefficient bounds."""
import math
import pytest
from pipeline_integrity.models.library_models import ReliefGas, relief_gas

RU=6.02214076e23*1.380649e-23
BASE=dict(mass_flow_kg_s=2.3,temperature_k=340.,compressibility=1.,molar_mass_kg_mol=.028,
          heat_capacity_ratio=1.4,relieving_pressure_pa_abs=8e5,back_pressure_pa_abs=2e5,
          discharge_coefficient=.91,back_pressure_factor=1.,combination_factor=1.)


def calc(**changes):
    args=BASE|changes
    return relief_gas(ReliefGas(**args)).values["required_area_m2"],args


def independent_nozzle_area(p):
    k=p["heat_capacity_ratio"];ratio=p["back_pressure_pa_abs"]/p["relieving_pressure_pa_abs"]
    critical=(2/(k+1))**(k/(k-1));r=max(ratio,critical)
    # Integration of isentropic enthalpy fall with continuity, rather than API C/F2.
    available=-math.expm1((k-1)/k*math.log(r))
    flux=p["relieving_pressure_pa_abs"]*math.sqrt(2*k/(k-1)/(RU/p["molar_mass_kg_mol"]*p["temperature_k"])
        *r**(2/k)*available)
    return p["mass_flow_kg_s"]/(p["discharge_coefficient"]*p["combination_factor"]*flux),ratio<=critical


def assert_physical_reference_with_declared_coefficient_precision(area,p):
    expected,critical=independent_nozzle_area(p)
    # Library sizing constants carry stated decimal precision, not an exact SI-R identity.
    budget=.000005/.039475 if critical else .05/17.85
    assert abs(area/expected-1)<=budget


@pytest.mark.parametrize("k",[1.1,1.4,1.67])
@pytest.mark.parametrize("ratio",[.4,.8])
def test_existing_relief_area_matches_independent_ideal_nozzle_mass_flux(k,ratio):
    area,p=calc(heat_capacity_ratio=k,back_pressure_pa_abs=BASE["relieving_pressure_pa_abs"]*ratio)
    assert_physical_reference_with_declared_coefficient_precision(area,p)


@pytest.mark.parametrize("k",[1.1,1.4,1.67])
def test_k_dependent_critical_event_is_checked_from_each_side(k):
    critical=(2/(k+1))**(k/(k-1))
    samples=[]
    for r in (critical*(1-1e-7),critical*(1+1e-7)):
        area,p=calc(heat_capacity_ratio=k,back_pressure_pa_abs=BASE["relieving_pressure_pa_abs"]*r)
        assert_physical_reference_with_declared_coefficient_precision(area,p)
        samples.append(area)
    # Different rounded constants on two branches need not be exactly continuous.
    assert abs(samples[1]/samples[0]-1) <= .000005/.039475+.05/17.85


def test_choked_plateau_and_subcritical_backpressure_monotonicity():
    low,_=calc(back_pressure_pa_abs=1e5)
    lower,_=calc(back_pressure_pa_abs=2e5)
    assert low==lower
    a,_=calc(back_pressure_pa_abs=.7*BASE["relieving_pressure_pa_abs"])
    b,_=calc(back_pressure_pa_abs=.9*BASE["relieving_pressure_pa_abs"])
    assert b>a>low


def test_non_directed_relief_pressure_is_rejected():
    with pytest.raises(ValueError):calc(back_pressure_pa_abs=BASE["relieving_pressure_pa_abs"])
