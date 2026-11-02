"""Synthetic quadrature oracle for thermal section projection into a frame.

The four-point square section and its temperature gradient are authored
mathematical inputs, not measured material or plant observations.
"""

import runpy

import pytest

from pipeline_integrity.structural import FrameCombination, FrameSection
from pipeline_integrity.thermal_expansion import ExpansionCurve, ExpansionKnot
from pipeline_integrity.structural_thermal_section import (
    ThermalFiber,
    ThermalSectionField,
    ThermalSectionRequest,
    evaluate_thermal_section_frame,
)


EXAMPLE = runpy.run_path("examples/pipe_frame.py")


def _synthetic_case():
    frame = EXAMPLE["make_case"]()
    section = FrameSection(
        id="quadrature-square",
        area_m2=4.0,
        iy_m4=4.0,
        iz_m4=4.0,
        torsion_constant_m4=8.0,
        evidence_id="reference",
    )
    member = frame.members[0].model_copy(update={"section_id": section.id})
    frame = frame.model_copy(
        update={
            "members": (member,),
            "sections": (section,),
            "load_cases": ("thermal",),
            "node_loads": (),
            "combinations": (
                FrameCombination(id="free-thermal", factors={"thermal": 1.0}, evidence_id="reference"),
            ),
        }
    )
    curve = ExpansionCurve(
        id="synthetic-constant-expansion",
        material_id=member.material_id,
        knots=(
            ExpansionKnot(temperature_K=280.0, tangent_expansion_per_K=10e-6),
            ExpansionKnot(temperature_K=500.0, tangent_expansion_per_K=10e-6),
        ),
        evidence_id="reference",
        tangent_small_engineering_strain_basis_reviewed=True,
    )
    fibers = tuple(
        ThermalFiber(
            id=f"fiber-{y:+g}-{z:+g}",
            y_m=y,
            z_m=z,
            quadrature_area_m2=1.0,
            temperature_K=400.0 if z > 0 else 300.0,
        )
        for y, z in ((-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0))
    )
    thermal_field = ThermalSectionField(
        id="linear-z-temperature-gradient",
        member_id="pipe",
        case_id="thermal",
        expansion_curve=curve,
        stress_free_temperature_K=300.0,
        fibers=fibers,
        plane_sections_uniform_elasticity_and_axial_temperature_reviewed=True,
        evidence_id="reference",
    )
    request = ThermalSectionRequest(id="synthetic-section-gradient", frame=frame, fields=(thermal_field,))
    return request, EXAMPLE["context"]()


def test_linear_temperature_gradient_projects_to_exact_mean_strain_and_curvature():
    pytest.importorskip("Pynite")
    request, context = _synthetic_case()
    record = evaluate_thermal_section_frame(request, context)

    # At the four symmetric fibers, free strain is 0 at z=-1 m and 0.001 at
    # z=+1 m. The exact least-squares plane is 0.0005 + 0.0005*z.
    assert record["status"] == "computed"
    projected = record["projected_fields"][0]
    assert projected["mean_free_strain"] == pytest.approx(0.0005, abs=1e-15)
    assert projected["curvature_y_per_m"] == pytest.approx(0.0005, abs=1e-15)
    assert projected["curvature_z_per_m"] == pytest.approx(0.0, abs=1e-15)
    assert projected["residual_area_moments"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-15)
    assert record["maximum_absolute_fiber_elastic_normal_strain"] == pytest.approx(0.0, abs=1e-12)
