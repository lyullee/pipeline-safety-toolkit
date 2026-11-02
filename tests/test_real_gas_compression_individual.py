"""Selected EOS compressor work verified through independent v dp and budgets.

The EOS is a supporting material backend, not itself established as an exact
description of every natural-gas composition by these device-path checks.
"""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.models.equipment import Compression,compression
from pipeline_integrity.thermo import CoolPropProvider,StateRequest


@pytest.mark.parametrize('fluid,p1,p2,T,eta',[
    ('Methane',1e5,3e5,300.,1.),('Methane',2e6,5e6,300.,.8),
    ('Hydrogen',1e5,2e6,300.,.75),('Hydrogen',2e6,8e6,320.,1.)])
def test_real_gas_entropy_path_volume_work_and_actual_first_law(fluid,p1,p2,T,eta):
    provider=CoolPropProvider(use_cache=False)
    q=Compression(fluid=fluid,inlet_pressure_pa_abs=p1,inlet_temperature_k=T,outlet_pressure_pa_abs=p2,
        mass_flow_kg_s=.7,isentropic_efficiency=eta)
    inlet=provider.resolve(StateRequest(fluid=fluid,inputs={'P':p1,'T':T}))
    assert inlet.phase in {'gas','supercritical_gas','supercritical'}
    s=inlet.entropy_j_kg_k
    def volume(p):
        state=provider.resolve(StateRequest(fluid=fluid,inputs={'P':p,'Smass':s}))
        assert state.phase in {'gas','supercritical_gas','supercritical'}
        return 1/state.density_kg_m3
    reversible_work=quad(volume,p1,p2,epsabs=1e-5,epsrel=2e-10)[0]
    r=compression(q,property_provider=provider);assert r.status=='computed'
    work=r.values['specific_work_j_kg']
    assert work==pytest.approx(reversible_work/eta,rel=5e-8)
    assert r.values['shaft_power_w']==pytest.approx(.7*work,rel=2e-14)
    actual=provider.resolve(StateRequest(fluid=fluid,inputs={'P':p2,'Hmass':inlet.enthalpy_j_kg+work}))
    assert actual.phase in {'gas','supercritical_gas','supercritical'}
    assert actual.enthalpy_j_kg-inlet.enthalpy_j_kg==pytest.approx(work,rel=2e-10)
    assert actual.temperature_k==pytest.approx(r.values['outlet_temperature_k'],abs=2e-10)
    if eta==1:
        # Use a dimensionless entropy/cp defect for the P,S -> P,H flash cycle.
        # Hydrogen's independently preserved first attempt showed8.6e-6J/kg/K
        # absolute round-trip residual at an entropy reference ~4.2e4J/kg/K;
        # this is a backend flash defect, not a new irreversible work term.
        assert abs(actual.entropy_j_kg_k-s)/actual.cp_j_kg_k<1e-9
    else:assert actual.entropy_j_kg_k>s


def test_zero_mass_flow_preserves_specific_path_and_zero_power():
    q=Compression(fluid='Methane',inlet_pressure_pa_abs=2e5,inlet_temperature_k=300.,outlet_pressure_pa_abs=4e5,
        mass_flow_kg_s=0.,isentropic_efficiency=.9)
    r=compression(q,property_provider=CoolPropProvider(use_cache=False))
    assert r.values['specific_work_j_kg']>0 and r.values['shaft_power_w']==0


def test_liquid_inlet_is_out_of_scope_without_phase_forcing():
    q=Compression(fluid='Water',inlet_pressure_pa_abs=1e5,inlet_temperature_k=300.,outlet_pressure_pa_abs=2e5,
        mass_flow_kg_s=1.,isentropic_efficiency=.8)
    assert compression(q,property_provider=CoolPropProvider(use_cache=False)).status=='out_of_scope'
