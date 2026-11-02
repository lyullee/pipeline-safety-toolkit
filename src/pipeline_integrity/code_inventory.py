"""Source implementation accounting, independent of validation and field status.

The baseline catalog is fixed. Each previously partial capability is counted
once against its declared supported model, using a curated source review and
actual API declarations. Declaration inspection alone is not a physics audit.
"""
from __future__ import annotations

import ast
from pathlib import Path
from .capabilities import capabilities
from .contracts import digest
from .datasets import file_digest


BASELINE_CALCULATION_COUNT = 174
BASELINE_PROCEDURE_COUNT = 29
BASELINE_CALCULATION_IDS_SHA256 = "7cdf0956e9c8fa6399e64fecab78c5816b0666cac5752e53333995f65598efe4"
BASELINE_PROCEDURE_IDS_SHA256 = "b8aa7ffcec6ba9dbd47349438535618d461a7977ed96b14497ec0c09a7e0a75e"


SUPPORTED_SOURCE_APIS = {
    "knowledge.assistance": ("knowledge_native.NativeSagaLibraryClient",),
    "network.gas_general": ("gas_potential_network.evaluate_gas_potential_path","gas_potential_network.evaluate_gas_potential_plenums"),
    "transient.liquid_moc": ("transient.simulate_liquid_transient","pump_moc_exchange.evaluate_pump_moc"),
    "transient.gas_nonisothermal": ("gas_thermal_transient.evaluate_gas_thermal_transient",),
    "transient.multiphase": ("rupture_inventory.evaluate_rupture_inventory","cavity_moc.evaluate_cavity_moc","vapor_cavity_moc.evaluate_vapor_cavity_moc"),
    "release.line_rupture": ("rupture_inventory.evaluate_rupture_inventory",),
    "release.isolation": ("rupture_inventory.evaluate_rupture_inventory","valve_motion.simulate_valve_motion"),
    "transport.batch_interface": ("batch_transport_solver.evaluate_conservative_batch_transport",),
    "transport.pig_motion": ("pig_motion.evaluate_pig_motion",),
    "transport.slurry": ("heated_slurry.evaluate_heated_slurry","particle_settling.evaluate_particle_settling"),
    "transport.hydrate": ("hydrate_kinetics.evaluate_hydrate_kinetics","hydrate_kinetics.bind_hydrate_deposit_inventory"),
    "thermal.soil_coupling": ("fluid_soil_feedback.evaluate_fluid_soil_feedback","soil_moisture.evaluate_soil_moisture"),
    "thermal.buried_multi": ("soil_fem.evaluate_soil_fem",),
    "thermal.phase_change": ("spatial_phase_change.evaluate_spatial_phase_change","frost_growth.evaluate_frost_growth"),
    "thermal.heated_oil": ("adaptive_heated_transport.evaluate_adaptive_heated_transport",),
    "equipment.compressor_map": ("compressor_dynamics.evaluate_compressor_dynamics","compressor_control.evaluate_controlled_compressor"),
    "instrument.custody_transfer": ("constrained_metrology.evaluate_constrained_gls","temporal_metrology.evaluate_temporal_weighted_budget"),
    "instrument.sensor_faults": ("instrument_exchange.replay_transmitter","clock_epochs.evaluate_clock_epochs"),
    "leak.inverse_localization": ("inverse_localization.localize_leak",),
    "leak.acoustic_localization": ("acoustic_network.evaluate_acoustic_network_inverse","acoustic_multipath.evaluate_acoustic_multipath_fit"),
    "leak.negative_pressure_wave": ("pressure_wave.evaluate_pressure_wave",),
    "leak.sensor_placement": ("sensor_placement.plan_sensor_placement",),
    "leak.external_sensing": ("external_sensing.adapt_external_sensing","fiber_interferometry.evaluate_fiber_interferometry"),
    "integrity.defect_matching": ("inspection_evolution.evaluate_inspection_evolution",),
    "integrity.inspection_selection": ("inspection_evolution.plan_evolved_inspections",),
    "structural.beam_network": ("structural.solve_frame","component_stress.evaluate_component_stress"),
    "structural.soil_pipe": ("soil_pipe.evaluate_soil_pipe","gap_contact.evaluate_gap_contact_frame"),
    "structural.buckling": ("elastic_shell_ring.evaluate_elastic_shell_ring",),
    "structural.fracture_arrest": ("dynamic_fracture.evaluate_dynamic_fracture","dynamic_fracture.DynamicFractureProvider"),
    "risk.common_cause": ("common_cause.fault_tree_request_from_common_cause_counts",),
    "risk.dynamic_coupling": ("stochastic_process.evaluate_stochastic_process","stochastic_process.prepare_stochastic_period_logic"),
    "economics.optimization": ("continuous_optimization.evaluate_continuous_optimization",),
    "construction.route": ("route_generation.evaluate_route_generation","route_generation.prepare_generated_route_planning"),
    "construction.pull": ("elastic_pull.evaluate_elastic_pull","elastic_pull.prepare_elastic_span_guide_pull"),
    "tanks.breathing": ("hysteretic_tank.evaluate_hysteretic_tank","headspace_drainage.evaluate_headspace_drainage"),
    "control.regulation": ("control_modes.evaluate_regulation_modes",),
    "uncertainty.coupled": ("uncertainty.evaluate_ensemble","adapter_uncertainty.adapter_ensemble_node"),
    "hra.collection_risk_binding": ("human_collection_exchange.bind_human_collection_estimate",),
}

