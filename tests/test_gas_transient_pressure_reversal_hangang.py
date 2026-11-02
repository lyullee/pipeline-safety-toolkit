"""Diagnostic sweep for settled reversal in a finite-inventory gas network.

This synthetic case reports solver-rejected candidates separately from
computed candidates. A rejected or out-of-scope run can never count as a
successful reversal regime or receive verification credit.
"""

import importlib.util
import json
import math
import os
from pathlib import Path

import pytest

from pipeline_integrity.thermo import StateRequest
from pipeline_integrity import gas_thermal_transient as gt

_spec = importlib.util.spec_from_file_location(
    "gas_thermal_example", Path(__file__).parents[1] / "examples" / "gas_thermal_transient.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def _window_rows(record, window_id):
    return [row for row in (record.get("history") or []) if row["window_id"] == window_id]


def _late_signs(rows, sign):
    if not rows:
        return {
            "settled": False,
            "sample_count": 0,
            "minimum_signed_flow_kg_s": None,
            "link_signed_flows_kg_s": {},
        }
    count = max(1, math.ceil(len(rows) * 0.2))
    late = rows[-count:]
    link_flows = {
        link: [
            sign * row["native"]["network_solution"]["link_flows"][link]["mass_flow_kg_s"]
            for row in late
        ]
        for link in ("cell_0:in", "cell_1:out")
    }
    flows = [flow for samples in link_flows.values() for flow in samples]
    return {
        "settled": all(flow > 0.0 for flow in flows),
        "sample_count": count,
        "minimum_signed_flow_kg_s": min(flows),
        "link_signed_flows_kg_s": {
            link: {"minimum": min(samples), "maximum": max(samples), "final": samples[-1]}
            for link, samples in link_flows.items()
        },
    }


def test_pressure_reversal_reports_only_qualified_settling_candidates():
    coolprop = pytest.importorskip("CoolProp.CoolProp")
    props_si = coolprop.PropsSI
    base, _ = _example.make_case()
    cells = tuple(
        cell.model_copy(update={
            # Use a narrow bore so the high-contrast reversal stays inside the
            # model's independently declared omitted-kinetic-energy bound.
            "inner_diameter_m": 0.007,
            "fluid_wall_UA_W_K": 0.0,
            "ambient_UA_W_K": 0.0,
            "ambient_temperature_K": 300.0,
            "initial_wall_temperature_K": 300.0,
        })
        for cell in base.cells
    )

    def state(pressure_pa):
        return StateRequest(fluid="Nitrogen", inputs={"P": pressure_pa, "T": 300.0})

    # The first contrast and 10 ms baseline match the previously computed
    # regime. The reverse contrast and hold duration are swept logarithmically
    # to distinguish solver acceptance from a genuinely settled sign change.
    reverse_contrasts_pa = (1_000.0,)
    reverse_durations_s = (0.1, 0.5, 2.0)
    report = {
        "schema": "pipeline_integrity.gas_reversal_sweep.v1",
        "qualification": "diagnostic candidates only; not physical or field qualification",
        "baseline_forward_contrast_Pa": 0.0005,
        "forward_duration_s": 0.01,
        "reverse_contrasts_Pa": list(reverse_contrasts_pa),
        "reverse_durations_s": list(reverse_durations_s),
        "cases": [],
        "accepted_settling_candidates": [],
    }

    for reverse_contrast in reverse_contrasts_pa:
        for reverse_duration in reverse_durations_s:
            first = base.windows[0].model_copy(update={
                "id": "left_to_right",
                "duration_s": 0.01,
                "timestep_s": 0.001,
                "maximum_reviewed_timestep_s": 0.001,
                "left_state": state(1_000_000.0005),
                "right_state": state(1_000_000.0),
            })
            second = first.model_copy(update={
                "id": "right_to_left",
                "duration_s": reverse_duration,
                "left_state": state(1_000_000.0),
                "right_state": state(1_000_000.0 + reverse_contrast),
            })
            request, context = _example.make_case(
                reviewed=True,
                cells=cells,
                windows=(first, second),
                maximum_omitted_kinetic_energy_transport_W=0.01,
            )
            record = gt.evaluate_gas_thermal_transient(request, context)
            item = {
                "reverse_contrast_Pa": reverse_contrast,
                "reverse_duration_s": reverse_duration,
                "status": record["status"],
                "notes": record["notes"],
                "candidate_accepted": False,
            }
            if record["status"] != "computed_diagnostic":
                item["classification"] = "rejected_or_out_of_scope"
                report["cases"].append(item)
                continue

            first_rows = _window_rows(record, first.id)
            second_rows = _window_rows(record, second.id)
            budgets_ok = bool(record["history"]) and all(
                row["within_budget"] for row in record["history"]
            )
            forward_sign = _late_signs(first_rows, +1.0)
            reverse_sign = _late_signs(second_rows, -1.0)
            endpoint_inventory_ok = True
            for row in record["history"]:
                for cell in request.cells:
                    inventory_state = row["native"]["states"][cell.id]
                    thermo_state = row["native"]["thermo_states"][cell.id]
                    volume_m3 = math.pi * cell.inner_diameter_m**2 * cell.length_m / 4.0
                    direct_density = props_si(
                        "Dmass", "P", thermo_state["pressure_Pa"], "T", thermo_state["temperature_K"],
                        "HEOS::Nitrogen",
                    )
                    endpoint_inventory_ok &= math.isclose(
                        inventory_state["mass_kg"], volume_m3 * direct_density,
                        rel_tol=2e-8, abs_tol=2e-10,
                    )
            item.update({
                "classification": "computed_candidate",
                "budgets_ok": budgets_ok,
                "endpoint_inventory_ok": endpoint_inventory_ok,
                "forward_late_sign": forward_sign,
                "reverse_late_sign": reverse_sign,
            })
            if budgets_ok and endpoint_inventory_ok and forward_sign["settled"] and reverse_sign["settled"]:
                replay = gt.verify_gas_thermal_transient_record(
                    record, request=request, context=context
                )
                item["replay_valid"] = replay["valid"]
                if not replay["valid"]:
                    item["replay_reason"] = replay.get("reason")
                item["candidate_accepted"] = bool(replay["valid"])
                if item["candidate_accepted"]:
                    item["classification"] = "verified_synthetic_settling_candidate"
                    report["accepted_settling_candidates"].append({
                        "reverse_contrast_Pa": reverse_contrast,
                        "reverse_duration_s": reverse_duration,
                    })
            report["cases"].append(item)

    output = Path(os.environ.get(
        "PIPELINE_GAS_REVERSAL_SWEEP_REPORT", "gas-reversal-settling-sweep-report.json"
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    assert report["accepted_settling_candidates"], (
        "No computed, budget-compliant, replay-valid settling regime found; "
        f"rejections and candidates are recorded in {output}: "
        + json.dumps(report["cases"], allow_nan=False)
    )
