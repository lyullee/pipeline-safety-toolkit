"""Deferred shared kinematic/head/pressure regressions; no execution during coding."""

import math

import pytest

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.models.hydraulics import (
    LiquidHeadLoss,
    LiquidPressureLoss,
    liquid_head_loss,
    liquid_pressure_loss,
)
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleRequest,
    JointCase,
    UncertainVariable,
    evaluate_ensemble,
    generate_ensemble,
    registry_node,
)


def head_case(**changes):
    return LiquidHeadLoss(
        **(
            dict(
                diameter_m=0.02,
                length_m=10.0,
                roughness_m=0.0,
                volume_flow_m3_s=1e-5,
                kinematic_viscosity_m2_s=1e-6,
            )
            | changes
        )
    )


def test_density_free_laminar_head_matches_independent_hagen_poiseuille_reference():
    q = head_case()
    r = liquid_head_loss(q)
    expected = (
        128
        * q.kinematic_viscosity_m2_s
        * q.length_m
        * q.volume_flow_m3_s
        / (math.pi * q.gravity_m_s2 * q.diameter_m**4)
    )
    assert r.status == "computed"
    assert r.values["required_inlet_minus_outlet_head_m"] == pytest.approx(expected, rel=1e-12)
    assert set(r.values) == {
        "velocity_m_s",
        "reynolds",
        "darcy_friction_factor",
        "dissipative_head_loss_m",
        "static_head_change_m",
        "required_inlet_minus_outlet_head_m",
    }
    assert not any("pressure" in key or "power" in key or "mass" in key for key in r.values)


@pytest.mark.parametrize("flow", [1e-5, -1e-5, 0.0, 0.002])
@pytest.mark.parametrize("density", [850.0, 1000.0])
def test_supplied_density_pressure_contract_is_consistent_with_shared_head_kernel(flow, density):
    h = head_case(
        volume_flow_m3_s=flow,
        roughness_m=1e-5,
        minor_loss_coefficient=1.2,
        outlet_minus_inlet_elevation_m=2.0,
    )
    pressure = LiquidPressureLoss.model_validate(
        h.model_dump(mode="python", exclude={"kinematic_viscosity_m2_s"})
        | {"density_kg_m3": density, "viscosity_pa_s": density * h.kinematic_viscosity_m2_s}
    )
    hr, pr = liquid_head_loss(h), liquid_pressure_loss(pressure)
    assert hr.status == pr.status == "computed"
    assert pr.values["required_inlet_minus_outlet_pa"] == pytest.approx(
        density * h.gravity_m_s2 * hr.values["required_inlet_minus_outlet_head_m"], rel=1e-12
    )
    assert pr.values["reynolds"] == pytest.approx(hr.values["reynolds"], rel=1e-12)
    assert pr.values["dissipated_power_w"] == pytest.approx(
        abs(pr.values["dissipative_pressure_loss_pa"] * flow)
    )
    assert set(pr.values) == {
        "velocity_m_s",
        "reynolds",
        "darcy_friction_factor",
        "dissipative_pressure_loss_pa",
        "static_pressure_change_pa",
        "required_inlet_minus_outlet_pa",
        "dissipated_power_w",
    }


def test_signed_friction_and_zero_flow_keep_geometric_elevation_direction():
    forward = liquid_head_loss(head_case(outlet_minus_inlet_elevation_m=-2.0))
    reverse = liquid_head_loss(head_case(volume_flow_m3_s=-1e-5, outlet_minus_inlet_elevation_m=-2.0))
    stopped = liquid_head_loss(head_case(volume_flow_m3_s=0.0, outlet_minus_inlet_elevation_m=-2.0))
    assert forward.values["dissipative_head_loss_m"] == -reverse.values["dissipative_head_loss_m"]
    assert forward.values["static_head_change_m"] == reverse.values["static_head_change_m"] == -2.0
    assert stopped.values["required_inlet_minus_outlet_head_m"] == -2.0
    assert stopped.values["reynolds"] == stopped.values["darcy_friction_factor"] == 0.0


def test_transition_policy_is_the_same_shared_owner_domain():
    q = head_case()
    flow = 3000 * q.kinematic_viscosity_m2_s / q.diameter_m * math.pi * q.diameter_m**2 / 4
    r = liquid_head_loss(head_case(volume_flow_m3_s=flow))
    assert r.status == "out_of_scope" and r.values == {}
    assert (
        liquid_head_loss(
            head_case(volume_flow_m3_s=flow, transition_policy="use_declared_correlation")
        ).status
        == "computed"
    )


