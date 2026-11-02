"""Real-EOS flash/domain and uniform finite-volume checks, separate from ideal-gas proofs."""
import numpy as np
import pytest

CoolProp=pytest.importorskip("CoolProp")
from CoolProp.CoolProp import PropsSI

from pipeline_integrity.gas_acoustic_pipe import (
    CoolPropGasInternalEnergyEOS, GasAcousticPipeSettings, GasPipeBoundary,
    GasPipeBoundaryStage, gas_conservative_state, solve_gas_acoustic_pipe,
)


@pytest.mark.parametrize("fluid,p,t", [("Nitrogen",1e5,300.),("Methane",5e6,320.),("Hydrogen",2e7,300.)])
def test_density_internal_energy_flash_recovers_independent_pt_state(fluid,p,t):
    rho=PropsSI("Dmass","P",p,"T",t,fluid)
    e=PropsSI("Umass","P",p,"T",t,fluid)
    provider=CoolPropGasInternalEnergyEOS(fluid)
    point=provider.evaluate(rho,e)
    assert point.pressure_pa_abs==pytest.approx(p,rel=2e-9)
    assert point.temperature_K==pytest.approx(t,rel=2e-9)
    assert point.sound_speed_m_s==pytest.approx(PropsSI("A","P",p,"T",t,fluid),rel=2e-9)
    assert point.cv_J_kg_K==pytest.approx(PropsSI("Cvmass","P",p,"T",t,fluid),rel=2e-9)


def test_negative_declared_energy_reference_preserves_uniform_real_gas_state_and_budgets():
    fluid="Methane";p=5e6;t=320.;offset=-1e6
    rho=PropsSI("Dmass","P",p,"T",t,fluid)
    e=PropsSI("Umass","P",p,"T",t,fluid)+offset
    assert e<0
    provider=CoolPropGasInternalEnergyEOS(fluid,energy_reference_offset_J_kg=offset)
    u=gas_conservative_state(np.full(6,rho),2.,np.full(6,e))
    b=GasPipeBoundary(kind="periodic")
    result=solve_gas_acoustic_pipe(u,GasAcousticPipeSettings(length_m=100.,inner_diameter_m=.2,duration_s=.002),
        eos=provider,boundary_stages=(GasPipeBoundaryStage(start_time_s=0.,left=b,right=b),))
    assert result.conservative==pytest.approx(u,rel=3e-14,abs=1e-10)
    assert result.pressure_pa_abs==pytest.approx(np.full(6,p),rel=2e-9)
    assert np.all(result.conservative[:,2]<0)
    assert result.eos_identity["allowed_phases"]==["gas","supercritical_gas","supercritical"]
    assert result.eos_identity["energy_reference_offset_J_kg"]==offset
    assert all(phase in ("gas","supercritical_gas","supercritical") for phase in result.phase_by_cell)
    assert np.max(np.abs(result.ledger.residual))<1e-7


@pytest.mark.parametrize("phase", ["liquid","two_phase"])
def test_liquid_and_two_phase_eos_states_are_rejected_in_selected_gas_domain(phase):
    if phase=="liquid":
        rho=PropsSI("Dmass","P",1e5,"T",300.,"Water")
        e=PropsSI("Umass","P",1e5,"T",300.,"Water")
    else:
        rho=PropsSI("Dmass","P",1e5,"Q",.5,"Water")
        e=PropsSI("Umass","P",1e5,"Q",.5,"Water")
    with pytest.raises(ValueError):CoolPropGasInternalEnergyEOS("Water").evaluate(rho,e)
