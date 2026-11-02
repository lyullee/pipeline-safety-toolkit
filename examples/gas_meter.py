"""Authored meter conditions; reference pressure/temperature are explicit choices."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_meter import (
    INPUT_UNITS,
    REVIEWS,
    GasComponent,
    GasMeterGeometry,
    GasMeterRequest,
    GasMeterSupport,
    GasVolumeReference,
    boundary_metadata,
    evaluate_gas_meter,
)


def make_case(*, reviewed=False, mode="specified_pair", meter_type="unspecified meter", components=None):
    if components is None:
        components = (GasComponent(fluid="Nitrogen", mole_fraction=1.0),)
    meaning = (
        "cone_end_diameter"
        if "cone" in meter_type
        else "wedge_flow_height"
        if "wedge" in meter_type
        else "circular_throat_diameter"
    )
    secondary = 0.075 if meaning == "cone_end_diameter" else 0.03 if meaning == "wedge_flow_height" else 0.05
    taps = (
        "flange"
        if meter_type in {"Miller eccentric orifice", "Miller segmental orifice", "segmental orifice"}
        else "corner"
        if "orifice" in meter_type
        else None
    )
    position = "180 degree" if meter_type == "Miller eccentric orifice" else None
    request = GasMeterRequest(
        asset_id="authored-meter-line",
        meter_id="authored-meter",
        location_id="authored-taps",
        clock_id="authored-elapsed",
        event_id="authored-gas-meter",
        time_s=0.0,
        clock_offset_s=10.0,
        nominal_positive_source_port="port-A",
        nominal_positive_target_port="port-B",
        upstream_port_id="port-A",
        downstream_port_id="port-B",
        geometry=GasMeterGeometry(
            native_meter_type=meter_type,
            inner_pipe_diameter_m=0.1,
            secondary_dimension_m=secondary,
            secondary_dimension_meaning=meaning,
            taps=taps,
            tap_position=position,
            tap_description="Authored tap selector; no facility or standards qualification",
        ),
        components=components,
        volume_basis="same_composition_dry",
        upstream_pressure_pa_abs=500000.0,
        downstream_pressure_pa_abs=495000.0,
        upstream_temperature_K=300.0,
        coefficient_mode=mode,
        specified_discharge_coefficient=0.7 if mode == "specified_pair" else None,
        specified_expansibility_factor=0.99 if mode == "specified_pair" else None,
        exponent_basis="local_isentropic_rho_a2_over_P",
        reference=GasVolumeReference(
            pressure_pa_abs=101325.0,
            temperature_K=293.15,
            volume_basis="same_composition_dry",
            evidence_id="authored",
        ),
        support=GasMeterSupport(
            beta_range=(0.05, 0.95),
            reynolds_range=(0.0, 1e9),
            upstream_pressure_range_pa_abs=(50000.0, 3000000.0),
            upstream_temperature_range_K=(270.0, 340.0),
            maximum_pressure_drop_fraction=0.2,
            maximum_upstream_mach=0.3,
            evidence_id="authored",
        ),
        geometry_evidence_id="authored",
        observation_evidence_id="authored",
        composition_evidence_id="authored",
        coefficient_evidence_id="authored",
        evidence_ids=("authored",),
        applicability_reviews={key: True for key in REVIEWS} if reviewed else {},
        review_evidence_ids={key: "authored" for key in REVIEWS} if reviewed else {},
        input_units=INPUT_UNITS,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored-numerical-meter",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original geometry, gas composition, coefficient and moderate-pressure numerical conditions",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"gas_meter_boundary": boundary_metadata(request)},
    )
    return request, context


if __name__ == "__main__":
    print(evaluate_gas_meter(*make_case())["status"])
