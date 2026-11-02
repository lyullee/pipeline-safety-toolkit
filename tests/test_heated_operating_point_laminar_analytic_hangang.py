"""Independent laminar-flow oracle for a heated-line pressure operating point.

The liquid properties are a synthetic constant-property test double. The
closed-form pressure-flow relation is Poiseuille flow, not measured data.
"""

import math
import runpy

import pytest

from pipeline_integrity import heated_transport as heated
from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.heated_operating_point import (
    HeatedOperatingPointRequest,
    evaluate_heated_operating_point,
)


EXAMPLE = runpy.run_path("examples/heated_transport.py")


class ConstantLiquid:
    def runtime_identity(self):
        return {
            "provider": "synthetic_constant_liquid",
            "version": "1",
            "reference_basis": "h=4000*T J/kg for this authored test only",
        }

    def resolve(self, request):
        temperature = request.inputs.get("T")
        if temperature is None:
            temperature = request.inputs["Hmass"] / 4000.0
        return heated.ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=request.inputs["P"],
            temperature_k=temperature,
            density_kg_m3=1000.0,
            enthalpy_j_kg=4000.0 * temperature,
            entropy_j_kg_k=0.0,
            phase="liquid",
            viscosity_pa_s=0.001,
            cp_j_kg_k=4000.0,
            cv_j_kg_k=4000.0,
            conductivity_w_m_k=None,
            speed_of_sound_m_s=None,
            provider_version="1",
            provider_revision="authored_test",
        )


def test_pressure_root_matches_closed_form_laminar_flow_rate():
    provider = ConstantLiquid()
    transport, context = EXAMPLE["make_case"](property_provider_kind="custom")
    transport = transport.model_copy(
        update={
            "cells": (transport.cells[0],),
            "mass_flow_kg_s": 0.002,
            "reviews": dict.fromkeys(heated.REVIEWS, True),
            "review_evidence_ids": dict.fromkeys(heated.REVIEWS, transport.evidence_ids[0]),
        }
    )
    transport = transport.model_copy(
        update={"owner_pins": heated.native_backend_metadata(transport, property_provider=provider)}
    )
    context = context.model_copy(
        update={"metadata": {"heated_transport_boundary": heated.boundary_metadata(transport)}}
    )

    mass_flow_reference = 0.002
    density_kg_m3 = 1000.0
    viscosity_pa_s = 0.001
    length_m = transport.cells[0].length_m
    diameter_m = transport.cells[0].inner_diameter_m
    inlet_pressure_pa = transport.inlet.inputs["P"]
    pressure_drop_pa = (
        128.0 * viscosity_pa_s * length_m * mass_flow_reference
        / (math.pi * diameter_m**4 * density_kg_m3)
    )
    target_pressure_pa = inlet_pressure_pa - pressure_drop_pa
    mapping = EvidenceRef(
        id="pressure-flow-mapping",
        citation="Synthetic fixed-pressure boundary mapping",
        origin="synthetic",
        license_id="MIT",
        sha256="c" * 64,
    )
    context = context.model_copy(update={"evidence": context.evidence + (mapping,)})
    request = HeatedOperatingPointRequest(
        id="synthetic-laminar-operating-root",
        transport=transport,
        downstream_pressure_pa_abs=target_pressure_pa,
        mass_flow_bracket_kg_s=(0.001, 0.003),
        mass_flow_absolute_tolerance_kg_s=1e-10,
        pressure_absolute_tolerance_Pa=1e-4,
        pressure_relative_tolerance=0.0,
        mapping_review_evidence_id=mapping.id,
        diagnostic_model_and_selected_bracket_reviewed=True,
    )

    record = evaluate_heated_operating_point(request, context, property_provider=provider)
    assert record["status"] == "computed_diagnostic", record["issue"]
    assert record["mass_flow_kg_s"] == pytest.approx(mass_flow_reference, abs=1e-9)
    assert record["transport_result"]["values"]["outlet"]["pressure_pa_abs"] == pytest.approx(
        target_pressure_pa, abs=1e-4
    )
    assert record["trials"][-1]["pressure_residual_Pa"] == pytest.approx(0.0, abs=1e-4)
    assert record["global_uniqueness_proved"] is False
