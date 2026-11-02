"""Publisher-pinned meter observations; missing gas boundaries stay unresolved.

Workbook cells and supplier text belong in private source receipts. Published
reviews contain aggregate diagnostics rather than copies of the observations.
"""

from __future__ import annotations

import io
import math
import re
from datetime import datetime
from pathlib import Path

from .contracts import EvidenceRef, RunContext, digest
from .soil_data import _numeric, _pinned, _workbook

VSL_ID = "vsl_lng_water_calibration"
VORTEX_ID = "vortex_air_meter_experiment"
VSL_WORKBOOK = "SI-traceable calibration dataset containing flow meter errors with LNG and water.xlsx"
SCHEMA = "pipeline_integrity.meter_source.v1"
SHEETS = (
    "Column headers",
    "EH PromassQ DN50",
    "EH PromassQ DN50 All runs",
    '2" Trigas DM-32 turbine',
    '2"Trigas DM-32 turbine All runs',
    'KROHNE 4" Alstosonic V',
    'KROHNE 4" Altosonic V All runs',
    "Emerson CMF300",
    "Emerson CMF300 All runs",
    "Emerson CMF100 LH2",
    "Emerson CMF100 LH2 All runs",
)
_RUN_FIELDS = {
    "MUT Upstream Pressure (bar(g))": ("upstream_pressure_pa_gauge", 100000.0, 0.0),
    "MUT Downstream Pressure (bar(g))": ("downstream_pressure_pa_gauge", 100000.0, 0.0),
    "MUT Upstream Temperature  (°C)": ("upstream_temperature_K", 1.0, 273.15),
    "MUT Downstream Temperature  (°C)": ("downstream_temperature_K", 1.0, 273.15),
    "Reference Flowrate (kg/s)": ("reference_mass_flow_kg_s", 1.0, 0.0),
    "Reference Flowrate (kg/h)": ("reference_mass_flow_kg_s", 1 / 3600, 0.0),
    "MUT Flowrate (kg/s)": ("indicated_mass_flow_kg_s", 1.0, 0.0),
    "MUT Flowrate (kg/h)": ("indicated_mass_flow_kg_s", 1 / 3600, 0.0),
    "Reference flowrate (m3/h)": ("reference_local_volume_flow_m3_s", 1 / 3600, 0.0),
    "MUT Flowrate (m3/h)": ("indicated_local_volume_flow_m3_s", 1 / 3600, 0.0),
    "MUT Upstream Reference Density (kg/m3)": ("upstream_source_density_kg_m3", 1.0, 0.0),
    "MUT Downstream Reference Density (kg/m3)": ("downstream_source_density_kg_m3", 1.0, 0.0),
    "MUT Run Time (s)": ("counter_duration_s", 1.0, 0.0),
    "MUT Pulse Count": ("pulse_count", 1.0, 0.0),
    "MUT Error (%)": ("reported_error_percent", 1.0, 0.0),
    "MUT Error corrected for zero offset (%)": ("source_zero_corrected_error_percent", 1.0, 0.0),
    "FT114 frequency (Hz)": ("source_average_frequency_Hz", 1.0, 0.0),
    "FT114 pulse factor (P/L)": ("source_pulse_factor_per_litre", 1.0, 0.0),
}


def _seal(body):
    return dict(body, record_digest=digest(body))


def _meter_pinned(path, identity, source_spec):
    payload, provenance = _pinned(path, identity, source_spec=source_spec)
    if provenance["dataset_origin"] != "laboratory_experiment":
        raise ValueError("reviewed meter sources retain laboratory-experiment origin")
    return payload, provenance


def _value(sheet, row_index, col_index):
    value = sheet["source_cells"][row_index][col_index]
    if isinstance(value, str) and value.startswith("="):
        # The cached supplier result is retained; no spreadsheet formula is run.
        number, letters = col_index + 1, ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        value = sheet["formula_cache"].get(f"{letters}{row_index + 1}")
    return _numeric(value)


