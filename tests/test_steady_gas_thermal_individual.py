"""Independent continuous momentum/enthalpy ODE for the existing gas path."""
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.gas_potential_network import (
    GasPotentialPathRequest,GasPotentialSegment,gas_potential_path_metadata,evaluate_gas_potential_path)
from test_elevated_general_gas_independent import IdealReferenceGas,R,CP,MU,G,D,AREA


def _continuous(flow,length,height,ua,ambient,*,real=False):
    native=None
    if real:
        import CoolProp
        native=CoolProp.AbstractState("HEOS","Nitrogen")
    inlet=(2e6,310.0) if real else (2e5,300.0)
    def state(p,t):
        if native is None:
            rho=p/(R*t)
            return rho,MU,rho/p,-rho/t,0.0,CP
        import CoolProp
        native.update(CoolProp.PT_INPUTS,float(p),float(t))
        return (native.rhomass(),native.viscosity(),
            native.first_partial_deriv(CoolProp.iDmass,CoolProp.iP,CoolProp.iT),
            native.first_partial_deriv(CoolProp.iDmass,CoolProp.iT,CoolProp.iP),
            native.first_partial_deriv(CoolProp.iHmass,CoolProp.iP,CoolProp.iT),native.cpmass())
    def rhs(x,y):
        p,t=y
        rho,mu,rho_p,rho_t,h_p,cp=state(p,t)
        velocity=flow/(AREA*rho)
        vp,vt=-velocity*rho_p/rho,-velocity*rho_t/rho
        reynolds=flow*D/(AREA*mu)
        friction=(64/reynolds if reynolds<2300 else
            1/(-1.8*math.log10((1e-5/(3.7*D))**1.11+6.9/reynolds))**2)
        force=friction*rho*velocity**2/(2*D)
        matrix=np.array(((1+rho*velocity*vp,rho*velocity*vt),(h_p+velocity*vp,cp+velocity*vt)))
        heat_per_mass_length=ua/length*(ambient-t)/flow
        return np.linalg.solve(matrix,(-force-rho*G*height/length,heat_per_mass_length-G*height/length))
    solution=solve_ivp(rhs,(0,length),inlet,method="DOP853",rtol=2e-12,atol=(2e-7,2e-10),dense_output=True)
    assert solution.success
    return solution,inlet


def _case(parts,flow,length,height,ua,ambient,*,real=False):
    reference,inlet=_continuous(flow,length,height,ua,ambient,real=real)
    xs=np.linspace(0,length,parts+1)
    pressures,temperatures=reference.sol(xs)
    evidence=EvidenceRef(id="authored_gas_thermal",citation="Authored gas path heat-bath/geometry reference",
        origin="synthetic")
    context=RunContext(asset_id="thermal_gas_pipe",scenario_id="continuous_ode",clock_id="steady",
        origin="synthetic",evidence=(evidence,))
    q=GasPotentialPathRequest(id="steady_thermal_pipe",asset_id=context.asset_id,clock_id=context.clock_id,
        fluid="Nitrogen" if real else "AuthoredIdealGas",property_basis_id="reviewed_HEOS" if real else "authored_ideal",
        diameter_m=D,inlet_pressure_Pa_abs=inlet[0],inlet_temperature_K=inlet[1],outlet_pressure_Pa_abs=float(pressures[-1]),
        segments=tuple(GasPotentialSegment(id=f"segment_{i}",length_m=length/parts,
            end_minus_start_elevation_m=height/parts,roughness_m=1e-5 if real else 0.0,
            wall_conductance_W_K=ua/parts,ambient_temperature_K=ambient,evidence_id=evidence.id) for i in range(parts)),
        pressure_support_Pa_abs=(inlet[0]*0.7,inlet[0]*1.1),temperature_support_K=(260.0,350.0),
        positive_mass_flow_support_kg_s=(flow*0.8,flow*1.2),initial_mass_flow_kg_s=flow*1.001,
        initial_node_pressures_Pa_abs=tuple(float(p) for p in pressures[1:]),
        initial_node_temperatures_K=tuple(float(t) for t in temperatures[1:]),
        expected_owner_pipe_sha256=gas_potential_path_metadata()["owner_pipe_sha256"],
        absolute_momentum_tolerance_Pa=0.001 if real else 1e-6,
        absolute_total_energy_tolerance_W=1e-5 if real else 1e-8,relative_closure_tolerance=1e-9,
        maximum_solver_evaluations=1000,fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed=True,
        small_segment_property_variation_and_wall_heat_reviewed=True,
        owner_friction_and_common_thermodynamic_reference_reviewed=True,evidence_ids=(evidence.id,))
    record=evaluate_gas_potential_path(q,context,property_provider=None if real else IdealReferenceGas())
    assert record["status"]=="computed_diagnostic",record["failure"]
    assert abs(record["global_total_energy_residual_W"]) < (1e-4 if real else 1e-7)
    for row,left,right in zip(record["segment_checks"],record["states"][:-1],record["states"][1:],strict=True):
        # Compare the explicitly selected heat boundary, not an inferred constant temperature.
        expected=ua/parts*(ambient-(left["temperature_k"]+right["temperature_k"])/2)
        assert row["wall_heat_into_fluid_W"] == pytest.approx(expected,abs=1e-10)
        assert row["total_heat_into_fluid_W"] == pytest.approx(expected)
        assert abs(row["total_energy_residual_W"]) <= row["total_energy_tolerance_W"]
        assert abs(row["momentum_residual_Pa"]) <= row["momentum_tolerance_Pa"]
    return record,reference


