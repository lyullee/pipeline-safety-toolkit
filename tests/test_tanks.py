"""Static tank references, provider phase semantics and explicit geometry/inventory limits."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.tanks import (
    AxisymmetricTank,
    CalibratedTankCurve,
    DeclaredTankDensity,
    PropertyTankDensity,
    TankHead,
    TankInventoryRequest,
    TankObservation,
    evaluate_tank_inventory,
    lh2dt_tank_geometry,
)
from pipeline_integrity.thermo import StateRequest

CONTEXT = RunContext(
    asset_id="tank",
    scenario_id="authored",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(EvidenceRef(id="e", citation="Original tank reference", origin="synthetic"),),
)


def geometry(**changes):
    return AxisymmetricTank(
        **(dict(inner_diameter_m=2.0, straight_length_m=4.0, horizontal=False, evidence_id="e") | changes)
    )


def observation(value=1.0, **changes):
    return TankObservation(
        **(
            dict(
                tank_id="tank",
                clock_id="elapsed",
                time_s=0.0,
                quantity="level_from_internal_bottom",
                value=value,
                unit="m",
                evidence_id="e",
            )
            | changes
        )
    )


def request(**changes):
    return TankInventoryRequest(**(dict(geometry=geometry(), observation=observation()) | changes))


def test_vertical_cylinder_level_volume_and_declared_phase_masses():
    density = DeclaredTankDensity(
        phase="liquid",
        fluid="declared_water",
        density_kg_m3=1000.0,
        property_basis="authored uniform density",
        evidence_id="e",
    )
    gas = DeclaredTankDensity(
        phase="gas",
        fluid="declared_air",
        density_kg_m3=1.0,
        property_basis="authored uniform density",
        evidence_id="e",
    )
    result = evaluate_tank_inventory(request(liquid_density=density, headspace_density=gas), CONTEXT)
    assert result["status"] == result["mass_status"] == "computed"
    assert result["values"]["liquid_volume_m3"] == pytest.approx(math.pi)
    assert result["values"]["total_fluid_mass_kg"] == pytest.approx(1003 * math.pi)
    assert not result["equilibrium_or_tank_dynamics_solved"] and not result["field_calibrated"]


@pytest.mark.parametrize("height,fraction", [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0)])
def test_horizontal_cylinder_empty_half_and_full_references(height, fraction):
    result = evaluate_tank_inventory(
        request(geometry=geometry(horizontal=True), observation=observation(height)), CONTEXT
    )
    assert result["values"]["fill_volume_fraction"] == pytest.approx(fraction)
    assert (
        result["values"]["liquid_volume_m3"] + result["values"]["headspace_volume_m3"]
        == result["values"]["total_internal_volume_m3"]
    )


@pytest.mark.parametrize("kind,factor", [("conical", 1 / 3), ("ellipsoidal", 2 / 3)])
def test_full_head_capacity_against_independent_closed_forms(kind, factor):
    head = TankHead(kind=kind, depth_m=0.4)
    shape = geometry(side_a=head, side_b=head)
    result = evaluate_tank_inventory(request(geometry=shape, observation=observation(4.8)), CONTEXT)
    assert result["values"]["total_internal_volume_m3"] == pytest.approx(math.pi * (4 + 2 * 0.4 * factor))


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("kind", ["spherical", "torispherical", "ellipsoidal", "conical"])
def test_level_volume_inverse_on_explicit_headed_shapes(kind, horizontal):
    head = TankHead(
        kind=kind,
        **(
            {"dish_radius_ratio": 1.0, "knuckle_radius_ratio": 0.08}
            if kind == "torispherical"
            else {"depth_m": 0.4}
        ),
    )
    shape = geometry(horizontal=horizontal, side_a=head, side_b=head)
    first = evaluate_tank_inventory(request(geometry=shape, observation=observation(0.65)), CONTEXT)
    second = evaluate_tank_inventory(
        request(
            geometry=shape,
            observation=observation(
                first["values"]["liquid_volume_m3"], quantity="liquid_volume", unit="m^3"
            ),
        ),
        CONTEXT,
    )
    assert first["status"] == second["status"] == "computed"
    assert second["values"]["liquid_level_m"] == pytest.approx(0.65, abs=1e-8)


def test_custom_calibration_is_monotone_interpolation_with_explicit_endpoints():
    curve = CalibratedTankCurve(heights_m=(0.0, 1.0, 2.0), volumes_m3=(0.0, 1.0, 4.0), evidence_id="e")
    result = evaluate_tank_inventory(request(geometry=curve, observation=observation(1.5)), CONTEXT)
    assert result["values"]["liquid_volume_m3"] == 2.5
    assert result["geometry_provider"]["id"] == "reviewed_piecewise_linear_calibration"
    assert result["mass_status"] == "needs_data" and result["values"]["total_fluid_mass_kg"] is None


def test_known_full_headed_tank_uses_the_qualified_exact_boundary_without_rounding_nearby_inputs():
    head = TankHead(kind="conical", depth_m=0.4)
    full = evaluate_tank_inventory(
        request(geometry=geometry(horizontal=True, side_a=head, side_b=head), observation=observation(2.0)),
        CONTEXT,
    )
    assert full["status"] == "computed" and full["values"]["headspace_volume_m3"] == 0.0
    assert "native_boundary_volume_m3" in full["geometry_checks"]
    assert full["values"]["liquid_volume_m3"] == full["values"]["total_internal_volume_m3"]


@pytest.mark.parametrize("value", [-0.1, 4.01])
def test_level_outside_the_tank_is_not_silently_clipped(value):
    result = evaluate_tank_inventory(request(observation=observation(value)), CONTEXT)
    assert result["status"] == "out_of_scope" and not result["values"]


def test_unknown_headspace_mass_is_not_assumed_zero():
    liquid = DeclaredTankDensity(
        phase="liquid", fluid="water", density_kg_m3=1000.0, property_basis="reference", evidence_id="e"
    )
    result = evaluate_tank_inventory(request(liquid_density=liquid), CONTEXT)
    assert result["values"]["liquid_mass_kg"] == pytest.approx(1000 * math.pi)
    assert result["values"]["gas_mass_kg"] is None and result["values"]["total_fluid_mass_kg"] is None
    full = evaluate_tank_inventory(request(liquid_density=liquid, observation=observation(4.0)), CONTEXT)
    assert full["values"]["gas_mass_kg"] == 0.0 and full["mass_status"] == "computed"


def test_coolprop_liquid_and_gas_use_separate_reviewed_uniform_states():
    liquid = PropertyTankDensity(
        state=StateRequest(fluid="Water", inputs={"P": 101325.0, "T": 293.15}), evidence_id="e"
    )
    gas = PropertyTankDensity(
        state=StateRequest(fluid="Air", inputs={"P": 101325.0, "T": 293.15}), evidence_id="e"
    )
    result = evaluate_tank_inventory(request(liquid_density=liquid, headspace_density=gas), CONTEXT)
    assert result["mass_status"] == "computed"
    assert result["phase_density_states"]["liquid"]["phase"] == "liquid"
    assert result["phase_density_states"]["gas"]["phase"] in {"gas", "supercritical_gas"}


def test_saturated_hydrogen_endpoints_are_distinct_from_bulk_two_phase_density():
    def state(q):
        return PropertyTankDensity(
            state=StateRequest(fluid="Hydrogen", inputs={"P": 101325.0, "Q": q}, single_phase_required=False),
            evidence_id="e",
        )

    result = evaluate_tank_inventory(
        request(liquid_density=state(0.0), headspace_density=state(1.0)), CONTEXT
    )
    assert result["mass_status"] == "computed"
    bulk = evaluate_tank_inventory(request(liquid_density=state(0.5)), CONTEXT)
    assert bulk["mass_status"] == "needs_data" and bulk["values"]["liquid_mass_kg"] is None


def test_wrong_phase_density_does_not_become_liquid_mass():
    gas = DeclaredTankDensity(
        phase="gas", fluid="air", density_kg_m3=1.0, property_basis="reference", evidence_id="e"
    )
    result = evaluate_tank_inventory(request(liquid_density=gas), CONTEXT)
    assert result["mass_status"] == "needs_data" and result["issues"] == [
        "declared_density_phase_mismatch:liquid"
    ]


def test_bad_evidence_clock_and_nested_python_boolean_fail_before_model_execution():
    spec = request()
    for context in [
        CONTEXT.model_copy(update={"evidence": ()}),
        CONTEXT.model_copy(update={"clock_id": "other"}),
    ]:
        with pytest.raises(ValueError):
            evaluate_tank_inventory(spec, context)
    broken = spec.model_copy(update={"geometry": spec.geometry.model_copy(update={"inner_diameter_m": True})})
    with pytest.raises(ValueError):
        evaluate_tank_inventory(broken, CONTEXT)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "conical"},
        {"kind": "flat", "depth_m": 0.4},
        {"kind": "torispherical", "dish_radius_ratio": 1.0},
    ],
)
def test_missing_or_hidden_head_parameters_are_not_replaced_by_vendor_defaults(changes):
    with pytest.raises(ValueError):
        TankHead(**changes)


def test_vertical_guppy_and_unsupported_receiver_shapes_are_explicit():
    with pytest.raises(ValueError, match="vertical guppy"):
        geometry(side_a=TankHead(kind="guppy", depth_m=0.4))
    with pytest.raises(ValueError, match="receiver requires horizontal"):
        lh2dt_tank_geometry(geometry())


@pytest.mark.parametrize(
    "heights,volumes", [((0.0, 0.0), (0.0, 1.0)), ((0.0, 1.0), (0.0, 0.0)), ((1.0, 2.0), (0.0, 1.0))]
)
def test_unresolved_calibration_cannot_be_sorted_or_normalized(heights, volumes):
    with pytest.raises(ValueError):
        CalibratedTankCurve(heights_m=heights, volumes_m3=volumes, evidence_id="e")
