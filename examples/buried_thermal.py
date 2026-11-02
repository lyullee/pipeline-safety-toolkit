"""Original steady-boundary inputs, not soil material recommendations."""

import math

from pipeline_integrity.buried_thermal import INPUT_UNITS, REVIEWS, BuriedThermalRequest, ThermalPipeLayer
from pipeline_integrity.contracts import EvidenceRef, RunContext


def make_cases():
    context = RunContext(
        asset_id="authored_buried_segment",
        scenario_id="steady_soil_reference",
        clock_id="one_steady_boundary",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical steady thermal resistances",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    request = BuriedThermalRequest(
        asset_id=context.asset_id,
        location_id="one_straight_segment",
        clock_id=context.clock_id,
        mode="uniform_fluid_temperature",
        inner_radius_m=0.1,
        length_m=10.0,
        depth_to_pipe_center_m=0.2 * math.cosh(1.0),
        layers=(
            ThermalPipeLayer(
                id="authored_layer",
                material_condition_id="hypothetical_constant_conductivity",
                outer_radius_m=0.2,
                conductivity_W_m_K=math.log(2),
                upstream_contact_resistance_K_m_W=0.0,
                temperature_support_K=(250.0, 400.0),
                evidence_id="authored",
            ),
        ),
        fluid_inlet_temperature_K=320.0,
        surface_plane_temperature_K=300.0,
        soil_conductivity_W_m_K=1.0,
        soil_contact_resistance_K_m_W=0.0,
        inner_film_coefficient_W_m2_K=10.0,
        soil_temperature_support_K=(250.0, 350.0),
        fluid_temperature_support_K=(250.0, 400.0),
        minimum_supported_slenderness=20.0,
        soil_evidence_id="authored",
        film_evidence_id="authored",
        applicability_reviews=dict.fromkeys(REVIEWS, True),
        review_evidence_ids=dict.fromkeys(REVIEWS, "authored"),
        evidence_ids=("authored",),
        input_units=INPUT_UNITS,
        expected_ht_version="1.2.0",
    )
    flow = request.model_copy(
        update={
            "mode": "steady_plug_flow",
            "mass_flow_kg_s": 0.1,
            "specific_heat_J_kg_K": 1000.0,
            "maximum_supported_axial_NTU": 1.0,
            "fluid_property_evidence_id": "authored",
        }
    )
    return {"uniform": request, "flow": flow}, context