@pytest.mark.parametrize("ambient",[280.0,320.0])
@pytest.mark.parametrize("height",[0.0,100.0])
def test_ideal_gas_wall_heat_and_gravity_converge_to_continuous_total_energy_ode(ambient,height):
    errors=[]
    for parts in (2,4,8):
        record,reference=_case(parts,0.0025,40000.0,height,1.0,ambient)
        mass_error=abs(record["mass_flow_kg_s"]-0.0025)/0.0025
        temperature_error=abs(record["states"][-1]["temperature_k"]-reference.y[1,-1])
        errors.append((mass_error,temperature_error))
        assert (record["states"][-1]["temperature_k"]-300)*(ambient-300)>0
    for index in (0,1):
        assert all(a[index]>2.5*b[index]>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1][0]<0.001
    assert errors[-1][1]<0.01


@pytest.mark.parametrize("ua",[0.0,40.0])
def test_real_nitrogen_joule_thomson_and_wall_heat_with_refinement(ua):
    errors=[]
    for parts in (2,4,8):
        record,reference=_case(parts,0.8,1000.0,10.0,ua,285.0,real=True)
        errors.append((abs(record["mass_flow_kg_s"]-0.8)/0.8,
                       abs(record["states"][-1]["temperature_k"]-reference.y[1,-1])))
        assert record["states"][-1]["temperature_k"]<310
        if ua==0:
            # Static h is not constant when kinetic/potential work changes.
            inlet,outlet=record["states"][0],record["states"][-1]
            dv=record["segment_checks"][-1]["outlet_velocity_m_s"]**2-record["segment_checks"][0]["inlet_velocity_m_s"]**2
            assert outlet["enthalpy_j_kg"]-inlet["enthalpy_j_kg"] == pytest.approx(-dv/2-G*10,abs=2e-4)
    assert all(a[0]>2.0*b[0]>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1][0]<1e-4
    assert errors[-1][1]<0.001


def test_actual_joule_thomson_coefficient_matches_independent_isenthalpic_flash_derivative():
    import CoolProp.CoolProp as CP_native
    p,t=2e6,310.0
    enthalpy=CP_native.PropsSI("Hmass","P",p,"T",t,"Nitrogen")
    step=100.0
    numerical=(CP_native.PropsSI("T","P",p+step,"Hmass",enthalpy,"Nitrogen")-
               CP_native.PropsSI("T","P",p-step,"Hmass",enthalpy,"Nitrogen"))/(2*step)
    rho=CP_native.PropsSI("Dmass","P",p,"T",t,"Nitrogen")
    rho_t=CP_native.PropsSI("d(Dmass)/d(T)|P","P",p,"T",t,"Nitrogen")
    cp=CP_native.PropsSI("Cpmass","P",p,"T",t,"Nitrogen")
    independent=(-t*rho_t/rho**2-1/rho)/cp
    assert numerical == pytest.approx(independent,rel=2e-7)
    assert numerical>0


def test_unknown_ambient_is_not_zero_wall_heat():
    with pytest.raises(ValueError,match="ambient"):
        GasPotentialSegment(id="missing_bath",length_m=100.0,end_minus_start_elevation_m=0.0,
            roughness_m=0.0,wall_conductance_W_K=1.0,evidence_id="source")
