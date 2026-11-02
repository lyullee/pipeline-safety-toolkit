"""Spatial support and exposure accounting around the existing PRISM and HRA APIs.

Coordinates are never transformed or interpolated here.  A receptor is a reviewed
representative of exactly one disjoint rectangular population cell.  A supplied
joint casualty distribution remains a separate input from its marginal mean.
"""

import importlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .consequence_exchange import ConsequenceResult
from .contracts import Contract, Fraction, Name, Nonnegative, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .risk_exchange import RiskFrequency

UNITS = {
    "coordinates": "m",
    "cell_area": "m2",
    "population": "person",
    "presence_probability": "1",
    "conditional_harm": "1",
    "frequency": "year^-1",
    "time": "s",
}
REVIEWS = (
    "coordinate_frame_and_representative_support",
    "disjoint_population_and_presence",
    "conditional_harm_and_time_window",
    "event_counting_and_condition_partition",
)


class SpatialGrid(Contract):
    frame_id: Name
    crs: Literal["local_ENU"] = "local_ENU"
    origin_description: Name
    origin_latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    origin_longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    horizontal_datum: Name
    vertical_datum: Name
    axes: Literal["east_north_up"] = "east_north_up"
    x_edges_m: tuple[float, ...] = Field(min_length=2, max_length=101)
    y_edges_m: tuple[float, ...] = Field(min_length=2, max_length=101)
    receptor_height_m: float
    evidence_id: Name

    @model_validator(mode="after")
    def geometry(self):
        if (self.origin_latitude_deg is None) != (self.origin_longitude_deg is None):
            raise ValueError("geographic origin requires both latitude and longitude")
        for edges in (self.x_edges_m, self.y_edges_m):
            if any(b <= a for a, b in zip(edges, edges[1:], strict=False)):
                raise ValueError("grid edges must increase strictly")
        return self

    def cells(self):
        return [
            {
                "id": f"cell:{iy}:{ix}",
                "ix": ix,
                "iy": iy,
                "bounds_m": [x0, x1, y0, y1],
                "area_m2": (x1 - x0) * (y1 - y0),
                "position_enu_m": [(x0 + x1) / 2, (y0 + y1) / 2, self.receptor_height_m],
            }
            for iy, (y0, y1) in enumerate(zip(self.y_edges_m, self.y_edges_m[1:], strict=False))
            for ix, (x0, x1) in enumerate(zip(self.x_edges_m, self.x_edges_m[1:], strict=False))
        ]


class SpatialPopulation(Contract):
    cell_id: Name
    receptor_id: Name | None
    persons: int | None = Field(ge=0, le=1000000)
    presence_probability: Fraction | None
    evidence_id: Name
    measure: Literal["persons_with_presence_probability"] = "persons_with_presence_probability"


class SpatialHarmField(Contract):
    source_kind: Literal["declared_conditional_field", "consequence_exchange"]
    source_record: dict
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    sensitivity_member: Literal["low", "high"] | None = None
    frame_id: Name
    evidence_id: Name

    @model_validator(mode="after")
    def source(self):
        if digest(self.source_record) != self.source_digest:
            raise ValueError("conditional field source changed")
        if (self.source_kind == "consequence_exchange") != (self.sensitivity_member is not None):
            raise ValueError("native consequence fields require an explicit sensitivity member")
        return self


class JointCasualtyState(Contract):
    fatalities_by_cell: dict[Name, Annotated[int, Field(ge=0, le=1000000)]]
    probability: Fraction


