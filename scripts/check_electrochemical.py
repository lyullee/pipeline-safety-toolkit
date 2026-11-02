"""Run original references against the owner engine and preserve unresolved laboratory metadata."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

import openpyxl
from check_integrations import revision

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.electrochemical_exchange import (
    SurfaceCurrentReduction,
    evaluate_electrochemical,
    load_galvanized_tafel_summary,
    reduce_surface_current,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    before = revision(args.materials)
    sys.path.insert(0, str((args.materials / "src").resolve()))
    private = Path("artifacts/electrochemical")
    private.mkdir(parents=True, exist_ok=True)
    cases, context = runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()
    records, authored = {}, {}

    def save(name, request, ctx=context, history=None):
        result = evaluate_electrochemical(request, ctx, history_record=history)
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
        (private / f"{name}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        records[name] = result
        return result

    for method, request in cases.items():
        result = save(method, request)
        assert result["status"] == "computed"
        authored[method] = dict(
            status=result["status"],
            values=result["values"],
            units=result["output_units"],
            result_digest=result["record_digest"],
            model_version=result["native_receipt"]["native"]["payload"]["model_version"],
        )
        assert (
            save(method + "_unreviewed", request.model_copy(update={"applicability_reviews": {}}))["status"]
            == "needs_data"
        )
    # Independent mathematical reference checks, not a reproduction of an external design table.
    checks = {}

    def check(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-15), (name, actual, expected)
        checks[name] = dict(actual=actual, expected=expected, absolute_error=abs(actual - expected))

    check(
        "coupon_penetration_m",
        records["corrosion.coupon_loss"]["values"]["mean_penetration_m"],
        0.001 / (0.01 * 7850),
    )
    rate = 0.9 * 0.2 * 0.055845 / (2 * 96485.33212331001 * 7850)
    check("faraday_rate_m_s", records["corrosion.faraday"]["values"]["rate_m_s"], rate)
    check(
        "charge_mass_kg",
        records["corrosion.charge_to_loss"]["values"]["dissolved_mass_kg"],
        0.0279225 * 0.0001,
    )
    check(
        "stern_geary_coefficient_V",
        records["corrosion.stern_geary"]["values"]["stern_geary_coefficient_V"],
        0.06 / math.log(10),
    )
    check(
        "galvanic_current_A", records["corrosion.galvanic_linear"]["values"]["coupling_current_A"], 0.01 / 3
    )
    check(
        "coating_wet_volume_m3",
        records["protection.coating_coverage"]["values"]["required_wet_volume_m3"],
        100 * 0.0002 / (0.5 * 0.7),
    )
    check("cathodic_charge_C", records["protection.cathodic_budget"]["values"]["required_charge_C"], 40 / 6)
    check("cathodic_interior_peak_A", records["protection.cathodic_budget"]["values"]["peak_current_A"], 2.5)
    check(
        "cathodic_interior_peak_time_s", records["protection.cathodic_budget"]["values"]["peak_time_s"], 2.0
    )
    assert (
        save(
            "protective_current_is_not_dissolution",
            cases["corrosion.faraday"].model_copy(update={"current_meaning": "protective_demand"}),
        )["status"]
        == "out_of_scope"
    )
    assert (
        save(
            "net_current_is_not_dissolution",
            cases["corrosion.charge_to_loss"].model_copy(
                update={"current_meaning": "net_external_electrode_current"}
            ),
        )["status"]
        == "out_of_scope"
    )
    galvanic = cases["corrosion.galvanic_linear"]
    assert (
        save(
            "galvanic_outside_linear_support",
            galvanic.model_copy(
                update={"parameters": galvanic.parameters | {"maximum_linear_overpotential_V": 0.001}}
            ),
        )["status"]
        == "out_of_scope"
    )
    assert (
        save("missing_potential_reference", galvanic.model_copy(update={"potential_reference_id": None}))[
            "status"
        ]
        == "needs_data"
    )
    source_spec = SurfaceCurrentReduction(
        asset_id=context.asset_id,
        location_id=galvanic.location_id,
        clock_id=context.clock_id,
        condition_id=galvanic.condition_id,
        channel="j",
        exposed_area_m2=2.0,
        density_area_basis="bare_surface",
        current_meaning="anodic_dissolution",
        maximum_interval_s=4.0,
        sign_and_surface_reviewed=True,
        evidence_ids=("reference",),
    )
    frames = tuple(
        SignalFrame(
            stream_id="authored_dissolution_current",
            clock_id=context.clock_id,
            time_s=t,
            values={"j": value},
            units={"j": "A/m2"},
            valid={"j": True},
            origin="synthetic",
        )
        for t, value in ((0.0, 1.0), (4.0, 3.0))
    )
    history = reduce_surface_current(frames, source_spec, context)
    assert history["charge_C_on_known_intervals"] == 16.0
    charge = cases["corrosion.charge_to_loss"]
    linked = save(
        "bound_dissolution_history",
        charge.model_copy(update={"parameters": charge.parameters | {"anodic_charge_C": 16.0}}),
        history=history,
    )
    assert linked["status"] == "computed"
    check("bound_charge_mass_kg", linked["values"]["dissolved_mass_kg"], 16 * 0.0279225 / 96485.33212331001)
    cp = cases["protection.cathodic_budget"]
    cp_history = reduce_surface_current(
        frames, source_spec.model_copy(update={"current_meaning": "protective_demand"}), context
    )
    cp_parameters = cp.parameters | dict(
        time_s=[0.0, 4.0],
        protected_area_m2=2.0,
        bare_surface_fraction=[1.0, 1.0],
        bare_surface_current_density_A_m2=[1.0, 3.0],
    )
    assert (
        save(
            "bound_protective_demand", cp.model_copy(update={"parameters": cp_parameters}), history=cp_history
        )["status"]
        == "computed"
    )
    dataset = next(d for d in catalog() if d.id == "galvanized_polarization")
    root = Path("data/raw/galvanized_polarization")
    assets = []
    for asset in dataset.assets:
        algorithm, checksum = asset.checksum.split(":", 1)
        assert file_digest(root / asset.filename, algorithm) == checksum
        assets.append(
            dict(
                filename=asset.filename,
                bytes=(root / asset.filename).stat().st_size,
                sha256=file_digest(root / asset.filename),
                supplier_checksum=asset.checksum,
            )
        )
    table = load_galvanized_tafel_summary(root / "metals-3156094_data.xlsx")
    (private / "reported_tafel_summary.json").write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    assert len(table["rows"]) == 24
    data_context = RunContext(
        asset_id="galvanized_steel_lab_specimens",
        scenario_id="conditional_electrochemical_exchange",
        clock_id="declared_hypothetical_exposure_seconds",
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id="source",
                citation=dataset.citation,
                origin="laboratory",
                sha256=table["source_sha256"],
                license_id=dataset.license_id,
            ),
            EvidenceRef(
                id="assumptions",
                citation="Authored conditional Fe2+ / density7850 / efficiency1 / 100000-second exposure; not measured dissolution or field validation",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={
            "reported_icorr_is_author_estimate": True,
            "exposure_and_dissolution_stoichiometry_assumed": True,
            "output_origin": "simulation",
        },
    )
    conditional, held = [], []
    for i, row in enumerate(table["rows"]):
        template = cases["corrosion.stern_geary"]
        req = template.model_copy(
            update=dict(
                asset_id=data_context.asset_id,
                location_id=f"source_{row['specimen_id']}_{row['electrolyte_id']}",
                clock_id=data_context.clock_id,
                condition_id=row["electrolyte_id"],
                material_id=row["specimen_id"],
                environment_id=row["electrolyte_id"],
                parameters=dict(
                    anodic_tafel_slope_V_decade=row["anodic_slope_V_decade"],
                    cathodic_tafel_slope_V_decade=row["cathodic_slope_magnitude_V_decade"],
                    area_normalized_resistance_ohm_m2=None,
                ),
                evidence_ids=("source", "assumptions"),
            )
        )
        result = save(f"source_unknown_area_{i}", req, data_context)
        assert result["status"] == "needs_data"
        held.append(result["record_digest"])
        if not row["zinc_coated"]:
            template = cases["corrosion.faraday"]
            p = template.parameters | dict(
                current_density_A_m2=row["corrosion_current_density_estimate_A_m2"], current_efficiency=1.0
            )
            req = template.model_copy(
                update=dict(
                    asset_id=data_context.asset_id,
                    location_id=req.location_id,
                    clock_id=data_context.clock_id,
                    condition_id=req.condition_id,
                    material_id="hypothetical_Fe2+_steel",
                    environment_id=req.environment_id,
                    current_meaning="conditional_corrosion_current_estimate",
                    parameters=p,
                    evidence_ids=("source", "assumptions"),
                )
            )
            result = save(f"source_conditional_Fe_{i}", req, data_context)
            assert result["status"] == "computed"
            conditional.append(result["values"]["penetration_m"])
    assert len(conditional) == 12 and len(held) == 24
    curves = []
    for asset in dataset.assets:
        if "potentiodynamic" not in asset.filename:
            continue
        workbook = openpyxl.load_workbook(root / asset.filename, read_only=True, data_only=True)
        curves.append(
            dict(
                filename=asset.filename,
                sheets=[dict(name=s.title, rows=s.max_row, columns=s.max_column) for s in workbook],
            )
        )
        workbook.close()
    assert revision(args.materials) == before
    summary = dict(
        schema="pipeline_integrity.electrochemical_verification.v1",
        authored_cases=authored,
        independent_references=checks,
        all_cases={
            name: dict(status=r["status"], result_digest=r["record_digest"])
            for name, r in records.items()
            if not name.startswith("source_")
        },
        source=dict(
            dataset_id=dataset.id,
            citation=dataset.citation,
            creators=list(dataset.creators),
            license_id=dataset.license_id,
            source=dataset.source,
            assets=assets,
            reported_summary_rows=24,
            unresolved_area_or_reference_cases=24,
            conditional_bare_steel_cases=12,
            conditional_penetration_m_range=[min(conditional), max(conditional)],
            hypothetical_exposure_s=100000.0,
            conditional_Fe2_density_kg_m3=7850.0,
            current_efficiency_assumed=1.0,
            measured_dissolution_or_service_rate_validated=False,
            plus_minus_kind="source unspecified; no independent specimen or distribution inference",
            polarization_workbooks=curves,
            raw_or_derived_observations_in_code_release=False,
        ),
        potash_cp=dict(
            status="metadata_only",
            source="https://data.mendeley.com/datasets/z6m27978vj/2",
            raw_access="HTTP 403; no observations acquired or applied",
        ),
        external_materials=before,
        external_materials_unchanged=True,
        implementation_sha256=file_digest(Path("src/pipeline_integrity/electrochemical_exchange.py")),
        script_sha256=file_digest(Path(__file__)),
        example_sha256=file_digest(Path("examples/electrochemical_methods.py")),
        field_or_standard_qualification=False,
    )
    Path("research/electrochemical_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                native_models=7,
                independent_references=len(checks),
                source_rows=24,
                unknown_area_held=24,
                conditional_Fe_cases=12,
                materials_unchanged=True,
            )
        )
    )


if __name__ == "__main__":
    main()