def test_turbulent_head_uses_existing_darcy_colebrook_owner():
    q = head_case(volume_flow_m3_s=0.002, roughness_m=2e-5)
    r = liquid_head_loss(q)
    fd, reynolds = r.values["darcy_friction_factor"], r.values["reynolds"]
    residual = 1 / math.sqrt(fd) + 2 * math.log10(
        q.roughness_m / q.diameter_m / 3.7 + 2.51 / reynolds / math.sqrt(fd)
    )
    assert abs(residual) < 1e-10


def test_head_does_not_invent_density_and_revalidates_copied_inputs():
    with pytest.raises(ValueError):
        head_case(density_kg_m3=1000.0)
    with pytest.raises(ValueError):
        head_case(viscosity_pa_s=0.001)
    with pytest.raises(ValueError):
        liquid_head_loss(head_case().model_copy(update={"kinematic_viscosity_m2_s": float("nan")}))
    with pytest.raises(ValueError):
        head_case(roughness_m=0.01)


def test_mapping_specific_overflow_withholds_only_unrepresentable_outputs():
    h = head_case(gravity_m_s2=1e-320)
    assert liquid_head_loss(h).status == "out_of_scope"
    p = LiquidPressureLoss.model_validate(
        h.model_dump(mode="python", exclude={"kinematic_viscosity_m2_s"})
        | {"density_kg_m3": 1000.0, "viscosity_pa_s": 0.001}
    )
    assert liquid_pressure_loss(p).status == "computed"
    assert liquid_head_loss(head_case(diameter_m=1e-300, volume_flow_m3_s=1.0)).status == "out_of_scope"


def test_known_zero_flow_static_mapping_does_not_require_representable_viscosity_ratio():
    p = LiquidPressureLoss(
        diameter_m=0.02,
        length_m=10.0,
        roughness_m=0.0,
        volume_flow_m3_s=0.0,
        density_kg_m3=1e100,
        viscosity_pa_s=1e-300,
        outlet_minus_inlet_elevation_m=2.0,
    )
    r = liquid_pressure_loss(p)
    assert r.status == "computed"
    assert r.values["dissipative_pressure_loss_pa"] == r.values["dissipated_power_w"] == 0.0
    assert r.values["static_pressure_change_pa"] == pytest.approx(p.density_kg_m3 * p.gravity_m_s2 * 2.0)


def test_density_free_head_is_registered_and_uses_shared_uncertainty_engine():
    registry = create_registry()
    model = registry.describe("hydraulics.liquid_head_loss")
    assert model["model"]["output_units"]["required_inlet_minus_outlet_head_m"] == "m"
    assert "density_kg_m3" not in model["input_schema"]["properties"]
    assert "viscosity_pa_s" not in model["input_schema"]["properties"]

    context = RunContext(
        asset_id="head_line",
        scenario_id="joint_head_scenarios",
        clock_id="not_a_time_axis",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(id=name, citation="Authored density-free head scenario evidence", origin="synthetic")
            for name in ("design", "flow", "case_low", "case_high", "head_metric")
        ),
    )
    variable = UncertainVariable(id="flow", unit="m3/s", layer="input", evidence_id="flow")
    design = generate_ensemble(
        EnsembleRequest(
            id="head_scenario_set",
            variables=(variable,),
            method="joint_cases",
            interpretation="scenario",
            dependence="paired_rows",
            evidence_id="design",
            definition_reviewed=True,
            cases=(
                JointCase(id="low", weight=0.5, values={"flow": 5e-6}, evidence_id="case_low"),
                JointCase(id="high", weight=0.5, values={"flow": 1e-5}, evidence_id="case_high"),
            ),
        ),
        context,
    )
    node = registry_node(
        registry,
        id="liquid_head",
        model_id="hydraulics.liquid_head_loss",
        base_inputs=head_case().model_dump(mode="json"),
        bindings={"flow": "volume_flow_m3_s"},
        variables=(variable,),
        metrics=(
            EnsembleMetric(
                id="required_inlet_minus_outlet_head_m",
                unit="m",
                meaning="quantity",
                evidence_id="head_metric",
                support_lower=0.0,
            ),
        ),
    )
    result = evaluate_ensemble(design, (node,), quantiles=(0.5,))
    summary = result["summaries"]["liquid_head"]["required_inlet_minus_outlet_head_m"]
    assert result["status"] == "computed"
    assert summary["unit"] == "m" and summary["known_weight"] == 1.0
    assert summary["full_design_mean"] is not None
    assert not result["observed_failure_rate_inferred"]
