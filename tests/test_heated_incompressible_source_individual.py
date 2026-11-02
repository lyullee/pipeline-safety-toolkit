"""Pressure-work/heat-loss references for the existing steady pipe solver."""
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import heated_transport as ht

_spec=importlib.util.spec_from_file_location(
    "incompressible_heated_example",Path(__file__).parents[1]/"examples/heated_transport.py")
_example=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)
RHO, CAPACITY, MU, FLOW, DIAMETER = 1000.,4000.,.1,.0001,.05


class IncompressibleLiquid:
    """Constant density with u=c*T and h=u+P/rho, including pressure work."""
    def runtime_identity(self):
        return {"provider":"authored_incompressible_pressure_work","version":"1",
                "reference_basis":"u=4000*T; h=u+P/1000; constant rho, viscosity and heat capacity"}

    def resolve(self,q):
        pressure=q.inputs["P"]
        temperature=q.inputs.get("T")
        if temperature is None:
            temperature=(q.inputs["Hmass"]-pressure/RHO)/CAPACITY
        return ht.ThermodynamicState(
            fluid=q.fluid,backend=q.backend,pressure_pa_abs=pressure,temperature_k=temperature,
            density_kg_m3=RHO,enthalpy_j_kg=CAPACITY*temperature+pressure/RHO,
            entropy_j_kg_k=CAPACITY*math.log(temperature/300),phase="liquid",
            viscosity_pa_s=MU,cp_j_kg_k=CAPACITY,cv_j_kg_k=CAPACITY,
            conductivity_w_m_k=None,speed_of_sound_m_s=None,
            provider_version="1",provider_revision="authored_incompressible_limit")


PROVIDER=IncompressibleLiquid()


def run(parts,*,length=100.,conductance=0.,ambient=280.,elevation=0.):
    cells=tuple(ht.HeatedTransportCell(
        id=f"cell_{i}",length_m=length/parts,inner_diameter_m=DIAMETER,
        roughness_m=0.,minor_loss_coefficient=0.,outlet_minus_inlet_elevation_m=elevation/parts,
        conductance_W_K=conductance/parts,ambient_temperature_K=ambient,
        heater_into_fluid_W=0.,evidence_id="authored_liquid_boundary",
    ) for i in range(parts))
    q,c=_example.make_case(property_provider_kind="custom",mass_flow_kg_s=RHO*FLOW,cells=cells,
        reviews=dict.fromkeys(ht.REVIEWS,True),
        review_evidence_ids=dict.fromkeys(ht.REVIEWS,"authored_liquid_boundary"))
    q=q.model_copy(update={"owner_pins":ht.native_backend_metadata(q,property_provider=PROVIDER)})
    c=c.model_copy(update={"metadata":{"heated_transport_boundary":ht.boundary_metadata(q)}})
    result=ht.evaluate_heated_transport(q,c,property_provider=PROVIDER)
    assert result["status"]=="computed_diagnostic",result["notes"]
    return q,result["values"]


def loss(length):
    return 128*MU*length*FLOW/(math.pi*DIAMETER**4)


@pytest.mark.parametrize("length,parts",[(10.,1),(100.,1),(100.,4)])
def test_adiabatic_pressure_work_increases_internal_energy_not_total_enthalpy(length,parts):
    q,v=run(parts,length=length)
    expected_pressure_loss=loss(length)
    expected_rise=expected_pressure_loss/(RHO*CAPACITY)
    assert v["outlet"]["temperature_k"]==pytest.approx(300+expected_rise,abs=2e-11)
    assert v["outlet"]["pressure_pa_abs"]==pytest.approx(1e6-expected_pressure_loss,abs=2e-8)
    assert v["total_heat_into_fluid_W"]==0.
    assert v["global_enthalpy_flux_change_W"]==pytest.approx(0.,abs=2e-8)
    assert v["summed_native_dissipated_power_W"]==pytest.approx(FLOW*expected_pressure_loss,rel=2e-13)


@pytest.mark.parametrize("ambient",[280.,320.])
def test_wall_loss_and_distributed_friction_heating_converge_to_exact_energy_ode(ambient):
    conductance=100.
    capacity_rate=RHO*FLOW*CAPACITY
    dissipation=FLOW*loss(100.)
    equilibrium=ambient+dissipation/conductance
    expected=equilibrium+(300-equilibrium)*math.exp(-conductance/capacity_rate)
    errors=[]
    for parts in (2,4,8,16):
        _,v=run(parts,conductance=conductance,ambient=ambient)
        actual=v["outlet"]["temperature_k"]
        errors.append(abs(actual-expected))
        assert abs(v["global_reduced_energy_residual_W"])<2e-7
    assert all(coarse>fine>0 for coarse,fine in zip(errors,errors[1:]))
    assert all(coarse/fine>1.9 for coarse,fine in zip(errors,errors[1:]))
    assert errors[-1]<.001


def test_elevation_pressure_work_cancels_gravity_for_incompressible_internal_temperature():
    _,flat=run(4)
    _,raised=run(4,elevation=12.)
    assert raised["outlet"]["temperature_k"]==pytest.approx(flat["outlet"]["temperature_k"],abs=2e-11)
    assert flat["outlet"]["pressure_pa_abs"]-raised["outlet"]["pressure_pa_abs"]==pytest.approx(RHO*9.80665*12.,abs=2e-8)
