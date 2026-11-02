"""Reflecting cavity modes against the independent linear wave eigenproblem."""
import math
import numpy as np
import pytest
from pipeline_integrity.gas_acoustic_pipe import GasAcousticPipeSettings as Settings,GasPipeBoundary as Boundary,GasPipeBoundaryStage as Stage,IdealGasInternalEnergyEOS as EOS,gas_conservative_state,solve_gas_acoustic_pipe

@pytest.mark.parametrize('mode,length',[(1,1.),(2,1.),(1,2.5)])
def test_closed_cavity_mode_phase_convergence_and_closed_inventories(mode,length):
    eos=EOS(specific_gas_constant_J_kg_K=1.,gamma=1.4)
    rho0=1.;p0=1.;a=math.sqrt(1.4);amplitude=1e-5
    k=mode*math.pi/length;omega=a*k;quarter=math.pi/(2*omega)
    errors=[]
    for cells in (40,80,160):
        x=(np.arange(cells)+.5)*length/cells
        rho=rho0+amplitude/a**2*np.cos(k*x);pressure=p0+amplitude*np.cos(k*x)
        initial=gas_conservative_state(rho,np.zeros(cells),pressure/(rho*(eos.gamma-1)))
        boundary=Boundary(kind='reflective')
        result=solve_gas_acoustic_pipe(initial,Settings(length_m=length,inner_diameter_m=.1,duration_s=quarter,cfl=.4),eos=eos,
            boundary_stages=(Stage(start_time_s=0,left=boundary,right=boundary),))
        # Linear wave eigenvectors: p'=A cos(kx)cos(wt), u=A/(rho*a) sin(kx)sin(wt).
        pc=2*np.mean((result.pressure_pa_abs-p0)*np.cos(k*x))/amplitude
        vs=2*np.mean(result.velocity_m_s*np.sin(k*x))*rho0*a/amplitude
        phase=math.atan2(vs,pc)
        errors.append(abs(phase-math.pi/2))
        assert vs>0
        assert abs(phase/quarter-omega)/omega<.005
        assert np.max(np.abs(result.ledger.residual))<2e-12
        for index in (0,2):
            assert result.ledger.final_mass_momentum_energy[index]==pytest.approx(result.ledger.initial_mass_momentum_energy[index],abs=2e-12)
    assert errors[2]<errors[1]<errors[0]
    assert errors[-1]<.0005