def _calendar(date, start, end):
    source = dict(date=date, start=start, end=end, timezone=None, utc_assigned=False)
    # The reviewed campaign is 2026. Other two-digit years need another review.
    if not isinstance(date, str) or re.fullmatch(r"\d{2}-\d{2}-26", date) is None:
        return dict(source, start_iso=None, end_iso=None, wall_duration_s=None, valid=False)
    try:
        day = datetime.strptime(date[:6] + "2026", "%d-%m-%Y").date()
        a = datetime.combine(day, datetime.strptime(start, "%H:%M:%S").time())
        b = datetime.combine(day, datetime.strptime(end, "%H:%M:%S").time())
        duration = (b - a).total_seconds()
        if duration < 0:
            raise ValueError("a midnight rollover is not declared")
    except (TypeError, ValueError):
        return dict(source, start_iso=None, end_iso=None, wall_duration_s=None, valid=False)
    return dict(source, start_iso=a.isoformat(), end_iso=b.isoformat(), wall_duration_s=duration, valid=True)


def _difference(indicated, reference):
    return None if indicated is None or reference in (None, 0) else 100 * (indicated - reference) / reference


def _run_sheet(name, sheet):
    runs, header = [], None
    day = ambient = None
    pulse_factor = None
    pulse_basis = None
    for i, row in enumerate(sheet["source_cells"]):
        first = row[0]
        if isinstance(first, str) and re.fullmatch(r"Day\s+\d+\s*", first):
            day, ambient = first.strip(), None
        if isinstance(first, str) and first.lower().startswith("atmospheric pressure:"):
            matched = re.fullmatch(r"Atmospheric pressure:\s*([0-9.]+)\s*(Pa)?", first, flags=re.I)
            if matched is None:
                raise ValueError("unreviewed atmospheric pressure syntax")
            ambient = dict(
                source_row=i + 1, value=float(matched[1]), unit=matched[2], pressure_reference="absolute"
            )
            for text in row:
                if isinstance(text, str):
                    pulse = re.fullmatch(r"([0-9.]+) pulses/(kg|L)", text)
                    if pulse:
                        pulse_factor, pulse_basis = float(pulse[1]), pulse[2]
            if name == 'KROHNE 4" Altosonic V All runs' and i == 1:
                if row[10] != "programmed pulse factor" or row[12] != "pulse/L":
                    raise ValueError("ultrasonic pulse factor units changed")
                pulse_factor, pulse_basis = _value(sheet, i, 11), "L"
        if first == "DATE (DD-MM-YY)":
            new_header = [v for v in row if v is not None]
            if new_header[:3] != ["DATE (DD-MM-YY)", "TIME START", "TIME END"]:
                raise ValueError("source calendar headers changed")
            if any(v not in _RUN_FIELDS for v in new_header[3:]):
                raise ValueError("meter source header or unit is unreviewed")
            if header is not None and header != row:
                raise ValueError("within-sheet meter headers changed")
            header = row
        if not isinstance(first, str) or re.fullmatch(r"\d{2}-\d{2}-\d{2}", first) is None:
            continue
        if header is None:
            raise ValueError("source observation precedes a reviewed header")
        observed = {target: None for target, _, _ in _RUN_FIELDS.values()}
        for j, label in enumerate(header):
            if label in _RUN_FIELDS:
                target, scale, shift = _RUN_FIELDS[label]
                value = _value(sheet, i, j)
                observed[target] = None if value is None else value * scale + shift
        pa = ambient["value"] if ambient and ambient["unit"] == "Pa" and ambient["value"] > 0 else None
        for port in ("upstream", "downstream"):
            gauge = observed[f"{port}_pressure_pa_gauge"]
            observed[f"{port}_pressure_pa_abs"] = None if pa is None or gauge is None else pa + gauge
        error = _difference(observed["indicated_mass_flow_kg_s"], observed["reference_mass_flow_kg_s"])
        if error is None:
            error = _difference(
                observed["indicated_local_volume_flow_m3_s"], observed["reference_local_volume_flow_m3_s"]
            )
        reported = observed["reported_error_percent"]
        duration, pulses = observed["counter_duration_s"], observed["pulse_count"]
        pulse_flow = (
            None
            if None in (duration, pulses, pulse_factor) or duration <= 0 or pulse_factor <= 0
            else pulses / pulse_factor / duration
        )
        if pulse_flow is not None and pulse_basis == "L":
            pulse_flow /= 1000
        target = observed[
            "indicated_mass_flow_kg_s" if pulse_basis == "kg" else "indicated_local_volume_flow_m3_s"
        ]
        freq, volume = observed["source_average_frequency_Hz"], observed["reference_local_volume_flow_m3_s"]
        frequency_factor = None if freq is None or volume in (None, 0) else freq / volume / 1000
        clock = _calendar(*row[:3])
        invalid = [
            key
            for key, value in observed.items()
            if value is not None
            and (
                (key.endswith("temperature_K") and value <= 0)
                or (key.endswith("pressure_pa_abs") and value <= 0)
                or (key == "counter_duration_s" and value <= 0)
                or (key == "pulse_count" and value < 0)
            )
        ]
        runs.append(
            dict(
                source_sheet=name,
                source_row=i + 1,
                source_day_label=day,
                fluid="LNG",
                phase="liquid",
                observation_kind="publisher_single_repetition_average",
                clock=clock,
                source_atmospheric_pressure=ambient,
                observations_si=observed,
                invalid_domain_fields=invalid,
                programmed_pulse_factor=pulse_factor,
                programmed_pulse_factor_basis=pulse_basis,
                derived_error_percent=error,
                error_rounding_residual_percent=None
                if error is None or reported is None
                else error - reported,
                pulse_flow_residual_si=None if pulse_flow is None or target is None else pulse_flow - target,
                pulse_flow_residual_unit="kg/s"
                if pulse_basis == "kg"
                else "m3/s"
                if pulse_basis == "L"
                else None,
                frequency_factor_residual_per_litre=None
                if frequency_factor is None or observed["source_pulse_factor_per_litre"] is None
                else frequency_factor - observed["source_pulse_factor_per_litre"],
                wall_minus_counter_duration_s=None
                if duration is None or clock["wall_duration_s"] is None
                else clock["wall_duration_s"] - duration,
                reference_volume_basis="local_liquid_at_meter_not_normal_or_dry_gas",
                density_origin="supplier_REFPROP10_GERG_daily_GC_and_run_P_T"
                if "Density" in " ".join(v for v in header if isinstance(v, str))
                else None,
                gas_meter_applicability="needs_data_and_liquid_outside_gas_meter_scope",
            )
        )
    if header is None or not runs:
        raise ValueError("reviewed meter run sheet has no observations")
    return runs