class JointCasualtyDistribution(Contract):
    states: tuple[JointCasualtyState, ...] = Field(min_length=1, max_length=10001)
    dependence_evidence_id: Name
    source_record: dict
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    includes_presence_once: Literal[True] = True
    interpretation: Literal["joint_integer_cell_counts_given_event_and_condition"] = (
        "joint_integer_cell_counts_given_event_and_condition"
    )

    @model_validator(mode="after")
    def distribution(self):
        if digest(self.source_record) != self.source_digest:
            raise ValueError("joint casualty source changed")
        if self.source_record.get("states") != [x.model_dump(mode="json") for x in self.states]:
            raise ValueError("joint spatial casualty states differ from original source")
        counts = [digest(x.fatalities_by_cell) for x in self.states]
        if len(set(counts)) != len(counts) or not math.isclose(
            math.fsum(x.probability for x in self.states), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("unique integer casualty count states must close to probability one")
        return self


class SpatialCondition(Contract):
    id: Name
    probability_given_event: Fraction
    population: tuple[SpatialPopulation, ...] = Field(min_length=1, max_length=10000)
    field: SpatialHarmField
    joint_casualties: JointCasualtyDistribution | None = None
    evidence_id: Name


class SpatialEvent(Contract):
    id: Name
    initiating_condition_id: Name
    frequency: RiskFrequency | None
    conditions: tuple[SpatialCondition, ...] = Field(min_length=1, max_length=100)
    partition_evidence_id: Name
    frequency_condition_independence_evidence_id: Name

    @model_validator(mode="after")
    def partition(self):
        if len({c.id for c in self.conditions}) != len(self.conditions) or not math.isclose(
            math.fsum(c.probability_given_event for c in self.conditions), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("unique conditional branches must partition each initiating event")
        return self


class SpatialRiskRequest(Contract):
    id: Name
    grid: SpatialGrid
    events: tuple[SpatialEvent, ...] = Field(min_length=1, max_length=100)
    clock_id: Name
    window_start_s: Nonnegative
    window_finish_s: Nonnegative
    time_scope_evidence_id: Name
    scenario_relation: Literal["distinct_event_types", "independent_event_processes"]
    nonoverlapping_event_counting_evidence_id: Name
    fn_thresholds: tuple[int, ...] = Field(default=(1, 2, 5, 10), max_length=1000)
    units: dict[str, str] = Field(default_factory=lambda: dict(UNITS))
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    owner_pins: dict = Field(default_factory=dict)
    maximum_native_rows: int = Field(default=100000, ge=1, le=100000)

    @model_validator(mode="after")
    def shape(self):
        if self.window_finish_s <= self.window_start_s:
            raise ValueError("a positive explicitly conditioned consequence window is required")
        if self.units != UNITS or set(self.reviews) != set(REVIEWS):
            raise ValueError("units and named scope reviews must match the contract")
        if len({e.id for e in self.events}) != len(self.events):
            raise ValueError("initiating event identifiers must be unique")
        if not self.fn_thresholds or any(type(n) is not int or n < 1 for n in self.fn_thresholds):
            raise ValueError("F-N thresholds must be positive integers")
        if tuple(sorted(set(self.fn_thresholds))) != self.fn_thresholds:
            raise ValueError("F-N thresholds must be unique and increasing")
        return self


def _tree(root):
    rows = {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(root.rglob("*.py"))}
    return {"python_files": len(rows), "source_sha256": digest(rows)}


def native_backend_metadata():
    """Explicit optional-native discovery; normal facade import never calls this."""
    packages = {}
    for name in ("lh2_tno", "prism_qra", "human_reliability"):
        module = importlib.import_module(name)
        root = Path(module.__file__).parent
        license_path = next((p / "LICENSE" for p in root.parents[:4] if (p / "LICENSE").is_file()), None)
        if license_path is None:
            from importlib.metadata import distribution

            dist = distribution("human-reliability-toolkit" if name == "human_reliability" else "prism-risk")
            license_path = next(
                (Path(dist.locate_file(p)) for p in dist.files if str(p).endswith("licenses/LICENSE")), None
            )
        if license_path is None:
            raise ValueError(f"{name} requires accessible original license evidence")
        if "MIT License" not in license_path.read_text(encoding="utf-8")[:100]:
            raise ValueError(f"{name} has not retained the inspected MIT license")
        packages[name] = {
            **_tree(root),
            "version": getattr(module, "__version__", "bundled_in_prism-risk"),
            "license": "MIT",
            "license_sha256": file_digest(license_path),
        }
    import platform
    from importlib.metadata import version

    return {
        "packages": packages,
        "runtime": {
            "python": platform.python_version(),
            **{n: version(n) for n in ("numpy", "scipy", "pydantic")},
        },
        "operations": [
            "lh2_tno.risk.annual_risk",
            "prism_qra.risk.fn_curve:expected_death_threshold",
            "human_reliability.event_tree.event_tree",
            "human_reliability.event_tree.fn_curve:integer_casualty_exceedance",
        ],
    }


def _checked(value, cls):
    # Python validation precedes JSON conversion so nonfinite model_copy values cannot become null.
    return cls.model_validate(value.model_dump(mode="python"))


def _evidence(request, context):
    ids = [e.id for e in context.evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("context evidence identifiers must be unique")
    used = {
        request.grid.evidence_id,
        request.time_scope_evidence_id,
        request.nonoverlapping_event_counting_evidence_id,
    }
    for event in request.events:
        used.update((event.partition_evidence_id, event.frequency_condition_independence_evidence_id))
        if event.frequency:
            used.update(event.frequency.evidence_ids)
        for condition in event.conditions:
            used.update((condition.evidence_id, condition.field.evidence_id))
            used.update(p.evidence_id for p in condition.population)
            if condition.joint_casualties:
                used.add(condition.joint_casualties.dependence_evidence_id)
    if not used.issubset(ids):
        raise ValueError("all spatial, exposure and dependence evidence must be retained in context")


def _native_matches(actual, expected, label):
    """Audit retained primitives with the existing native arithmetic precision."""
    if isinstance(expected, dict):
        valid = isinstance(actual, dict) and set(actual) == set(expected)
        if valid:
            for key in expected:
                _native_matches(actual[key], expected[key], label)
            return
    elif isinstance(expected, (list, tuple)):
        valid = isinstance(actual, (list, tuple)) and len(actual) == len(expected)
        if valid:
            for a, e in zip(actual, expected, strict=True):
                _native_matches(a, e, label)
            return
    elif isinstance(expected, bool):
        valid = actual is expected
    elif type(expected) in (int, float):
        valid = (
            type(actual) in (int, float)
            and math.isfinite(actual)
            and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-14)
        )
    else:
        valid = actual == expected
    if not valid:
        raise ValueError(f"native {label} differs from its retained primitive replay")


def _native_primitive_harm(row, data, native_request):
    """Use owner dose/union/damage APIs; the upstream spatial field itself is retained, not solved."""
    worker = importlib.import_module("lh2_tno.chain_worker")
    # Called only inside our isolated primitive-audit subprocess.  The legacy
    # owner modules use flat imports; the owner's loader checks their origins.
    qra_root = Path(importlib.import_module("prism_qra").__file__).parent.resolve()
    module = importlib.import_module("lh2_tno.worker").module
    risk, damage, fire, ignition = (module(name, qra_root) for name in ("risk", "damage", "fire", "ignition"))
    q = native_request
    times, horizon, td = list(q.evaluation_times_s), q.evaluation_times_s[-1], q.ignition.delayed_time_s
    history = row["concentration_history"]
    if [v["time_s"] for v in history] != times or not all(
        type(v["h2_mole_fraction"]) in (int, float) and 0 <= v["h2_mole_fraction"] <= 1 for v in history
    ):
        raise ValueError("native concentration clock or conditional probability support changed")
    for point in history:
        _native_matches(point["h2_volume_percent"], 100 * point["h2_mole_fraction"], "concentration units")
    cloud = data["cloud_history_unignited_counterfactual"]
    if [v["time_s"] for v in cloud] != times:
        raise ValueError("native cloud observation clock changed")
    at_ignition = next((v for v in cloud if v["time_s"] == td), None)
    if at_ignition is None:
        raise ValueError("native ignition has no retained cloud observation")
    fm = at_ignition["flammable_mass_in_domain_kg"]
    if type(fm) not in (int, float) or fm < 0:
        raise ValueError("native flammable inventory is invalid")
    explosion = data["explosion"]
    r03, r01 = ignition.multi_energy_radii(
        fm * fire.H2_HEAT_OF_COMBUSTION, q.ignition.obstructed_energy_fraction
    )
    for key, expected in {
        "ignition_time_s": td,
        "flammable_mass_in_domain_kg": fm,
        "cloud_centroid_enu_m": at_ignition["flammable_centroid_enu_m"],
        "combustion_energy_j": fm * fire.H2_HEAT_OF_COMBUSTION,
        "obstructed_energy_fraction": q.ignition.obstructed_energy_fraction,
        "radius_0_3_barg_m": r03,
        "radius_0_1_barg_m": r01,
    }.items():
        _native_matches(explosion[key], expected, "explosion geometry/energy")
    concentration = next(v["h2_mole_fraction"] for v in history if v["time_s"] == td)
    flash = damage.flash_fire(float(concentration >= 0.04)).pe if fm > 0 else 0.0
    distance = math.dist(row["position_enu_m"], at_ignition["flammable_centroid_enu_m"])
    band, pressure = (
        (">=0.3 barg", 0.3)
        if fm > 0 and distance <= r03
        else ("0.1 to 0.3 barg", 0.1)
        if fm > 0 and distance <= r01
        else ("<0.1 barg", 0.0)
    )
    blast = damage.overpressure(pressure)
    for key, expected in {
        "flash_fire_envelope_harm": flash,
        "explosion_overpressure_band": band,
        "blast_individual_harm": blast.pe,
        "blast_indoor_fraction_of_deaths": blast.fe_in,
    }.items():
        _native_matches(row[key], expected, "flash/overpressure harm")
    source_steps = data["source"]["steps"]
    if [r["time_s"] for r in source_steps] != [r.time_s for r in q.resolved_release.steps]:
        raise ValueError("native source primitive clock differs from the original provider packet")
    clock = [0.0, horizon, q.resolved_release.duration_s, td, td + q.ignition.burn_back_duration_s]
    clock += [r["time_s"] for r in source_steps]
    if data["pool"] is not None:
        clock += [r["time_s"] for r in data["pool"]["ledger"]]
    clock = sorted(set(clock))
    for key in (
        "thermal_flux_direct_low_high",
        "thermal_flux_delayed_flash_low_high",
        "thermal_direct_low_high",
        "thermal_delayed_low_high",
        "branch_harm_low_high",
    ):
        if len(row[key]) != 2:
            raise ValueError("native thermal/branch fields must retain exactly two sensitivity members")
    branches = []
    for index in range(2):
        direct, delayed = (
            row["thermal_flux_direct_low_high"][index],
            row["thermal_flux_delayed_flash_low_high"][index],
        )
        for series in (direct, delayed):
            if [v["time_s"] for v in series] != clock or not all(
                type(v["flux_w_m2"]) in (int, float)
                and v["flux_w_m2"] >= 0
                and type(v["flame_contact"]) is bool
                for v in series
            ):
                raise ValueError("native thermal primitive clock, flux units or contact support changed")
        direct_harm = worker.dose_harm(direct, min(20.0, horizon), risk)
        delayed_harm = worker.dose_harm(
            [v for v in delayed if v["time_s"] >= td], min(td + 20.0, horizon), risk
        )
        residual_harm = worker.dose_harm(
            [v for v in direct if v["time_s"] >= td], min(td + 20.0, horizon), risk
        )["harm_probability"]
        _native_matches(row["thermal_direct_low_high"][index], direct_harm, "direct thermal dose/harm")
        _native_matches(row["thermal_delayed_low_high"][index], delayed_harm, "delayed thermal dose/harm")
        branches.append(
            {
                "direct_fire": direct_harm["harm_probability"],
                "delayed_flash_fire": worker.weighted_union_harm(flash, delayed_harm["harm_probability"]),
                "delayed_explosion": worker.weighted_union_harm(flash, blast.pe, residual_harm),
                "no_ignition": 0.0,
            }
        )
    _native_matches(row["branch_harm_low_high"], branches, "branch harm")
    return [
        math.fsum(data["ignition_branch_probabilities"][key] * branch[key] for key in branch)
        for branch in branches
    ]


def _run_primitive_worker():
    """Child-only import boundary; calls owner functions without changing host imports."""
    payload = json.load(sys.stdin)
    from importlib.metadata import version

    if {name: version(name) for name in payload["runtime"]} != payload["runtime"]:
        raise ValueError("native primitive runtime differs from the retained source environment")
    roots = {name: Path(value).resolve() for name, value in payload["roots"].items()}
    sys.path[:0] = [str(roots["prism_qra"]), str(roots["lh2_tno"].parent)]
    if file_digest(Path(__file__)) != payload["producer_sha256"] or any(
        _tree(root) != payload["source_pins"][name] for name, root in roots.items()
    ):
        raise ValueError("primitive worker source changed before replay")
    request = importlib.import_module("lh2_tno.consequences").ConsequenceRequest.model_validate_json(
        json.dumps(payload["request"], allow_nan=False)
    )
    result = {
        row["id"]: _native_primitive_harm(row, payload["data"], request)
        for row in payload["data"]["receptors"]
    }
    if file_digest(Path(__file__)) != payload["producer_sha256"] or any(
        _tree(root) != payload["source_pins"][name] for name, root in roots.items()
    ):
        raise ValueError("primitive owner source changed during replay")
    print(json.dumps(result, allow_nan=False))


def _replay_native_primitives(data, native_request, runtime):
    from importlib.metadata import version

    if {name: version(name) for name in runtime} != runtime:
        raise ValueError("native primitive runtime no longer matches the original source environment")
    roots = {
        name: Path(importlib.import_module(name).__file__).parent.resolve()
        for name in ("lh2_tno", "prism_qra")
    }
    payload = {
        "producer_sha256": file_digest(Path(__file__)),
        "roots": {name: str(root) for name, root in roots.items()},
        "source_pins": {name: _tree(root) for name, root in roots.items()},
        "request": native_request.model_dump(mode="json"),
        "data": data,
        "runtime": runtime,
    }
    code = "import sys;sys.path.insert(0,sys.argv[1]);from pipeline_integrity.spatial_risk import _run_primitive_worker;_run_primitive_worker()"
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", "-c", code, str(Path(__file__).resolve().parents[1])],
            input=json.dumps(payload, allow_nan=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("native primitive worker did not return a qualified audit") from error
    if result.returncode:
        raise ValueError("native primitive replay refused: " + result.stderr[-1500:])
    return {
        "method": "existing owner primitive-to-harm functions in an isolated interpreter; no spatial physics solve",
        "owner_source_pins": payload["source_pins"],
        "runtime": runtime,
        "primitive_input_digest": digest({"request": payload["request"], "data": data}),
        "harm_low_high": json.loads(result.stdout),
    }


def _field_rows(field, event, condition, request, context, audit_receipts=None):
    if field.frame_id != request.grid.frame_id:
        raise ValueError("field and population coordinate frames differ")
    if field.source_kind == "declared_conditional_field":
        source = field.source_record
        expected = {
            "schema": "pipeline_integrity.declared_spatial_harm.v1",
            "event_id": event.id,
            "condition_id": condition.id,
            "initiating_condition_id": event.initiating_condition_id,
            "context": context.model_dump(mode="json"),
            "grid_digest": digest(request.grid),
            "clock_id": request.clock_id,
            "window_start_s": request.window_start_s,
            "window_finish_s": request.window_finish_s,
            "harm_units": "1",
            "conditional_on": "event_and_condition_without_presence_or_frequency",
        }
        if any(source.get(k) != v for k, v in expected.items()):
            raise ValueError("declared field scope, clock, support or conditional meaning changed")
        rows = source["receptors"]
    else:
        original = ConsequenceResult.model_validate_json(json.dumps(field.source_record, allow_nan=False))
        original.check_integrity()
        from . import consequence_exchange

        if original.implementation_sha256 != file_digest(Path(consequence_exchange.__file__)):
            raise ValueError("the consequence producer changed since the retained native result")
        if condition.id != original.case.id or field.frame_id != original.case.facility["id"]:
            raise ValueError("native condition identity or facility ENU frame changed")
        original_context = original.context.model_dump(mode="json")
        target_context = context.model_dump(mode="json")
        original_evidence, target_evidence = original_context.pop("evidence"), target_context.pop("evidence")
        if (
            original_context != target_context
            or any(e not in target_evidence for e in original_evidence)
            or original.case.initiating_condition_id != event.initiating_condition_id
        ):
            raise ValueError("native consequence and annual assessment context differ")
        if original.source_record["context"] != original.context.model_dump(mode="json"):
            raise ValueError("native release and conditional-result contexts differ")
        packet = original.source_record["native_packet"]
        if digest(packet) != original.source_record["native_packet_digest"] or packet["event_id"] != event.id:
            raise ValueError("native conditional release identity changed")
        if (
            packet["provider_boundary_reference"]
            != f"accident-envelope:{original.source_record['history_envelope_digest']}"
            or original.source_record["initiating_frequency_or_consequence_calculated"] is not False
            or original.source_record["physical_actuation_or_field_validation"] is not False
        ):
            raise ValueError("native release provenance or source qualification changed")
        native_packet = packet
        if original.plane_evidence is not None:
            attachment = importlib.import_module("lh2_tno.compressible_handoff")
            native_packet, audit = attachment.attach_compressible_plane_handoff(
                packet, original.plane_evidence["export"]
            )
            if json_value(audit) != original.plane_evidence["attachment_audit"]:
                raise ValueError("native plane attachment audit changed")
        gas = importlib.import_module("lh2_tno.equipment_boundary").ResolvedGasRelease.model_validate_json(
            json.dumps(native_packet, allow_nan=False)
        )
        rebuilt = importlib.import_module(
            "lh2_tno.provider_release"
        ).build_consequence_request_from_provider_release(
            gas, dict(original.case.defaults) | {"occupancy_probability": 1.0}
        )
        q = original.native_request
        if rebuilt.model_dump(mode="json") != q:
            raise ValueError("native request differs from the original source packet and case defaults")
        native_request = importlib.import_module(
            "lh2_tno.consequences"
        ).ConsequenceRequest.model_validate_json(json.dumps(q, allow_nan=False))
        times = q["evaluation_times_s"]
        offset = original.source_record["provider_time_origin_in_context_s"]
        if (
            not times
            or any(b <= a for a, b in zip(times, times[1:], strict=False))
            or not all(type(t) in (int, float) and math.isfinite(t) for t in (*times, offset))
        ):
            raise ValueError("native consequence clock must be finite and strictly increasing")
        if [offset + times[0], offset + times[-1]] != [request.window_start_s, request.window_finish_s]:
            raise ValueError("native consequence time support does not equal the declared risk window")
        if (
            any(
                q.get(k) is not None
                for k in ("annual_frequency_per_year", "frequency_event", "initiation_intensity_per_s")
            )
            or q["occupancy_probability"] != 1.0
        ):
            raise ValueError("native conditional harm already includes frequency or presence")
        owner = importlib.import_module("lh2_tno")
        if original.provider["source_sha256"] != _tree(Path(owner.__file__).parent)["source_sha256"]:
            raise ValueError("native consequence provider source is no longer current")
        facility = {r["id"]: r["position_enu_m"] for r in original.case.facility["receptors"]}
        if original.status != "computed_diagnostic":
            return {key: {"position_enu_m": pos, "probability": None} for key, pos in facility.items()}
        native = original.native_result
        if original.issues or native["ok"] is not True or native["physical_validation_claim"] is not False:
            raise ValueError("native failure, issue or qualification cannot become a computed field")
        owner_contracts = importlib.import_module("lh2_tno.contracts")
        native_facility = owner_contracts.Facility.model_validate_json(
            json.dumps(original.case.facility, allow_nan=False)
        )
        component = next((c for c in native_facility.components if c.id == gas.component_id), None)
        port = next((p for p in native_facility.ports if p.id == gas.port_id), None)
        if (
            gas.component_id != original.context.asset_id
            or native_facility.environment != "outdoor"
            or component is None
            or component.property_basis != gas.property_basis
        ):
            raise ValueError(
                "native source asset or fluid/energy basis differs from its actual facility component"
            )
        if (
            port is None
            or port.component_id != gas.component_id
            or port.direction == "in"
            or port.phase not in {"vapour", "any"}
            or (port.position_enu_m is not None and port.position_enu_m != gas.position_enu_m)
        ):
            raise ValueError(
                "native source exit position, phase or direction differs from the original facility port"
            )
        if native["facility_digest"] != native_facility.digest():
            raise ValueError("native facility receipt does not bind the original geometry")
        owner_consequences = importlib.import_module("lh2_tno.consequences")
        payload = rebuilt.model_dump(mode="json")
        payload["source_input"] = owner_consequences.resolved_source_context(gas, rebuilt.weather)
        payload["receptors"] = [r.model_dump(mode="json") for r in native_facility.receptors]
        if native["request_digest"] != owner_contracts.canonical_digest(payload):
            raise ValueError("native worker payload receipt does not bind the original request and receptors")
        if native["adapter_source_digest"] != original.provider["source_sha256"] or native[
            "worker_sha256"
        ] != file_digest(Path(owner.__file__).with_name("worker.py")):
            raise ValueError("native worker or adapter source receipt changed")
        if native["environment"]["dependencies"] != original.provider["required_versions"]:
            raise ValueError("native runtime receipt differs from the original provider environment")
        data = original.native_result["data"]
        if (
            data["status"] != "END_TO_END_DIAGNOSTIC_EXECUTED"
            or data["facility_validation"] != "WITHHELD"
            or data["full_chain_executed"] is not True
            or data["physical_validation_claim"] is not False
        ):
            raise ValueError("native field execution or qualification changed")
        if (
            data["explosion"]["domain_truncated"] is not False
            or data["explosion"]["domain_truncation_reasons"]
        ):
            raise ValueError("native unresolved field support cannot become a computed risk map")
        if digest(q["resolved_release"]) != digest(native_packet) or digest(
            data["source"]["provider_boundary"]
        ) != digest(native_packet):
            raise ValueError("native source boundary differs from the retained release")
        if (
            data["source"]["discharge_recalculated"] is not False
            or data["source"]["equipment_dynamics_recalculated"] is not False
        ):
            raise ValueError("native conditional field changed source ownership")
        ledger = data["mass_ledger"]
        released = packet["cumulative_mass_out_kg"]
        if (
            not math.isclose(ledger["released_kg"], released, rel_tol=1e-8, abs_tol=0)
            or abs(ledger["end_to_end_residual_kg"]) > 1e-8 * released
        ):
            raise ValueError("native source-to-cloud mass accounting changed")
        member = 0 if field.sensitivity_member == "low" else 1
        weights = data["ignition_branch_probabilities"]
        expected_weights = native_request.ignition.probabilities()
        if set(weights) != set(expected_weights) or any(
            not math.isclose(weights[k], value, rel_tol=1e-12, abs_tol=1e-14)
            for k, value in expected_weights.items()
        ):
            raise ValueError("native ignition weights differ from the original native request")
        if not all(type(v) in (float, int) and 0 <= v <= 1 for v in weights.values()) or not math.isclose(
            math.fsum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-12
        ):
            raise ValueError("native ignition partition no longer closes")
        rows = []
        if len(data["receptors"]) != len(facility):
            raise ValueError("native field receptor support changed")
        primitive_receipt = _replay_native_primitives(
            data, native_request, original.provider["required_versions"]
        )
        replayed_harms = primitive_receipt["harm_low_high"]
        if audit_receipts is not None:
            audit_receipts.append(
                {
                    "operation": "original_native_conditional_field_audit",
                    "event_id": event.id,
                    "condition_id": condition.id,
                    "original_result_digest": original.result_digest,
                    "rebuilt_request_digest": rebuilt.digest(),
                    "worker_payload_digest": native["request_digest"],
                    "facility_digest": native["facility_digest"],
                    "primitive_replay": primitive_receipt,
                }
            )
        for row in data["receptors"]:
            if row["position_enu_m"] != facility.get(row["id"]):
                raise ValueError("native receptor positions changed")
            angle = math.radians((q["weather"]["wind_from_bearing_deg"] + 180.0) % 360.0)
            east, north, height = row["position_enu_m"]
            coordinates = (
                east * math.sin(angle) + north * math.cos(angle),
                east * math.cos(angle) - north * math.sin(angle),
                height,
            )
            if any(
                x < a - 1e-12 or x > b + 1e-12
                for x, (a, b) in zip(
                    coordinates,
                    (
                        q["domain"]["downwind_bounds_m"],
                        q["domain"]["crosswind_bounds_m"],
                        q["domain"]["height_bounds_m"],
                    ),
                    strict=True,
                )
            ):
                raise ValueError("retained native receptor lies outside its actual field domain")
            if any(
                row.get(k) is not None
                for k in (
                    "annual_current_weather_contribution_low_high",
                    "first_initiation_harm_probability_low_high",
                )
            ):
                raise ValueError("native field was already weighted by initiating risk")
            pair = row["harm_given_observed_release_low_high"]
            replayed_pair = replayed_harms[row["id"]]
            _native_matches(pair, replayed_pair, "conditional harm")
            if (
                len(pair) != 2
                or not all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in pair)
                or pair[0] > pair[1]
            ):
                raise ValueError("native conditional sensitivity pair changed meaning or range")
            p = pair[member]
            branch = row["branch_harm_low_high"][member]
            if set(branch) != set(weights) or not all(
                type(v) in (int, float) and 0 <= v <= 1 for v in branch.values()
            ):
                raise ValueError("native conditional branch probabilities changed")
            if not math.isclose(
                p, math.fsum(weights[k] * branch[k] for k in weights), rel_tol=1e-12, abs_tol=1e-14
            ):
                raise ValueError("native harm differs from its retained conditional branch ledger")
            rows.append({"id": row["id"], "position_enu_m": row["position_enu_m"], "probability": p})
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("field receptor identifiers repeat")
    for row in rows:
        p = row["probability"]
        if p is not None and (type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1):
            raise ValueError("conditional harm must be unknown or a finite probability")
        if len(row["position_enu_m"]) != 3 or not all(
            type(x) in (int, float) and math.isfinite(x) for x in row["position_enu_m"]
        ):
            raise ValueError("field positions must be finite ENU triples")
    return {r["id"]: r for r in rows}


def evaluate_spatial_risk(request: SpatialRiskRequest, context: RunContext):
    request, context = _checked(request, SpatialRiskRequest), _checked(context, RunContext)
    if context.clock_id != request.clock_id:
        raise ValueError("risk window and actual context clocks differ")
    _evidence(request, context)
    cells = request.grid.cells()
    cell_ids = {c["id"] for c in cells}
    conditions = [c for e in request.events for c in e.conditions]
    row_count = len(cells) * len(conditions) + sum(
        len(c.joint_casualties.states) * (len(request.fn_thresholds) + len(c.population))
        for c in conditions
        if c.joint_casualties
    )
    for event in request.events:
        if event.frequency is not None and (
            event.frequency.id != event.id
            or event.frequency.endpoint != "loss_of_containment"
            or event.frequency.condition_id != event.initiating_condition_id
            or event.frequency.context != context
        ):
            raise ValueError("spatial initiating rate must retain exact release/event/context meaning")
        for condition in event.conditions:
            if {p.cell_id for p in condition.population} != cell_ids or len(condition.population) != len(
                cells
            ):
                raise ValueError(
                    "every condition must explicitly declare every population cell, including unknown and zero"
                )
            receptors = [p.receptor_id for p in condition.population if p.receptor_id is not None]
            if len(set(receptors)) != len(receptors):
                raise ValueError("population groups must use distinct representative receptors")
    pending = []
    if not all(request.reviews.values()):
        pending.append("scope_review_required")
    if not request.owner_pins:
        pending.append("reviewed_owner_source_pins_required")
    if row_count > request.maximum_native_rows:
        pending.append("native_work_budget_exceeded")
    body = {
        "schema": "pipeline_integrity.spatial_risk.v1",
        "request": request.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "cells": cells,
        "status": "needs_data",
        "issues": pending,
        "provider": None,
        "maps": None,
        "outcomes": None,
        "native_receipts": None,
        "expected_death_threshold_curve": None,
        "integer_casualty_fn_curve": None,
        "physical_validation_claim": False,
        "complete_facility_risk_claim": False,
        "semantics": {
            "fixed_location_ir": "annualized declared-window fatality rate, continuously present person; year^-1",
            "present_person_ir": "same rate with condition-specific presence applied once; year^-1",
            "expected_fatalities": "disjoint persons times present-person rate; person/year",
            "fn": "integer joint casualty exceedance supplied independently; never inferred from expected deaths",
            "support": "cell-center representative receptors; no interpolation, area averaging, CRS transform or outside zero fill",
            "scenario_sum": "additive event counts, not a union probability or simultaneous-accident model",
        },
    }
    if pending:
        return {**body, "result_digest": digest(body)}
    before = native_backend_metadata()
    if before != request.owner_pins:
        raise ValueError("reviewed native source, license or runtime pins changed")
    pr = importlib.import_module("lh2_tno.risk")
    legacy = importlib.import_module("prism_qra.risk")
    hra = importlib.import_module("human_reliability.event_tree")
    fixed, present, expected = (
        {c["id"]: [] for c in cells},
        {c["id"]: [] for c in cells},
        {c["id"]: [] for c in cells},
    )
    missing = {c["id"]: [] for c in cells}
    fixed_missing = {c["id"]: [] for c in cells}
    present_missing = {c["id"]: [] for c in cells}
    receipts, outcomes, ledger, distributions, legacy_outcomes = [], [], [], {}, []
    fn_missing = []
    for event in request.events:
        native_branches = tuple(
            hra.Branch(
                c.id, c.probability_given_event, consequence_id=f"{event.id}/{c.id}", evidence=c.evidence_id
            )
            for c in event.conditions
        )
        event_ledger = (
            None
            if event.frequency is None
            else hra.event_tree(event.frequency.annual_frequency_per_year, native_branches)
        )
        receipts.append({"event_id": event.id, "event_tree": json_value(event_ledger)})
        if event_ledger:
            ledger.extend(event_ledger)
        for condition in event.conditions:
            key = f"{event.id}/{condition.id}"
            field = _field_rows(condition.field, event, condition, request, context, receipts)
            population = {p.cell_id: p for p in condition.population}
            conditional_expected, supported = [], True
            for cell in cells:
                cid, pop = cell["id"], population[cell["id"]]
                row = field.get(pop.receptor_id)
                harm = None if row is None else row["probability"]
                if row is not None and not all(
                    math.isclose(a, b, rel_tol=0, abs_tol=1e-10)
                    for a, b in zip(row["position_enu_m"], cell["position_enu_m"], strict=True)
                ):
                    raise ValueError(
                        "representative receptor is not the exact population cell center in the declared frame"
                    )
                if (
                    harm is None
                    or pop.persons is None
                    or pop.presence_probability is None
                    or event.frequency is None
                ):
                    missing[cid].append(key)
                if harm is None or event.frequency is None:
                    fixed_missing[cid].append(key)
                if harm is None or pop.presence_probability is None or event.frequency is None:
                    present_missing[cid].append(key)
                if harm is not None:
                    for target, occupancy in ((fixed[cid], 1.0), (present[cid], pop.presence_probability)):
                        if occupancy is not None:
                            target.append(
                                pr.AnnualScenario(
                                    id=key,
                                    initiating_event_id=event.id,
                                    weather_class=condition.id,
                                    frequency_per_year=None
                                    if event.frequency is None
                                    else event.frequency.annual_frequency_per_year,
                                    harm_probability_given_event=harm,
                                    annual_weather_probability=condition.probability_given_event,
                                    occupancy_probability=occupancy,
                                )
                            )
                if harm is None or pop.persons is None or pop.presence_probability is None:
                    conditional_expected.append(None)
                    supported = False
                else:
                    # Expected count accounting is linear; it does not identify a casualty distribution.
                    conditional_expected.append(pop.persons * pop.presence_probability * harm)
            mean = math.fsum(conditional_expected) if supported else None
            frequency = (
                None
                if event.frequency is None
                else event.frequency.annual_frequency_per_year * condition.probability_given_event
            )
            outcomes.append(
                {
                    "id": key,
                    "annual_frequency_per_year": frequency,
                    "conditional_expected_fatalities": mean,
                    "cell_expected_fatalities": conditional_expected,
                }
            )
            if mean is not None and frequency is not None:
                legacy_outcomes.append(legacy.SocietalOutcome(frequency, mean))
            joint = condition.joint_casualties
            if joint is None or mean is None or frequency is None:
                fn_missing.append(key)
            else:
                joint_scope = {
                    "schema": "pipeline_integrity.joint_spatial_casualties.v1",
                    "event_id": event.id,
                    "condition_id": condition.id,
                    "context_digest": digest(context),
                    "grid_digest": digest(request.grid),
                    "field_source_digest": condition.field.source_digest,
                    "population_digest": digest(condition.population),
                    "window_start_s": request.window_start_s,
                    "window_finish_s": request.window_finish_s,
                    "includes_presence_once": True,
                }
                if any(joint.source_record.get(k) != v for k, v in joint_scope.items()):
                    raise ValueError(
                        "joint casualty source is not bound to these exact spatial marginals and time scope"
                    )
                totals = {}
                for state in joint.states:
                    if set(state.fatalities_by_cell) != cell_ids or any(
                        count > population[cid].persons for cid, count in state.fatalities_by_cell.items()
                    ):
                        raise ValueError(
                            "joint casualty state must cover every cell within its integer population"
                        )
                    total = sum(state.fatalities_by_cell.values())
                    totals.setdefault(total, []).append(state.probability)
                for cell, required_mean in zip(cells, conditional_expected, strict=True):
                    cell_mean = math.fsum(
                        state.fatalities_by_cell[cell["id"]] * state.probability for state in joint.states
                    )
                    if not math.isclose(cell_mean, required_mean, rel_tol=1e-10, abs_tol=1e-12):
                        raise ValueError(
                            "joint per-cell casualty mean differs from its own harm and presence marginals"
                        )
                distributions[key] = {total: math.fsum(weights) for total, weights in totals.items()}
            for cell, count in zip(cells, conditional_expected, strict=True):
                if count is not None and frequency is not None:
                    expected[cell["id"]].append(frequency * count)
    maps = []
    for cell in cells:
        cid = cell["id"]
        # The native partition API requires every branch.  Incomplete cells are not padded with zero.
        native_fixed = None if fixed_missing[cid] else pr.annual_risk(tuple(fixed[cid]))
        native_present = None if present_missing[cid] else pr.annual_risk(tuple(present[cid]))
        maps.append(
            {
                "cell_id": cid,
                "fixed_location_ir_per_year": None if native_fixed is None else native_fixed["value"],
                "present_person_ir_per_year": None if native_present is None else native_present["value"],
                "expected_fatalities_per_year": None if missing[cid] else math.fsum(expected[cid]),
                "known_expected_fatalities_per_year": math.fsum(expected[cid]),
                "unresolved_conditions": missing[cid],
                "native_fixed": native_fixed,
                "native_present": native_present,
            }
        )
    n, f = legacy.fn_curve(legacy_outcomes)
    legacy_complete = all(
        x["conditional_expected_fatalities"] is not None and x["annual_frequency_per_year"] is not None
        for x in outcomes
    )
    true_fn = (
        None
        if fn_missing
        else {str(k): v for k, v in hra.fn_curve(ledger, distributions, request.fn_thresholds).items()}
    )
    receipts.append(
        {
            "operation": "integer_casualty_fn_curve",
            "distributions": {key: {str(n): p for n, p in pmf.items()} for key, pmf in distributions.items()},
            "thresholds": list(request.fn_thresholds),
            "result": true_fn,
        }
    )
    if native_backend_metadata() != before:
        raise ValueError("native source or runtime changed during spatial risk evaluation")
    body.update(
        {
            "status": "computed_diagnostic" if not any(missing.values()) else "partial",
            "issues": sorted({f"unresolved:{x}" for values in missing.values() for x in values}),
            "provider": before,
            "maps": maps,
            "outcomes": outcomes,
            "native_receipts": receipts,
            "expected_death_threshold_curve": {
                "value": {"expected_deaths": n.tolist(), "frequency_per_year": f.tolist()}
                if legacy_complete
                else None,
                "known_contribution": {"expected_deaths": n.tolist(), "frequency_per_year": f.tolist()},
                "meaning": "frequency of outcomes whose EXPECTED death count exceeds the displayed real-valued threshold; not P(integer fatalities >= N)",
            },
            "integer_casualty_fn_curve": {
                "value": true_fn,
                "unresolved_conditions": fn_missing,
                "meaning": "annual frequency of at least N integer fatalities; supplied joint PMFs include presence once",
            },
        }
    )
    return {**body, "result_digest": digest(body)}


def verify_spatial_risk_record(record, *, request=None, context=None):
    """Recreate native accounting and every semantic export from retained original inputs."""
    try:
        json_value(record)
        if record["result_digest"] != digest({k: v for k, v in record.items() if k != "result_digest"}):
            raise ValueError("spatial result checksum changed")
        restored = SpatialRiskRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        original_context = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if request is not None and _checked(request, SpatialRiskRequest) != restored:
            raise ValueError("caller request differs from the retained original")
        if context is not None and _checked(context, RunContext) != original_context:
            raise ValueError("caller context differs from the retained original")
        replay = evaluate_spatial_risk(restored, original_context)
        if replay != record:
            raise ValueError(
                "stored spatial geometry, accounting, source, native receipts or status differ from replay"
            )
        return {"valid": True, "issues": []}
    except (ValueError, TypeError, KeyError, ImportError, AttributeError) as error:
        return {"valid": False, "issues": [str(error)]}


def to_signal_frames(record, context: RunContext, *, allow_partial=False):
    if not verify_spatial_risk_record(record, context=context)["valid"]:
        raise ValueError("spatial record or actual caller context failed semantic replay")
    if record["status"] != "computed_diagnostic" and not (allow_partial and record["status"] == "partial"):
        raise ValueError("only computed spatial maps or explicitly accepted partial maps can export")
    frames = []
    for row in record["maps"]:
        values = {
            k: row[k]
            for k in (
                "fixed_location_ir_per_year",
                "present_person_ir_per_year",
                "expected_fatalities_per_year",
            )
        }
        frames.append(
            SignalFrame(
                stream_id=f"{context.asset_id}/{context.scenario_id}/{record['request']['id']}/{row['cell_id']}",
                clock_id=context.clock_id,
                time_s=record["request"]["window_start_s"],
                values=values,
                units={
                    k: "person/year" if k == "expected_fatalities_per_year" else "year^-1" for k in values
                },
                valid={k: v is not None for k, v in values.items()},
                origin="synthetic" if context.origin == "synthetic" else "simulation",
                operating_mode="annualized_declared_window_spatial_risk",
            )
        )
    return tuple(frames)