# Cleared only after reviewing the actual algorithm and its adapter handoff.
SOURCE_REVIEW_PENDING = frozenset()

BOUNDED_DOMAINS = {
    "knowledge.assistance": "Original SAGA source-qualified hybrid retrieval and reviewed assistance-provider replies; native lightweight autonomous reasoning is not inferred from retrieval.",
    "network.gas_general": "Native gas pressure/enthalpy network plus simultaneous serial total-energy/momentum and zero-velocity-plenum graph potential-energy models; arbitrary finite-velocity branch mixing is excluded.",
    "transient.liquid_moc": "Acoustic liquid characteristics, declared head/withdrawal/valve boundaries and simultaneous signed-map pump rotor coupling; pressure-domain and discretization limits remain enforced.",
    "transient.gas_nonisothermal": "Native finite mass/internal-energy/wall-temperature cells and pressure-driven network exchanges; no general distributed gas acoustic momentum PDE.",
    "transient.multiphase": "HEM shared inventories; supplied-gas DGCM; event-split one-pipe DVCM. No two-fluid slip or full thermal vapor closure.",
    "transport.hydrate": "Single-guest lumped fugacity-driven reaction/deposition with supplied equilibrium and calibrated rates; external bath temperature.",
    "release.line_rupture": "Native finite shared HEM inventories with simultaneous bidirectional restrictions and integrated boundary ledgers; no distributed rupture wave or atmospheric jet/phase fate.",
    "release.isolation": "Original finite actuator trajectories coupled to actual valve conductance, liquid MOC and shared HEM residual inventories; unsupported fluid/phase handoffs remain withheld.",
    "transport.batch_interface": "Conservative component-volume and sensible-energy FV advection/dispersion with additive incompressible volumes and fixed pure-component properties; flow and pressure are supplied.",
    "transport.pig_motion": "Rigid one-dimensional stick/slip motion with declared pressure histories, event, force-work and impulse ledgers; hydraulic seal/pipe-fluid feedback is not predicted.",
    "transport.slurry": "Native supported rheology/hydraulics and heated slurry coupling plus dilute spherical-particle settling; no general heterogeneous turbulent suspension/slip closure.",
    "thermal.soil_coupling": "Native radial solid conduction with actual fluid-energy feedback, reviewed frozen moisture-property stages and thin zero-storage contact resistance; no infiltration or groundwater PDE.",
    "thermal.buried_multi": "Reviewed polygonal multi-cavity FEM/FV conduction, boundary radiation and physical/weak energy audits; true curved facets and groundwater transport are excluded.",
    "thermal.phase_change": "Native boiling/condensing exchange, lumped/spatial enthalpy freezing and supplied-flux frost storage/growth; no inferred nucleation, phase slip or sharp-front trajectory.",
    "thermal.heated_oil": "Original property/hydraulic thermal march with outlet roots, adaptive subdivision and wall/fluid storage; unsupported material properties and unsteady hydraulic feedback remain explicit.",
    "equipment.compressor_map": "Reviewed same-fluid native stage maps/intercoolers, common-shaft inertia and sampled native-PID recycle feedback under imposed inlet conditioning; no surge/plenum-wave prediction.",
    "instrument.custody_transfer": "Supported EIV/calibration/application, constrained singular GLS, temporal covariance, totalization and reset epochs; unsupported joint time-reading error laws are not fitted implicitly.",
    "instrument.sensor_faults": "Original transmitter dynamics with declared drift, delay, dropout and clock scenarios plus explicit clock epochs; unsupported monitoring clock/availability combinations are withheld.",
    "leak.inverse_localization": "Actual reviewed forward-provider residual/inverse evaluation over finite location hypotheses and supported uncertainty; no unbounded geographic source search.",
    "leak.acoustic_localization": "Delay localization, reviewed travel-time networks, multiband intersection and known-excitation coherent multipath fitting; unknown-source leak multipath is not inferred.",
    "leak.negative_pressure_wave": "Sampled pressure-arrival, direction and error/support qualification; event cause and physical onset are not inferred from an arrival threshold alone.",
    "leak.sensor_placement": "Finite candidate sensor/design selection using actual correlated-noise information and resource scheduling; no arbitrary continuous network placement claim.",
    "leak.external_sensing": "Spatially supported processed measurements/censoring and calibrated coherent optical phase-to-fiber-strain conversion; no automatic host coupling or raw radiance retrieval.",
    "integrity.defect_matching": "Reviewed one-to-one coordinate registration and original material growth/detection evolution; split/merge identity and stochastic repair efficacy are excluded.",
    "integrity.inspection_selection": "Original POD/Bayes/resource planner with repeated growth/no-indication updates and source-qualified performed-repair resets; no unrestricted joint adaptive-policy optimum.",
    "structural.beam_network": "Native small-displacement elastic frames, thermal strain/curvature/section recovery, physical junction loads and reviewed local stress transfers; no inferred nonlinear elbow constitutive law.",
    "structural.soil_pipe": "Actual frame/ground displacement and supplied spring laws, with finite frictionless gap contact in the shared contact solver; no inferred nonlinear soil continuum.",
    "structural.buckling": "Frame eigenbuckling and nonlinear elastic long-cylinder section ring ovalization; no finite-length axial shell modes or plastic ultimate collapse.",
    "structural.fracture_arrest": "One-coordinate inertial fracture coupled to native EOS/HEM and reviewed geometry/energy/resistance; no distributed shell-wave fracture.",
    "risk.dynamic_coupling": "Finite-state piecewise Markov competing failure/recovery with reviewed deterministic deadlines and preserved probability meaning.",
    "risk.common_cause": "Observed grouped cause/joint-state counts and original Boolean risk evaluation with explicit probability meaning; no hidden common-cause rate or repair-parameter inference.",
    "economics.optimization": "Original finite operating-plan comparison/scheduling and bounded continuous actual-adapter objective/constraint search; global optimality is not asserted.",
    "construction.route": "Reviewed route screening and constrained supplied-corridor graph paths with terrain, turn and linear resource checks; no unsurveyed-terrain or permit feasibility inference.",
    "construction.pull": "Reviewed quasi-static guided flexible-line loads and native finite-EA fully suspended catenary/work coupling; finite-EI contact, impact and dynamic pipe buckling are excluded.",
    "tanks.breathing": "Original supported pure-fluid tank/vent/phase model with event-split instantaneous relay plus ideal headspace/filling/drainage coupling; unsupported atmospheric mixtures or entrainment remain withheld.",
    "control.regulation": "Original PID filtering/limits/state with reviewed manual/interlock arbitration, actual-output tracking and finite actuator/process handoffs; no functional-safety authorization.",
    "uncertainty.coupled": "Declared joint cases/copula/LHS/Sobol and actual registry/adapter forward ensembles with explicit conditional weights; field distributions or rare-event coverage are not inferred.",
    "hra.collection_risk_binding": "Original CollectionBundle reconciliation and HEP/event/opportunity risk binding under supported protocols; industrial population/field transfer qualification is separate.",
}