def _summary_sheet(name, sheet):
    records, compositions, header, components = [], [], None, None
    fluid, day = None, None
    for i, row in enumerate(sheet["source_cells"]):
        first = row[0]
        if isinstance(first, str) and "with LNG" in first:
            fluid, day = "LNG", None
        if isinstance(first, str) and "with water" in first:
            fluid, day = "water", None
        if isinstance(first, str) and first.startswith("Day"):
            day = first
        if isinstance(row[1], str) and row[1].startswith("Reference Flowrate"):
            if row[1] not in {"Reference Flowrate (kg/h)", "Reference Flowrate (m3/h)"} or row[2:4] != [
                "Meter pressure (bar(g))",
                "Meter Temperature (°C)",
            ]:
                raise ValueError("summary flow/pressure/temperature units changed")
            if not all(label in row for label in ("Type A mean uA (%)", "Expanded U (%)", "Repeats (n)")):
                raise ValueError("summary uncertainty headers changed")
            header, components = row, None
            continue
        if first == "Component":
            components, header = [(j, v) for j, v in enumerate(row) if j > 0 and v is not None], None
            continue
        if components is not None and _value(sheet, i, components[0][0]) is not None:
            values = {label: _value(sheet, i, j) for j, label in components}
            expanded = first == "Uncertainty (cmol/mol, k=2)"
            compositions.append(
                dict(
                    source_sheet=name,
                    source_row=i + 1,
                    source_label=first,
                    quantity="expanded_composition_uncertainty" if expanded else "daily_average_composition",
                    source_unit="cmol/mol",
                    values=values,
                    coverage_factor=2 if expanded else None,
                    mole_fraction_values=None
                    if expanded
                    else {k: None if v is None else v / 100 for k, v in values.items()},
                    source_sum=None
                    if expanded or any(v is None for v in values.values())
                    else sum(values.values()),
                    covariance=None,
                    normalized=False,
                )
            )
            continue
        if header is None or _value(sheet, i, 1) is None:
            continue
        if fluid is None:
            raise ValueError("summary fluid identity absent")
        values = {
            label: _value(sheet, i, j)
            for j, label in enumerate(header)
            if label is not None and j > 0 and label != "Comments"
        }
        expanded, ua, repeats = (values[k] for k in ("Expanded U (%)", "Type A mean uA (%)", "Repeats (n)"))
        records.append(
            dict(
                source_sheet=name,
                source_row=i + 1,
                source_day_label=day,
                fluid=fluid,
                phase="liquid",
                observation_kind="publisher_repeated_setpoint_summary",
                source_values=values,
                error_percent=values.get("Meter error (%)"),
                source_zero_corrected_error_percent=values.get("Meter Error corrected for zero offset (%)"),
                pressure_pa_gauge=None
                if values["Meter pressure (bar(g))"] is None
                else values["Meter pressure (bar(g))"] * 100000,
                temperature_K=None
                if values["Meter Temperature (°C)"] is None
                else values["Meter Temperature (°C)"] + 273.15,
                reference_mass_flow_kg_s=None
                if "Reference Flowrate (kg/h)" not in values or values["Reference Flowrate (kg/h)"] is None
                else values["Reference Flowrate (kg/h)"] / 3600,
                reference_local_volume_flow_m3_s=None
                if "Reference Flowrate (m3/h)" not in values or values["Reference Flowrate (m3/h)"] is None
                else values["Reference Flowrate (m3/h)"] / 3600,
                uncertainty=dict(
                    expanded_percent=expanded,
                    coverage_factor=2,
                    scalar_standard_percent_from_U_over_k=None if expanded is None else expanded / 2,
                    source_type_A_mean_percent=ua,
                    type_A_quantity="relative_mean_pulse_factor" if "Trigas" in name else "meter_error",
                    repeats=repeats,
                    coverage_probability_approx=0.95,
                    coverage_condition="source_normal_distribution_statement_only",
                    degrees_of_freedom=None,
                    component_covariance=None,
                    independence_assumed=False,
                ),
                atmospheric_pressure_pa_abs=None,
                reference_volume_basis="local_liquid_not_normal_or_dry_gas",
                clock=None,
                field_accuracy_qualified=False,
            )
        )
    return records, compositions


