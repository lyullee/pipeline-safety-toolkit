"""Single-path gas momentum/energy with signed height and independent ODE.

The continuous reference includes acceleration; the historical exponential
constant-temperature relation is checked as its explicitly low-Mach reduction.
"""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.thermo import ThermodynamicState
from pipeline_integrity.gas_potential_network import (
    GasPotentialPathRequest,GasPotentialSegment,gas_potential_path_metadata,evaluate_gas_potential_path)

R=287.05;CP=1005.;MU=1.8e-5;T=300.;G=9.80665;D=.1;AREA=math.pi*D*D/4;P0=2e5


class IdealReferenceGas:
    def resolve(self,q):
        p=q.inputs['P'];t=q.inputs['T']
        return ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=p,temperature_k=t,
            density_kg_m3=p/(R*t),enthalpy_j_kg=CP*t,entropy_j_kg_k=CP*math.log(t)-R*math.log(p),
            phase='gas',viscosity_pa_s=MU,cp_j_kg_k=CP,cv_j_kg_k=CP-R,conductivity_w_m_k=.026,
            speed_of_sound_m_s=math.sqrt(CP/(CP-R)*R*t),provider_version='authored-ideal-law',provider_revision='analytic-v1')


def continuous_reference(flow,length,height,acceleration=True):
    re=flow*D/(AREA*MU);assert re<2000
    f=64/re;J=flow/AREA;K=J*J*R*T
    c=f*K/(2*D);b=G*height/(R*T*length)
    def ode(x,p):
        numerator=-(c/p[0]+b*p[0])
        return [numerator/(1-K/p[0]**2) if acceleration else numerator]
    solution=solve_ivp(ode,(0,length),[P0],method='DOP853',rtol=3e-13,atol=1e-7,dense_output=True)
    assert solution.success
    return solution


def exponential_reduction(flow,length,height):
    re=flow*D/(AREA*MU);friction=64/re
    c=friction*(flow/AREA)**2*R*T/(2*D)
    s=2*G*height/(R*T)
    mean_factor=1. if s==0 else -math.expm1(-s)/s
    return math.sqrt(P0**2*math.exp(-s)-2*c*length*mean_factor)


def case(n,length,height,flow=.0025):
    ref=continuous_reference(flow,length,height)
    xs=np.linspace(0,length,n+1);ps=ref.sol(xs)[0]
    velocity=flow*R*T/(AREA*ps)
    heats=flow*(np.diff(velocity**2)/2+G*height/n)
    evidence=EvidenceRef(id='authored',citation='Authored ideal isothermal gas, geometry and heat source',origin='synthetic')
    context=RunContext(asset_id='elevated-reference',scenario_id='signed-height-path',clock_id='elapsed',origin='synthetic',evidence=(evidence,))
    q=GasPotentialPathRequest(id='height-momentum-energy',asset_id=context.asset_id,clock_id=context.clock_id,
        fluid='AuthoredIdealGas',backend='HEOS',property_basis_id='independent-ideal-law',diameter_m=D,
        inlet_pressure_Pa_abs=P0,inlet_temperature_K=T,outlet_pressure_Pa_abs=float(ps[-1]),
        segments=tuple(GasPotentialSegment(id=f's{i}',length_m=length/n,end_minus_start_elevation_m=height/n,
            roughness_m=0.,heat_into_fluid_W=float(heats[i]),evidence_id='authored') for i in range(n)),
        pressure_support_Pa_abs=(180000.,220000.),temperature_support_K=(290.,310.),
        positive_mass_flow_support_kg_s=(.002,.0027),initial_mass_flow_kg_s=flow*1.001,
        initial_node_pressures_Pa_abs=tuple(float(p) for p in ps[1:]),initial_node_temperatures_K=(T,)*n,
        expected_owner_pipe_sha256=gas_potential_path_metadata()['owner_pipe_sha256'],
        absolute_momentum_tolerance_Pa=1e-6,absolute_total_energy_tolerance_W=1e-8,
        relative_closure_tolerance=1e-10,maximum_solver_evaluations=1000,
        fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed=True,
        small_segment_property_variation_and_wall_heat_reviewed=True,
        owner_friction_and_common_thermodynamic_reference_reviewed=True,evidence_ids=('authored',))
    return q,context,ps


@pytest.mark.parametrize('height',[-200.,0.,200.])
def test_signed_height_momentum_energy_against_continuous_reference_with_refinement(height):
    errors=[]
    for n in (2,4,8):
        q,c,ps=case(n,80000.,height)
        record=evaluate_gas_potential_path(q,c,property_provider=IdealReferenceGas())
        assert record['status']=='computed_diagnostic',record['failure']
        assert record['failure'] is None
        errors.append(abs(record['mass_flow_kg_s']-.0025))
        assert abs(record['global_total_energy_residual_W'])<1e-8
        for row in record['segment_checks']:
            assert abs(row['momentum_residual_Pa'])<row['momentum_tolerance_Pa']
            assert abs(row['total_energy_residual_W'])<row['total_energy_tolerance_W']
            assert row['gravity_pressure_head_Pa']*height>=0
        assert record['branching_junction_mixing_solved'] is False
        assert max(abs(s['temperature_k']-T) for s in record['states'])<.005
    assert errors[-1]<max(errors[0]/8,1e-10)
    assert errors[-1]/.0025<2e-5


@pytest.mark.parametrize('height',[-300.,-1e-8,0.,1e-8,300.])
def test_exponential_source_reduction_matches_independent_low_mach_ode(height):
    flow=.0025;length=80000.
    reference=continuous_reference(flow,length,height,acceleration=False).y[0,-1]
    exact=exponential_reduction(flow,length,height)
    assert reference==pytest.approx(exact,abs=2e-7)
    full=continuous_reference(flow,length,height).y[0,-1]
    assert abs(full-exact)<.003
