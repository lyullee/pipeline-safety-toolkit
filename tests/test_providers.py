import json
from pathlib import Path

import pytest

from pipeline_integrity import create_registry, evaluate
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.thermo import ThermodynamicState


class ConstantDensityDemo:
    def resolve(self, request):
        return ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=request.inputs["P"],
            temperature_k=request.inputs["T"],
            density_kg_m3=2.0,
            enthalpy_j_kg=100.0,
            entropy_j_kg_k=1.0,
            phase="gas",
            viscosity_pa_s=None,
            cp_j_kg_k=None,
            cv_j_kg_k=None,
            conductivity_w_m_k=None,
            speed_of_sound_m_s=None,
            provider_version="1",
            provider_revision="synthetic-test",
        )


def test_provider_replacement_preserves_identity_and_qualification():
    data = json.loads(Path("examples/engineering_cases.json").read_text())
    context = RunContext.model_validate_json(json.dumps(data["context"]))
    engine = create_registry(
        property_provider=ConstantDensityDemo(), provider_id="test_constant", provider_version="1"
    )
    result = engine.evaluate("inventory.uniform", data["cases"]["inventory.uniform"], context)
    assert result.values["mass_kg"] == 200.0
    assert result.values["reference_volume_m3"] == 100.0
    assert result.qualification == "user_model" and "CoolProp" not in result.runtime
    with pytest.raises(ValueError):
        create_registry(property_provider=ConstantDensityDemo())


def test_public_evaluate_survives_submodule_import_order():
    import pipeline_integrity.registry

    data = json.loads(Path("examples/engineering_cases.json").read_text())
    context = RunContext.model_validate_json(json.dumps(data["context"]))
    assert pipeline_integrity.registry.Registry
    assert (
        evaluate("equipment.pump_power", data["cases"]["equipment.pump_power"], context).status == "computed"
    )