def parse_vsl_calibration(payload):
    """Parse the reviewed worksheet schema; no formula or supplier program executes."""
    sheets = _workbook(payload)
    if tuple(sheets) != SHEETS:
        raise ValueError("meter workbook sheet identities/order differ from the reviewed source")
    runs, summaries, compositions = [], [], []
    for name, sheet in sheets.items():
        if "All runs" in name:
            runs.extend(_run_sheet(name, sheet))
        elif name != "Column headers":
            summary, composition = _summary_sheet(name, sheet)
            summaries.extend(summary)
            compositions.extend(composition)
    return _seal(
        dict(
            schema=SCHEMA,
            kind="vsl_liquid_meter_calibration",
            origin="laboratory",
            worksheets=sheets,
            runs=runs,
            summaries=summaries,
            compositions=compositions,
            claims=dict(
                independent_gas_validation=False,
                field_calibration=False,
                source_summary_and_repeats_independent=False,
                source_density_recomputed=False,
                covariance_inferred=False,
            ),
            limitations=[
                "LNG and water measurements do not qualify a gas-meter model",
                "CMF100 LH2 sheet title is an opaque label; observations are LNG",
                "Calendar clock has no supplied timezone",
                "Supplier repeat averages are not original pulse time histories",
                "Water has summary observations only",
                "Shared reference correlations and uncertainty degrees of freedom are unknown",
            ],
        )
    )


