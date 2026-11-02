"""Inventory/reference-volume conversion with a synthetic covolume gas EOS.

The property fixture supplies a thermodynamically consistent p(v-b)=RT gas.
These checks concern inventory conversion, not an empirical natural-gas EOS.
"""
import math

import pytest

from pipeline_integrity.models.thermal import (
    Inventory, InventorySegment, SegmentedInventory, inventory, segmented_inventory,
)
from pipeline_integrity.thermo import ThermodynamicState

R, CV = 300.0, 750.0


class CovolumeGas:
    def __init__(self, b):
        self.b = b

    def resolve(self, request):
        p, t = request.inputs["P"], request.inputs["T"]
        volume = R*t/p + self.b
        return ThermodynamicState(
            fluid=request.fluid, backend=request.backend, pressure_pa_abs=p,
            temperature_k=t, density_kg_m3=1/volume,
            enthalpy_j_kg=CV*t+p*volume,
            entropy_j_kg_k=CV*math.log(t/300)+R*math.log((volume-self.b)/0.9),
            phase="gas", viscosity_pa_s=None, cp_j_kg_k=CV+R, cv_j_kg_k=CV,
            conductivity_w_m_k=None,
            speed_of_sound_m_s=math.sqrt((CV+R)/CV*R*t)/(1-self.b/volume),
            provider_version="authored-covolume-gas-v1", provider_revision="analytic",
        )


@pytest.mark.parametrize("b", [0.0, 0.003, 0.01])
def test_supplied_mean_state_reference_volume_includes_both_z_factors(b):
    p, t, pipe_volume, pb, tb = 7e6, 345.0, 13.0, 101325.0, 288.15
    out = inventory(Inventory(
        fluid="AuthoredGas", pressure_pa_abs=p, temperature_k=t,
        internal_volume_m3=pipe_volume, reference_pressure_pa_abs=pb,
        reference_temperature_k=tb,
    ), property_provider=CovolumeGas(b)).values
    z, zb = 1+b*p/(R*t), 1+b*pb/(R*tb)
    expected = pipe_volume*(p/pb)*(tb/t)*(zb/z)
    assert out["mass_kg"] == pytest.approx(p*pipe_volume/(z*R*t), rel=3e-14)
    assert out["reference_volume_m3"] == pytest.approx(expected, rel=3e-14)
    if b:
        ideal_shortcut = pipe_volume*(p/pb)*(tb/t)
        assert abs(expected/ideal_shortcut-1) > 0.1


@pytest.mark.parametrize("b", [0.003, 0.01])
def test_piecewise_state_sum_preserves_mass_and_uniform_segment_refinement(b):
    rows = [(2e6, 310.0, 2.0), (7e6, 360.0, 3.5), (4e6, 330.0, 0.7)]
    pb, tb = 101325.0, 288.15

    def calc(states):
        return segmented_inventory(SegmentedInventory(
            fluid="AuthoredGas", segments=tuple(InventorySegment(
                id=f"segment_{i}", pressure_pa_abs=p, temperature_k=t,
                internal_volume_m3=v,
            ) for i, (p,t,v) in enumerate(states)),
            reference_pressure_pa_abs=pb, reference_temperature_k=tb,
        ), property_provider=CovolumeGas(b)).values

    out = calc(rows)
    expected_mass = math.fsum(p*v/(R*t+b*p) for p,t,v in rows)
    expected_volume = math.fsum(
        v*(p/pb)*(tb/t)*(1+b*pb/(R*tb))/(1+b*p/(R*t)) for p,t,v in rows
    )
    assert out["total_mass_kg"] == pytest.approx(expected_mass, rel=3e-14)
    assert out["reference_volume_m3"] == pytest.approx(expected_volume, rel=3e-14)
    split = [(p,t,v/4) for p,t,v in rows for _ in range(4)]
    refined = calc(split)
    for key in ("total_mass_kg", "reference_volume_m3", "total_internal_energy_j"):
        assert refined[key] == pytest.approx(out[key], rel=3e-14)
