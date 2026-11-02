"""Source-pinned guard: trajectory screening is not a crack-arrest physics solver.

This test intentionally awards no physical qualification. The current module needs
an externally computed trajectory and has no executable propagation/decompression
model to compare against an independent toughness or energy oracle.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pipeline_integrity.fracture_arrest import assess_fracture_arrest


EXPECTED_MODULE_SHA256 = "ef609e4c444376229026006d42f3ad056e91d780202c06ac8005657f605d5657"
ROOT = Path(__file__).parents[1]
spec = spec_from_file_location("arrest_qualification_fixture", ROOT / "examples/fracture_arrest.py")
fixture = module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_no_executable_physics_solver_withholds_arrest_qualification():
    request, context, _ = fixture.make_case()

    result = assess_fracture_arrest(request, context)

    assert result["module_sha256"] == EXPECTED_MODULE_SHA256
    assert result["status"] == "needs_data"
    assert "coupled_dynamic_decompression_and_crack_driving_force" in result["missing"]
    assert result["trajectory"] is None
    assert result["point_comparisons"] == []
    assert result["dynamic_arrest_proven"] is False
    assert result["continuous_hold_proven"] is False
    assert result["field_or_code_acceptance"] is False
    assert result["failure_probability"] is None