def parse_vortex_air_pdf(payload):
    """Read the actual single-page table with a lazy, bounded optional PDF reader."""
    if not isinstance(payload, bytes) or not 0 < len(payload) <= 1_000_000:
        raise ValueError("nonempty original meter PDF within 1 MB required")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload), strict=True)
    if reader.is_encrypted or len(reader.pages) != 1:
        raise ValueError("reviewed meter source is one unencrypted page")
    text = reader.pages[0].extract_text()
    if len(text) > 100000:
        raise ValueError("meter source text budget exceeded")
    return _vortex_text(text)


def _vortex_text(text):
    if not all(
        label in text
        for label in (
            "Pressures (kPa)",
            "Temperatures (°C)",
            "Gauge Atmospheric Orifice plate air pressure difference",
        )
    ):
        raise ValueError("vortex pressure reference or source units changed")
    selected = re.findall(r"^RH-\d+[^\n]*", text, flags=re.M)
    if len(selected) != 5 or [line.split()[0] for line in selected] != [f"RH-{i}" for i in range(1, 6)]:
        raise ValueError("vortex operating-case identities/order differ")
    observations = []
    for line in selected:
        tokens = line.split()
        if len(tokens) != 11:
            raise ValueError("vortex source numeric column count differs")
        try:
            values = [float(v) for v in tokens[1:]]
        except ValueError as exc:
            raise ValueError("invalid vortex source numeric token") from exc
        if any(not math.isfinite(v) for v in values):
            raise ValueError("nonfinite vortex observation")
        gauge, ambient, delta, temperatures = values[:3], values[3], values[4:7], values[7:]
        if (
            ambient <= 0
            or min(delta) < 0
            or min(temperatures) <= -273.15
            or any(g + ambient <= 0 for g in gauge)
        ):
            raise ValueError("vortex observation outside absolute domain")
        observations.append(
            dict(
                case_id=tokens[0],
                source_numeric_tokens=tokens[1:],
                source_values=values,
                branches=[
                    dict(
                        branch=branch,
                        pressure_pa_gauge=g * 1000,
                        atmospheric_pressure_pa_abs=ambient * 1000,
                        upstream_pressure_pa_abs=(g + ambient) * 1000,
                        orifice_pressure_difference_pa=dp * 1000,
                        upstream_temperature_K=t + 273.15,
                        downstream_pressure_pa_abs=None,
                        time_s=None,
                        gas_components=None,
                        gas_volume_basis=None,
                        reference_pressure_pa_abs=None,
                        reference_temperature_K=None,
                        meter_geometry=None,
                        taps=None,
                        independent_flow_reference=None,
                        uncertainty=None,
                        gas_meter_status="needs_data",
                    )
                    for branch, g, dp, t in zip(
                        ("inlet", "hot", "cold"), gauge, delta, temperatures, strict=True
                    )
                ],
            )
        )
    return _seal(
        dict(
            schema=SCHEMA,
            kind="vortex_air_orifice_conditions",
            origin="laboratory",
            source_text=text,
            observations=observations,
            clock_kind="operating_case_ids_not_time",
            source_units=dict(pressure="kPa", temperature="degC"),
            claims=dict(
                independent_gas_validation=False,
                field_calibration=False,
                dry_gas_assumed=False,
                downstream_tap_inferred=False,
            ),
            limitations=[
                "Nominal compressed air has no explicit mole fractions or humidity basis",
                "Geometry, pressure taps, flow reference and uncertainty are absent",
                "Operating cases do not supply a time axis or physical plant port inventory",
            ],
        )
    )


def read_vsl_calibration(folder, *, source_spec=None):
    """Require both the original workbook and its pinned supplier method document."""
    folder = Path(folder)
    raw, provenance = _meter_pinned(folder / VSL_WORKBOOK, VSL_ID, source_spec)
    _, method = _meter_pinned(folder / "ReadMe.pdf", VSL_ID, source_spec)
    body = {k: v for k, v in parse_vsl_calibration(raw).items() if k != "record_digest"}
    return _seal(dict(body, provenance=provenance, method_assets=[method]))


