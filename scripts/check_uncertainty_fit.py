"""Fit source-condition cohorts without row mixing; retain adverse ordered-holdout outcomes."""

import hashlib
import json
import math
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.inverse import ResidualScale
from pipeline_integrity.lifecycle_exchange import PowerIntegration, integrate_power_frames
from pipeline_integrity.loaders import hydraulic_cycle
from pipeline_integrity.uncertainty import EnsembleMetric, EnsembleNode, EnsembleValues, evaluate_ensemble
from pipeline_integrity.uncertainty_fit import (
    DistributionAssessment,
    DistributionFitRequest,
    DistributionUse,
    JointObservation,
    JointObservations,
    UncertainVariable,
    assess_joint_distribution,
    distribution_design,
    fit_joint_distribution,
)


def main():
    archive = Path("data/raw/hydraulic/source.zip")
    dataset = next(d for d in catalog() if d.id == "hydraulic")
    assert file_digest(archive) == dataset.assets[0].checksum.split(":", 1)[1]
    private = Path("artifacts/uncertainty_fit")
    private.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        profile_bytes = source.read("profile.txt")
        profiles = [
            tuple(int(x) for x in line.split()) for line in profile_bytes.decode("ascii").splitlines()
        ]
        assert len(profiles) == 2205 and all(len(p) == 5 for p in profiles)
        features = [dict(energy_j=None, volume_m3=None) for _ in profiles]
        member_hashes = {"profile.txt": hashlib.sha256(profile_bytes).hexdigest()}
        for member, count, rate, field, scale in (
            ("EPS1.txt", 6000, 100.0, "energy_j", 1.0),
            ("FS1.txt", 600, 10.0, "volume_m3", 1 / 60000),
        ):
            fingerprint = hashlib.sha256()
            times = np.arange(count) / rate
            used = 5991 if member == "EPS1.txt" else 600
            assert times[used - 1] == 59.9
            rows = 0
            with source.open(member) as stream:
                for index, line in enumerate(stream):
                    fingerprint.update(line)
                    values = np.fromstring(line.decode("ascii"), sep="\t") * scale
                    assert len(values) == count and index < len(profiles)
                    if np.isfinite(values).all() and (values >= 0).all():
                        features[index][field] = float(np.trapezoid(values[:used], times[:used]))
                    rows += 1
            assert rows == len(profiles)
            member_hashes[member] = fingerprint.hexdigest()
    evidence = (
        EvidenceRef(
            id="uci447",
            citation=dataset.citation,
            origin="laboratory",
            sha256=file_digest(archive),
            license_id=dataset.license_id,
        ),
        EvidenceRef(
            id="protocol",
            citation="Original predeclared ordered-holdout protocol: source-condition cohorts; no IID, stationarity or field qualification",
            origin="synthetic",
            license_id="MIT",
        ),
    )
    context = RunContext(
        asset_id="uci_hydraulic_fixture",
        scenario_id="source_order_distribution_study",
        clock_id="cycle_source_order_not_elapsed_seconds",
        origin="laboratory",
        evidence=evidence,
        metadata={
            "source_index_is_elapsed_time": False,
            "profile_is_component_condition_not_HumanFailure_or_actuation": True,
        },
    )
    variables = tuple(
        UncertainVariable(id=key, unit=unit, layer="measurement", evidence_id="uci447")
        for key, unit in (("energy_j", "J"), ("volume_m3", "m3"))
    )
    rows = []
    block = -1
    previous = None
    for index, (profile, feature) in enumerate(zip(profiles, features, strict=True)):
        if profile != previous:
            block += 1
        previous = profile
        rows.append(
            JointObservation(
                id=f"cycle_{index}",
                order=index,
                group_id=f"contiguous_profile_block_{block}",
                condition_id="profile_" + "_".join(map(str, profile)),
                values=feature,
                valid={k: v is not None for k, v in feature.items()},
                evidence_id="uci447",
            )
        )
    observations = JointObservations(
        id="uci447_cycle_interval_quantities",
        variables=variables,
        rows=tuple(rows),
        order_basis="source_row_order",
        source_digests={"archive": file_digest(archive), **member_hashes},
        context=context,
    )
    (private / "observations.json").write_text(
        observations.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    # Audit the bulk feature reduction against the established source loader and
    # independent signed power integrator at both early and late source positions.
    audits = {}
    for cycle in (0, 146, 2186, 2204):
        sensors = hydraulic_cycle(archive, cycle, sensors=("EPS1", "FS1"))["sensors"]
        power, flow = sensors["EPS1"], sensors["FS1"]
        source_context = context.model_copy(update={"clock_id": power["clock_id"]})
        frames = tuple(
            SignalFrame(
                stream_id=f"EPS1_cycle_{cycle}",
                clock_id=power["clock_id"],
                time_s=float(t),
                values={"power": float(p)},
                units={"power": "W"},
                valid={"power": bool(ok)},
                origin="laboratory",
            )
            for t, p, ok in zip(
                power["time_s"][:5991], power["values"][:5991], power["valid"][:5991], strict=True
            )
        )
        result = integrate_power_frames(
            frames,
            PowerIntegration(
                channel="power",
                maximum_sample_interval_s=0.01,
                sign_convention="nonnegative_consumption",
                evidence_id="uci447",
            ),
            source_context,
        )
        assert result["status"] == "computed"
        energy = result["values"]["import_energy_j"]
        volume = float(np.trapezoid(flow["values"], flow["time_s"]))
        assert math.isclose(energy, features[cycle]["energy_j"], rel_tol=1e-13)
        assert volume == features[cycle]["volume_m3"]
        audits[str(cycle)] = dict(
            power_samples=5991,
            flow_samples=600,
            common_interval_s=[0.0, 59.9],
            power_integrator_digest=result["record_digest"],
            energy_absolute_difference_j=abs(energy - features[cycle]["energy_j"]),
        )
    first_group = [r for r in rows if r.group_id == rows[0].group_id]
    assert len(first_group) == 210 and profiles[0] == (3, 100, 0, 130, 1)
    train, heldout = first_group[:128], first_group[-64:]
    scales = dict(
        energy_j=ResidualScale(value=100000.0, unit="J", source_id="protocol"),
        volume_m3=ResidualScale(value=0.005, unit="m3", source_id="protocol"),
    )

    def protocol(family):
        return DistributionFitRequest(
            id=f"transient_source_condition_{family}",
            condition_id=rows[0].condition_id,
            training_ids=tuple(r.id for r in train),
            validation_ids=tuple(r.id for r in heldout),
            families=dict.fromkeys((v.id for v in variables), family),
            scales=scales,
            split_policy="ordered_gap",
            minimum_order_gap=19,
            minimum_training_rows=30,
            minimum_validation_rows=10,
            definition_reviewed=True,
            evidence_id="protocol",
        )

    criteria = DistributionAssessment(
        evidence_id="protocol", minimum_marginal_coverage=0.8, maximum_mean_energy_score=1.0
    )
    candidates, private_predictions = {}, {}
    fit_records = {}
    for family in ("normal", "lognormal"):
        fit = fit_joint_distribution(observations, protocol(family))
        assert fit["status"] == "computed"
        fit_records[family] = fit
        (private / f"fit_{family}.json").write_text(json.dumps(fit, indent=2) + "\n", encoding="utf-8")
        candidates[family] = dict(
            training_rows=128,
            ignored_gap_rows=18,
            validation_rows=64,
            status=fit["status"],
            training_only_digest=fit["training_only_digest"],
            fitted_variables=fit["fitted_variables"],
            latent_correlation=fit["latent_correlation"],
            same_source_block_in_train_and_validation=True,
            heldout={},
        )
        for mode in ("empirical_pairs", "normal_score_copula", "independent_counterfactual"):
            use = DistributionUse(
                mode=mode,
                samples=1024,
                seed=881,
                interpretation="scenario",
                target_condition_id=protocol(family).condition_id,
                applicability_reviewed=True,
                evidence_id="protocol",
            )
            prediction = distribution_design(fit, use, context)
            report = assess_joint_distribution(fit, observations, prediction, criteria)
            assert report["status"] == "computed" and report["declared_gate"] in ("passed", "failed")
            (private / f"prediction_{family}_{mode}.json").write_text(
                json.dumps(prediction, indent=2) + "\n", encoding="utf-8"
            )
            (private / f"score_{family}_{mode}.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            candidates[family]["heldout"][mode] = {k: v for k, v in report.items() if k not in ("rows",)}
            private_predictions[(family, mode)] = prediction
    # Replacing holdout values cannot change fitted parameters or rank correlation.
    changed = observations.model_copy(
        update={
            "rows": tuple(
                r.model_copy(update={"values": dict(energy_j=1e8, volume_m3=1.0)})
                if r.id in set(protocol("lognormal").validation_ids)
                else r
                for r in rows
            )
        }
    )
    changed_fit = fit_joint_distribution(changed, protocol("lognormal"))
    assert changed_fit["fitted_variables"] == fit_records["lognormal"]["fitted_variables"]
    assert changed_fit["latent_correlation"] == fit_records["lognormal"]["latent_correlation"]
    assert changed_fit["training_only_digest"] == fit_records["lognormal"]["training_only_digest"]
    stable = [r for r, p in zip(rows, profiles, strict=True) if p == (100, 100, 0, 90, 0)]
    assert len(stable) == 19
    insufficient = protocol("lognormal").model_copy(
        update={
            "id": "small_stable_cohort",
            "condition_id": stable[0].condition_id,
            "training_ids": tuple(r.id for r in stable[:10]),
            "validation_ids": tuple(r.id for r in stable[-6:]),
            "minimum_order_gap": 4,
        }
    )
    stable_fit = fit_joint_distribution(observations, insufficient)
    assert stable_fit["status"] == "needs_data" and stable_fit["fitted_variables"] is None
    (private / "small_stable_fit.json").write_text(json.dumps(stable_fit, indent=2) + "\n", encoding="utf-8")
    nominal = [r for r, p in zip(rows, profiles, strict=True) if p == (100, 100, 0, 130, 0)]
    assert len(nominal) == 10
    nominal_request = insufficient.model_copy(
        update={
            "id": "small_all_nominal_stable_cohort",
            "condition_id": nominal[0].condition_id,
            "training_ids": tuple(r.id for r in nominal[:6]),
            "validation_ids": tuple(r.id for r in nominal[-3:]),
            "minimum_order_gap": 2,
        }
    )
    nominal_fit = fit_joint_distribution(observations, nominal_request)
    assert nominal_fit["status"] == "needs_data" and nominal_fit["fitted_variables"] is None
    (private / "small_nominal_fit.json").write_text(
        json.dumps(nominal_fit, indent=2) + "\n", encoding="utf-8"
    )
    foreign = DistributionUse(
        mode="normal_score_copula",
        interpretation="scenario",
        target_condition_id=stable[0].condition_id,
        applicability_reviewed=True,
        evidence_id="protocol",
    )
    transferred = distribution_design(fit_records["lognormal"], foreign, context)
    assert transferred["status"] == "out_of_scope"
    # Couple a fitted positive-energy design to the unchanged registered cost
    # calculator. The price is illustrative, and one interval is not annual duty.
    engine = create_registry()
    metrics = (
        EnsembleMetric(id="specific_energy", unit="J/m3", meaning="quantity", evidence_id="protocol"),
        EnsembleMetric(id="interval_energy_cost", unit="REF", meaning="cost", evidence_id="protocol"),
    )

    def evaluate(values, ctx):
        energy, volume = values["energy_j"], values["volume_m3"]
        if energy is None or volume is None or energy < 0 or volume <= 0:
            return EnsembleValues(
                context=ctx,
                values=dict.fromkeys(m.id for m in metrics),
                units={m.id: m.unit for m in metrics},
                statuses=dict.fromkeys((m.id for m in metrics), "out_of_scope"),
                native_payload={"issue": "nonphysical interval energy or volume"},
            )
        native = engine.evaluate(
            "economics.energy",
            dict(
                electrical_power_w=energy / 59.9,
                operating_duration_s=59.9,
                price_per_kwh=0.2,
                emission_kg_co2e_per_kwh=0.4,
                currency="REF",
                factor_source="authored illustrative tariff and emission factor",
            ),
            ctx,
        )
        return EnsembleValues(
            context=ctx,
            values=dict(specific_energy=energy / volume, interval_energy_cost=native.values["energy_cost"]),
            units={m.id: m.unit for m in metrics},
            statuses=dict.fromkeys((m.id for m in metrics), native.status),
            native_payload=native.model_dump(mode="json"),
        )

    node = EnsembleNode(
        id="interval_quantities",
        version="1",
        source_digest=file_digest(Path(__file__)),
        configuration=dict(
            duration_s=59.9,
            tariff_REF_per_kwh=0.2,
            annual_duty_inferred=False,
            condition_id=protocol("lognormal").condition_id,
        ),
        metrics=metrics,
        evaluate=evaluate,
    )
    propagated = evaluate_ensemble(
        private_predictions[("lognormal", "normal_score_copula")]["design"], (node,)
    )
    assert propagated["status"] == "computed"
    (private / "fitted_propagation.json").write_text(
        json.dumps(propagated, indent=2) + "\n", encoding="utf-8"
    )
    summary = dict(
        schema="pipeline_integrity.uncertainty_fit_verification.v1",
        data=dict(
            id=dataset.id,
            source=dataset.source,
            citation=dataset.citation,
            creators=list(dataset.creators),
            license_id=dataset.license_id,
            archive_sha256=file_digest(archive),
            member_sha256=member_hashes,
            original_cycles=2205,
            original_power_samples=2205 * 6000,
            original_flow_samples=2205 * 600,
            used_power_samples=2205 * 5991,
            used_flow_samples=2205 * 600,
            common_interval_s=[0.0, 59.9],
            complete_feature_pairs=sum(all(v is not None for v in f.values()) for f in features),
            condition_profiles=len(Counter(profiles)),
            source_order_is_physical_time=False,
            bulk_source_audits=audits,
            raw_or_derived_cycle_rows_in_release=False,
        ),
        transient_cohort=dict(
            profile=[3, 100, 0, 130, 1],
            contiguous_source_rows=210,
            stable_flag_meaning="static conditions may not yet have been reached; this study does not assume stationarity",
            independent_train_and_holdout_established=False,
            criteria=criteria.model_dump(mode="json"),
            candidates=candidates,
            family_or_mode_automatically_selected=False,
        ),
        stable_cohort=dict(
            profile=[100, 100, 0, 90, 0],
            all_components_nominal=False,
            accumulator_condition="source label 90: degraded accumulator; not an executable pressure boundary",
            rows=19,
            training_rows=10,
            validation_rows=6,
            required_training_rows=30,
            required_validation_rows=10,
            status=stable_fit["status"],
            issues=stable_fit["issues"],
            lowered_minimum_to_force_fit=False,
        ),
        all_nominal_stable_cohort=dict(
            profile=[100, 100, 0, 130, 0],
            rows=10,
            training_rows=6,
            validation_rows=3,
            required_training_rows=30,
            required_validation_rows=10,
            status=nominal_fit["status"],
            issues=nominal_fit["issues"],
            lowered_minimum_to_force_fit=False,
        ),
        target_condition_transfer=dict(status=transferred["status"], issues=transferred["issues"]),
        heldout_values_cannot_change_fitted_parameters=True,
        native_fitted_propagation=dict(
            samples=1024,
            status=propagated["status"],
            summaries=propagated["summaries"],
            result_digest=propagated["record_digest"],
            diagnostic_use_only=True,
            observed_human_error_rate_or_annual_duty_inferred=False,
        ),
        implementation_sha256=file_digest(Path("src/pipeline_integrity/uncertainty_fit.py")),
        ensemble_implementation_sha256=file_digest(Path("src/pipeline_integrity/uncertainty.py")),
        script_sha256=file_digest(Path(__file__)),
        field_qualified=False,
    )
    Path("research/uncertainty_fit_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                status="passed",
                source_cycles=2205,
                fit_rows=128,
                validation_rows=64,
                stable_cohort=stable_fit["status"],
                all_nominal_stable_cohort=nominal_fit["status"],
                transfer=transferred["status"],
                native_cost_draws=1024,
                gates={
                    family: {mode: entry["declared_gate"] for mode, entry in c["heldout"].items()}
                    for family, c in candidates.items()
                },
            )
        )
    )


if __name__ == "__main__":
    main()