def code_implementation_progress():
    """Inspect reviewed source declarations; never import or execute model engines."""
    root=Path(__file__).parent
    cache={}
    def concrete_body(node):
        if isinstance(node,ast.ClassDef):
            methods=[item for item in node.body if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef))
                     and (not item.name.startswith("__") or item.name=="__call__")]
            return bool(methods) and all(concrete_body(item) for item in methods)
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            return False
        body=[item for item in node.body if not (isinstance(item,ast.Expr)
              and isinstance(item.value,ast.Constant) and isinstance(item.value.value,str))]
        def placeholder(item):
            if isinstance(item,ast.Pass):
                return True
            if isinstance(item,ast.Expr) and isinstance(item.value,ast.Constant) and item.value.value is Ellipsis:
                return True
            if isinstance(item,ast.Raise):
                error=item.exc.func if isinstance(item.exc,ast.Call) else item.exc
                return isinstance(error,ast.Name) and error.id=="NotImplementedError"
            return False
        return bool(body) and not all(placeholder(item) for item in body)
    def inspect_api(api):
        module,name=api.rsplit(".",1)
        path=root.joinpath(*module.split(".")).with_suffix(".py")
        if module not in cache:
            if not path.is_file():
                cache[module]=({},None)
            else:
                tree=ast.parse(path.read_text(encoding="utf-8-sig"))
                cache[module]=({node.name:node for node in tree.body
                                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))},file_digest(path))
        declarations,pin=cache[module]
        node=declarations.get(name)
        return dict(api="pipeline_integrity."+api,source_file=path.relative_to(root.parent).as_posix(),
                    source_sha256=pin,declaration_present=node is not None,
                    non_placeholder_body_present=concrete_body(node))
    rows=[]; procedures=[]
    catalog=capabilities()
    calculation_ids=sorted(cap.id for cap in catalog if cap.state!="procedure_required")
    procedure_ids=sorted(cap.id for cap in catalog if cap.state=="procedure_required")
    if len(calculation_ids)!=len(set(calculation_ids)) or len(procedure_ids)!=len(set(procedure_ids)):
        raise ValueError("code implementation baseline contains duplicate capability identities")
    if (len(calculation_ids)!=BASELINE_CALCULATION_COUNT or len(procedure_ids)!=BASELINE_PROCEDURE_COUNT
            or digest(calculation_ids)!=BASELINE_CALCULATION_IDS_SHA256 or digest(procedure_ids)!=BASELINE_PROCEDURE_IDS_SHA256):
        raise ValueError("code implementation baseline changed; review the scope explicitly before recalculating its percentage")
    if not set(SUPPORTED_SOURCE_APIS)<=set(calculation_ids) or not SOURCE_REVIEW_PENDING<=set(SUPPORTED_SOURCE_APIS):
        raise ValueError("curated code review identities must belong to the fixed calculation baseline")
    for cap in catalog:
        if cap.state=="procedure_required":
            procedures.append(cap.id)
            continue
        apis=SUPPORTED_SOURCE_APIS.get(cap.id)
        if apis is None:
            done=cap.state in {"implemented","interface_verified"}
            evidence=[]
            basis="existing_reviewed_calculation_or_owner_integration" if done else "unresolved_catalog_scope"
        else:
            evidence=[inspect_api(api) for api in apis]
            done=all(e["declaration_present"] and e["non_placeholder_body_present"] for e in evidence) and cap.id not in SOURCE_REVIEW_PENDING
            basis="curated_supported_algorithm_and_handoff_source_review"
        rows.append(dict(id=cap.id,title=cap.title_ko,code_complete=done,basis=basis,
                         source_apis=evidence,baseline_entrypoint=cap.entrypoint,
                         supported_domain=BOUNDED_DOMAINS.get(cap.id,"Declared public request/model support; out-of-scope inputs remain withheld.")))
    done=sum(r["code_complete"] for r in rows)
    shared=inspect_api("procedures.assess_procedure")
    body=dict(schema="pipeline_integrity.code_implementation_progress.v1",scope="capability_catalog",
              engineering_method_goal_included=False,capabilities=len(rows),
              code_complete=done,remaining=len(rows)-done,percent=100*done/len(rows) if rows else 0.0,
              basis="source implementation of declared supported models; each catalog capability counted once",
              rows=rows,shared_procedure_capabilities=procedures,shared_procedure_engine=shared,
              baseline_calculation_ids_sha256=BASELINE_CALCULATION_IDS_SHA256,
              baseline_procedure_ids_sha256=BASELINE_PROCEDURE_IDS_SHA256,
              procedure_site_qualification_inferred=False,
              declaration_inspection_alone_proves_implementation=False,
              model_engines_executed_by_inventory=False,numerical_validation_included=False,
              documentation_packaging_included=False,implementation_sha256=file_digest(Path(__file__)))
    body["record_digest"]=digest(body)
    return body