def read_vortex_air_meter(path, *, source_spec=None):
    raw, provenance = _meter_pinned(path, VORTEX_ID, source_spec)
    body = {k: v for k, v in parse_vortex_air_pdf(raw).items() if k != "record_digest"}
    return _seal(dict(body, provenance=provenance))


def verify_meter_source_record(record, *, source_root, source_spec=None):
    """Re-read pinned source bytes and compare the complete unsigned source receipt.

    A self-consistent rehashed projection alone is insufficient. Source-root is
    the common raw-data directory, with one dataset-id subdirectory per source.
    """
    body = {k: v for k, v in record.items() if k != "record_digest"}
    if record.get("record_digest") != digest(body):
        raise ValueError("meter source receipt digest differs")
    if record.get("kind") == "vsl_liquid_meter_calibration":
        expected = read_vsl_calibration(Path(source_root) / VSL_ID, source_spec=source_spec)
    elif record.get("kind") == "vortex_air_orifice_conditions":
        expected = read_vortex_air_meter(
            Path(source_root) / VORTEX_ID / "Research data.pdf", source_spec=source_spec
        )
    else:
        raise ValueError("unknown meter source receipt")
    if record != expected:
        raise ValueError("meter projection, origin, uncertainty, clock or source binding differs")
    return True


def vortex_gas_meter_cases(record, *, source_root, source_spec=None):
    """Make explicit unresolved-source requests, without a fictitious time axis."""
    verify_meter_source_record(record, source_root=source_root, source_spec=source_spec)
    if record["kind"] != "vortex_air_orifice_conditions":
        raise ValueError("vortex source receipt required")
    from .gas_meter import INPUT_UNITS, GasMeterRequest, boundary_metadata

    evidence = EvidenceRef(
        id="vortex-source",
        citation=record["provenance"]["citation"],
        origin="laboratory",
        sha256=record["provenance"]["sha256"],
        license_id="CC-BY-4.0",
        limitations=tuple(record["limitations"]),
    )
    cases = []
    for observation in record["observations"]:
        for branch in observation["branches"]:
            node = f"source-diagram:{branch['branch']}"
            request = GasMeterRequest(
                asset_id="publisher-vortex-apparatus",
                meter_id=node,
                location_id=node,
                clock_id="publisher-cases-without-time",
                event_id=observation["case_id"],
                time_s=None,
                clock_offset_s=0.0,
                nominal_positive_source_port=node + ":upstream",
                nominal_positive_target_port=node + ":downstream",
                upstream_port_id=node + ":upstream",
                downstream_port_id=node + ":downstream",
                geometry=None,
                components=None,
                volume_basis=None,
                upstream_pressure_pa_abs=branch["upstream_pressure_pa_abs"],
                downstream_pressure_pa_abs=None,
                upstream_temperature_K=branch["upstream_temperature_K"],
                coefficient_mode="native_correlation",
                exponent_basis="local_isentropic_rho_a2_over_P",
                reference=None,
                support=None,
                geometry_evidence_id=evidence.id,
                observation_evidence_id=evidence.id,
                composition_evidence_id=evidence.id,
                coefficient_evidence_id=evidence.id,
                evidence_ids=(evidence.id,),
                applicability_reviews={},
                input_units=INPUT_UNITS.copy(),
            )
            context = RunContext(
                asset_id=request.asset_id,
                scenario_id=observation["case_id"],
                clock_id=request.clock_id,
                origin="laboratory",
                evidence=(evidence,),
                metadata=dict(
                    gas_meter_boundary=boundary_metadata(request),
                    meter_observation_binding=dict(
                        record_digest=record["record_digest"],
                        case_id=observation["case_id"],
                        branch=branch["branch"],
                        port_identity="symbolic_source_diagram_nodes_not_plant_inventory",
                        source_orifice_pressure_difference_pa=branch["orifice_pressure_difference_pa"],
                        downstream_tap_mapping=None,
                        source_clock=None,
                    ),
                ),
            )
            cases.append((request, context))
    return tuple(cases)
