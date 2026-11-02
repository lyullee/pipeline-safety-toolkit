"""Serial gas-path pressure/total-energy coupling using owner friction closure.

This supplies a serial-path capability alongside, rather than replacing, the
owner's branching static-enthalpy network. Every physical node has one pipe
area and a shared velocity. Momentum includes pressure, gravity, Darcy/local
loss and convective acceleration; energy includes h, velocity squared/2 and g*z.
Branch mixing, compressors, choking and flow reversal require other contracts.
"""
from __future__ import annotations

import inspect
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .thermo import CheckedProvider, CoolPropProvider, StateRequest
from .gas_network import GasNetwork, LH2GasNetworkBackend


class GasPotentialSegment(Contract):
    id: Name
    length_m: Positive
    end_minus_start_elevation_m: float
    roughness_m: Nonnegative
    local_loss_coefficient: Nonnegative = 0.0
    heat_into_fluid_W: float = 0.0
    wall_conductance_W_K: Nonnegative = 0.0
    ambient_temperature_K: Positive | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def thermal_boundary(self):
        if self.wall_conductance_W_K>0 and self.ambient_temperature_K is None:
            raise ValueError("positive wall conductance requires an ambient temperature")
        return self


class GasPotentialPathRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative = 0.0
    fluid: Name
    backend: Literal["HEOS", "PR", "SRK"] = "HEOS"
    property_basis_id: Name
    diameter_m: Positive
    inlet_pressure_Pa_abs: Positive
    inlet_temperature_K: Positive
    outlet_pressure_Pa_abs: Positive
    inlet_elevation_m: float = 0.0
    segments: tuple[GasPotentialSegment, ...] = Field(min_length=1, max_length=128)
    pressure_support_Pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    positive_mass_flow_support_kg_s: tuple[Positive, Positive]
    initial_mass_flow_kg_s: Positive
    initial_node_pressures_Pa_abs: tuple[Positive, ...]
    initial_node_temperatures_K: tuple[Positive, ...]
    expected_owner_pipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gravity_m_s2: Positive = 9.80665
    absolute_momentum_tolerance_Pa: Positive
    absolute_total_energy_tolerance_W: Positive
    relative_closure_tolerance: Positive = Field(default=1e-8, le=1e-3)
    maximum_mach: Positive = Field(default=0.1, le=0.1)
    maximum_segment_density_change: Positive = Field(default=0.05, le=0.1)
    maximum_segment_pressure_change: Positive = Field(default=0.05, le=0.1)
    maximum_solver_evaluations: int = Field(default=200, ge=1, le=10000)
    maximum_residual_evaluations: int = Field(default=10000, ge=1, le=1000000)
    maximum_property_resolutions: int = Field(default=100000, ge=1, le=10000000)
    maximum_result_bytes: int = Field(default=32000000, ge=1000, le=256000000)
    fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed: bool = False
    small_segment_property_variation_and_wall_heat_reviewed: bool = False
    owner_friction_and_common_thermodynamic_reference_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def path(self):
        n = len(self.segments)
        if len(self.initial_node_pressures_Pa_abs) != n or len(self.initial_node_temperatures_K) != n:
            raise ValueError("initial state arrays must identify every downstream path node")
        if len({s.id for s in self.segments}) != n or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("gas path segment and evidence IDs must be unique")
        for support in (self.pressure_support_Pa_abs,self.temperature_support_K,self.positive_mass_flow_support_kg_s):
            if support[0] >= support[1]:
                raise ValueError("gas path supports must be strictly ordered")
        if not self.positive_mass_flow_support_kg_s[0] <= self.initial_mass_flow_kg_s <= self.positive_mass_flow_support_kg_s[1]:
            raise ValueError("initial gas flow is outside the positive-flow search support")
        pressures=(self.inlet_pressure_Pa_abs,*self.initial_node_pressures_Pa_abs)
        if self.outlet_pressure_Pa_abs is not None:
            pressures=(*pressures,self.outlet_pressure_Pa_abs)
        if any(not self.pressure_support_Pa_abs[0] <= p <= self.pressure_support_Pa_abs[1] for p in pressures):
            raise ValueError("gas path pressure boundary/initial guess is outside support")
        if any(not self.temperature_support_K[0] <= t <= self.temperature_support_K[1]
               for t in (self.inlet_temperature_K,*self.initial_node_temperatures_K)):
            raise ValueError("gas path temperature boundary/initial guess is outside support")
        if any(s.roughness_m/self.diameter_m > 0.1 for s in self.segments):
            raise ValueError("relative roughness exceeds the declared Darcy closure range")
        return self


class GasPotentialForwardRequest(GasPotentialPathRequest):
    """Known positive mass flow; downstream pressures/temperatures are solved.

    Initial downstream pressures remain numerical guesses, not pressure boundary
    conditions. An outlet pressure may not also be prescribed in this mode.
    """
    outlet_pressure_Pa_abs: None = None
    prescribed_mass_flow_kg_s: Positive
    absolute_mass_flow_tolerance_kg_s: Positive = 1e-9

    @model_validator(mode="after")
    def fixed_mass_support(self):
        if not self.positive_mass_flow_support_kg_s[0] <= self.prescribed_mass_flow_kg_s <= self.positive_mass_flow_support_kg_s[1]:
            raise ValueError("prescribed mass flow is outside reviewed positive-flow support")
        return self


def gas_potential_path_metadata():
    from lh2dt import pipe, network, properties, contracts
    return {"gas_potential_path_sha256":file_digest(Path(__file__)),
            "thermo_sha256":file_digest(Path(__file__).with_name("thermo.py")),
            "gas_network_sha256":file_digest(Path(__file__).with_name("gas_network.py")),
            "energy_network_sha256":file_digest(Path(__file__).with_name("energy_network.py")),
            "owner_pipe_sha256":file_digest(Path(pipe.__file__)),
            "owner_network_sha256":file_digest(Path(network.__file__)),
            "owner_properties_sha256":file_digest(Path(properties.__file__)),
            "owner_contracts_sha256":file_digest(Path(contracts.__file__))}


def evaluate_gas_potential_path(request: GasPotentialPathRequest, context: RunContext, *, property_provider=None):
    import numpy as np
    import scipy
    from scipy.optimize import least_squares
    from scipy.sparse import lil_matrix
    from lh2dt.pipe import Pipe

    request_type=GasPotentialForwardRequest if isinstance(request,GasPotentialForwardRequest) else GasPotentialPathRequest
    q = request_type.model_validate(request.model_dump(mode="python"))
    forward=isinstance(q,GasPotentialForwardRequest)
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("gas potential path asset/clock differs from context")
    if not all((q.fixed_area_serial_positive_flow_and_no_branch_mixing_reviewed,
                q.small_segment_property_variation_and_wall_heat_reviewed,
                q.owner_friction_and_common_thermodynamic_reference_reviewed)):
        raise ValueError("serial-flow, segment closure and common property reference reviews are required")
    required = set(q.evidence_ids) | {s.evidence_id for s in q.segments}
    ids = [e.id for e in c.evidence]
    if len(set(ids)) != len(ids) or not required <= set(ids):
        raise ValueError("gas potential path source evidence is absent or ambiguous")
    pins = gas_potential_path_metadata()
    if pins["owner_pipe_sha256"] != q.expected_owner_pipe_sha256:
        raise ValueError("gas potential path owner friction source differs from the reviewed pin")
    raw_provider = CoolPropProvider(use_cache=False) if property_provider is None else property_provider
    provider_file = inspect.getsourcefile(type(raw_provider))
    if provider_file is None or not Path(provider_file).is_file():
        raise ValueError("gas potential provider must expose its actual source file")
    provider_pin = file_digest(Path(provider_file))
    provider = CheckedProvider(raw_provider)
    q_pin,c_pin = digest(q),digest(c)
    resolutions = evaluations = 0
    property_identity = None

    def resolve(p,t):
        nonlocal resolutions,property_identity
        resolutions += 1
        if resolutions > q.maximum_property_resolutions:
            raise ValueError("gas potential property-resolution budget exhausted")
        state = provider.resolve(StateRequest(fluid=q.fluid,backend=q.backend,inputs={"P":float(p),"T":float(t)}))
        if state.phase not in {"gas","supercritical_gas","supercritical"}:
            raise ValueError("gas potential path left the declared single-gas domain")
        if state.viscosity_pa_s is None or state.speed_of_sound_m_s is None:
            raise ValueError("gas potential closure requires actual viscosity and sound speed")
        if not math.isclose(state.pressure_pa_abs,float(p),rel_tol=1e-10) or not math.isclose(state.temperature_k,float(t),rel_tol=1e-10):
            raise ValueError("gas potential provider changed the queried pressure/temperature")
        identity = (state.provider_version,state.provider_revision)
        if property_identity is not None and identity != property_identity:
            raise ValueError("gas potential provider identity changed during evaluation")
        property_identity = identity
        return state

    inlet = resolve(q.inlet_pressure_Pa_abs,q.inlet_temperature_K)
    n = len(q.segments)
    area = math.pi*q.diameter_m**2/4
    if not math.isfinite(area) or area <= 0:
        raise ValueError("gas potential flow area is outside finite positive arithmetic")
    pressure_scale = max(q.inlet_pressure_Pa_abs,q.initial_node_pressures_Pa_abs[-1] if forward else q.outlet_pressure_Pa_abs)
    flow_scale = q.initial_mass_flow_kg_s
    temperature_scale = q.inlet_temperature_K
    # Unknowns: scaled mass flow followed by each downstream P,T pair.
    x0 = [q.initial_mass_flow_kg_s/flow_scale]
    lower = [q.positive_mass_flow_support_kg_s[0]/flow_scale]
    upper = [q.positive_mass_flow_support_kg_s[1]/flow_scale]
    for p,t in zip(q.initial_node_pressures_Pa_abs,q.initial_node_temperatures_K,strict=True):
        x0.extend((p/pressure_scale,t/temperature_scale))
        lower.extend((q.pressure_support_Pa_abs[0]/pressure_scale,q.temperature_support_K[0]/temperature_scale))
        upper.extend((q.pressure_support_Pa_abs[1]/pressure_scale,q.temperature_support_K[1]/temperature_scale))
    pattern = lil_matrix((2*n+1,2*n+1),dtype=int)
    for i in range(n):
        columns = [0,1+2*i,2+2*i]+([] if i == 0 else [2*i-1,2*i])
        for row in (2*i,2*i+1):
            for col in columns:
                pattern[row,col] = 1
    pattern[-1,0 if forward else 2*n-1] = 1

    def inspect_state(x):
        flow = float(x[0]*flow_scale)
        states = [inlet]+[resolve(x[1+2*i]*pressure_scale,x[2+2*i]*temperature_scale) for i in range(n)]
        rows = []
        normalized = []
        elevation = q.inlet_elevation_m
        for i,segment in enumerate(q.segments):
            left,right = states[i],states[i+1]
            rho = (left.density_kg_m3+right.density_kg_m3)/2
            viscosity = (left.viscosity_pa_s+right.viscosity_pa_s)/2
            reynolds = flow*q.diameter_m/(area*viscosity)
            friction = Pipe._friction_factor(reynolds,segment.roughness_m/q.diameter_m)
            v_left,v_right = flow/(area*left.density_kg_m3),flow/(area*right.density_kg_m3)
            friction_loss = (friction*segment.length_m/q.diameter_m+segment.local_loss_coefficient)*flow**2/(2*rho*area**2)
            gravity_head = rho*q.gravity_m_s2*segment.end_minus_start_elevation_m
            momentum_acceleration = (flow/area)**2*(1/right.density_kg_m3-1/left.density_kg_m3)
            pressure_drop = left.pressure_pa_abs-right.pressure_pa_abs
            momentum = pressure_drop-friction_loss-gravity_head-momentum_acceleration
            kinetic_delta = (v_right**2-v_left**2)/2
            potential_delta = q.gravity_m_s2*segment.end_minus_start_elevation_m
            enthalpy_delta = right.enthalpy_j_kg-left.enthalpy_j_kg
            wall_heat=(0.0 if segment.wall_conductance_W_K==0 else
                segment.wall_conductance_W_K*(segment.ambient_temperature_K-(left.temperature_k+right.temperature_k)/2))
            total_heat=segment.heat_into_fluid_W+wall_heat
            energy = flow*(enthalpy_delta+kinetic_delta+potential_delta)-total_heat
            momentum_tol = q.absolute_momentum_tolerance_Pa+q.relative_closure_tolerance*max(
                abs(pressure_drop),abs(friction_loss),abs(gravity_head),abs(momentum_acceleration))
            energy_tol = q.absolute_total_energy_tolerance_W+q.relative_closure_tolerance*max(
                abs(flow*enthalpy_delta),abs(flow*kinetic_delta),abs(flow*potential_delta),abs(total_heat))
            normalized.extend((momentum/momentum_tol,energy/energy_tol))
            rows.append(dict(segment_id=segment.id,momentum_residual_Pa=momentum,total_energy_residual_W=energy,
                             momentum_tolerance_Pa=momentum_tol,total_energy_tolerance_W=energy_tol,
                             darcy_factor=friction,reynolds_number=reynolds,
                             friction_pressure_loss_Pa=friction_loss,gravity_pressure_head_Pa=gravity_head,
                             momentum_acceleration_Pa=momentum_acceleration,
                             inlet_velocity_m_s=v_left,outlet_velocity_m_s=v_right,
                             inlet_elevation_m=elevation,outlet_elevation_m=elevation+segment.end_minus_start_elevation_m,
                             mach=max(v_left/left.speed_of_sound_m_s,v_right/right.speed_of_sound_m_s),
                             relative_density_change=abs(left.density_kg_m3-right.density_kg_m3)/min(left.density_kg_m3,right.density_kg_m3),
                             relative_pressure_change=abs(pressure_drop)/min(left.pressure_pa_abs,right.pressure_pa_abs)))
            rows[-1].update(prescribed_heat_into_fluid_W=segment.heat_into_fluid_W,
                wall_heat_into_fluid_W=wall_heat,total_heat_into_fluid_W=total_heat)
            elevation += segment.end_minus_start_elevation_m
        terminal = None if forward else states[-1].pressure_pa_abs-q.outlet_pressure_Pa_abs
        normalized.append((flow-q.prescribed_mass_flow_kg_s)/q.absolute_mass_flow_tolerance_kg_s
                          if forward else terminal/q.absolute_momentum_tolerance_Pa)
        if not all(math.isfinite(v) for v in normalized):
            raise ValueError("gas potential residual exceeded finite arithmetic")
        return np.asarray(normalized),flow,states,rows,terminal

    def residual(x):
        nonlocal evaluations
        evaluations += 1
        if evaluations > q.maximum_residual_evaluations:
            raise ValueError("gas potential residual-evaluation budget exhausted")
        return inspect_state(x)[0]

    fit = None
    result = None
    failure = None
    try:
        fit = least_squares(residual,x0,bounds=(lower,upper),jac_sparsity=pattern.tocsr(),
                            method="trf",x_scale="jac",ftol=1e-11,xtol=1e-11,gtol=1e-11,
                            max_nfev=q.maximum_solver_evaluations)
        result = inspect_state(fit.x)
        if not fit.success or np.max(np.abs(result[0])) > 1:
            failure = "gas potential coupled solve did not satisfy every physical closure tolerance"
        elif any(row["mach"] > q.maximum_mach or row["relative_density_change"] > q.maximum_segment_density_change
                 or row["relative_pressure_change"] > q.maximum_segment_pressure_change for row in result[3]):
            failure = "gas potential state exceeds the reviewed low-Mach/small-segment-variation domain"
        # Repeat the uncached/reference inlet lookup so an in-process reference
        # mutation cannot be hidden by a cached initial enthalpy.
        if resolve(q.inlet_pressure_Pa_abs,q.inlet_temperature_K).model_dump(mode="json") != inlet.model_dump(mode="json"):
            raise ValueError("gas potential thermodynamic reference changed during evaluation")
    except (ValueError, RuntimeError, ArithmeticError, np.linalg.LinAlgError) as error:
        failure = str(error)
    if gas_potential_path_metadata() != pins or file_digest(Path(provider_file)) != provider_pin or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("gas potential source/provider/request/context changed during evaluation")
    global_energy = None
    if result is not None:
        _,flow,states,rows,_ = result
        global_energy = flow*(states[-1].enthalpy_j_kg-states[0].enthalpy_j_kg
                              +(rows[-1]["outlet_velocity_m_s"]**2-rows[0]["inlet_velocity_m_s"]**2)/2
                              +q.gravity_m_s2*math.fsum(s.end_minus_start_elevation_m for s in q.segments))
        global_energy -= math.fsum(row["total_heat_into_fluid_W"] for row in rows)
    body = dict(schema="pipeline_integrity.gas_potential_path.v1",request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",
                failure=failure,mass_flow_kg_s=None if result is None else result[1],
                states=[] if result is None else [s.model_dump(mode="json") for s in result[2]],
                segment_checks=[] if result is None else result[3],
                outlet_pressure_residual_Pa=None if result is None else result[4],
                boundary_mode="prescribed_mass_flow" if forward else "prescribed_outlet_pressure",
                prescribed_mass_flow_residual_kg_s=(None if not forward or result is None else result[1]-q.prescribed_mass_flow_kg_s),
                global_total_energy_residual_W=global_energy,
                residual_evaluations=evaluations,property_resolutions=resolutions,
                solver_status=None if fit is None else int(fit.status),
                implementation=pins,property_provider_sha256=provider_pin,
                numerical_backend={"numpy":np.__version__,"scipy":scipy.__version__},
                energy_meaning="h_plus_velocity_squared_over_two_plus_gravity_elevation",
                branching_junction_mixing_solved=False,flow_reversal_or_choking_solved=False,
                numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("gas potential receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_gas_potential_path_record(record, *, request=None, context=None, property_provider=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("gas potential receipt changed")
    request_type=GasPotentialForwardRequest if "prescribed_mass_flow_kg_s" in record["request"] else GasPotentialPathRequest
    q = request_type.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("gas potential caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("gas potential caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("withheld gas potential states do not establish a completed replay")
    if evaluate_gas_potential_path(q,c,property_provider=property_provider) != record:
        raise ValueError("gas potential coupled replay differs")
    return True


class GasPotentialPlenumRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: Nonnegative = 0.0
    network: GasNetwork
    expected_owner_pipe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plenum_and_port_loss_evidence_id: Name
    all_nodes_are_zero_velocity_well_mixed_plenums_reviewed: bool = False
    entrance_exit_and_junction_losses_in_supplied_K_reviewed: bool = False
    potential_work_and_common_property_reference_reviewed: bool = False
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=256000000)


class _PotentialWorkPipe:
    def __init__(self,native,elevation_change_m):
        self.native = native
        self.dz = elevation_change_m

    def evaluate(self,left,right):
        result = self.native.evaluate(left,right)
        if result.mass_flow_kg_s == 0:
            return result
        signed_rise = self.dz if result.mass_flow_kg_s > 0 else -self.dz
        # At the reviewed zero-velocity plenum control surfaces h+g*z is
        # transported. Native Pipe still owns the signed hydraulic solution.
        return replace(result,outlet_specific_enthalpy_J_kg=(
            result.outlet_specific_enthalpy_J_kg-9.80665*signed_rise))


class _PotentialPlenumBackend(LH2GasNetworkBackend):
    def _pipe_component(self,pipe_module,properties,specification,pipe_spec,segment_index):
        elevations = {n.id:n.elevation_m for n in specification.boundaries+specification.junctions}
        dz = (elevations[pipe_spec.end_node]-elevations[pipe_spec.start_node])/pipe_spec.segments
        native = pipe_module.Pipe(pipe_spec.length_m/pipe_spec.segments,pipe_spec.diameter_m,
                                  pipe_spec.roughness_m,
                                  local_loss_coefficient=pipe_spec.local_loss_coefficient/pipe_spec.segments,
                                  heat_into_fluid_W=pipe_spec.heat_into_fluid_w/pipe_spec.segments,
                                  elevation_change_m=dz,properties=properties)
        return _PotentialWorkPipe(native,dz)


def evaluate_gas_potential_plenums(request: GasPotentialPlenumRequest,context: RunContext):
    """Reuse native pressure/junction assembly for a reviewed plenum graph.

    Finite port velocity mixing is not represented by this closure. Pipe kinetic
    effects are required to fit the original low-Mach/small-segment domain; the
    declared plenum surfaces have exactly zero kinetic energy flux. A separate
    serial-path API handles nonzero through-pipe endpoint kinetic energy.
    """
    from .gas_network import _qualify
    from .energy_network import EnergyMixingNode,EnergyMixingEdge,EnergyNetworkRequest,evaluate_energy_network
    q = GasPotentialPlenumRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("gas plenum graph asset/clock differs from context")
    if not all((q.all_nodes_are_zero_velocity_well_mixed_plenums_reviewed,
                q.entrance_exit_and_junction_losses_in_supplied_K_reviewed,
                q.potential_work_and_common_property_reference_reviewed)):
        raise ValueError("zero-velocity plenum, supplied losses and common-reference potential-work reviews are required")
    ids = [e.id for e in c.evidence]
    required = {q.plenum_and_port_loss_evidence_id,q.network.parameter_source_id}
    if len(ids) != len(set(ids)) or not required <= set(ids):
        raise ValueError("plenum graph source/review evidence is absent or ambiguous")
    pins,q_pin,c_pin = gas_potential_path_metadata(),digest(q),digest(c)
    if pins["owner_pipe_sha256"] != q.expected_owner_pipe_sha256:
        raise ValueError("plenum graph owner hydraulic source differs from the reviewed pin")
    run = qualification = energy_record = None
    failure = None
    segment_receipts,comparisons = [],[]
    try:
        run = _PotentialPlenumBackend().solve(q.network)
        if run.specification_digest != digest(q.network):
            raise ValueError("plenum graph native backend changed the requested specification")
        elevations = {n.id:n.elevation_m for n in q.network.boundaries+q.network.junctions}
        effective_pipes = []
        edges = []
        net = {name:0.0 for name in run.states}
        for pipe in q.network.pipes:
            names = run.pipe_links[pipe.id]
            rise = elevations[pipe.end_node]-elevations[pipe.start_node]
            rates = []
            for i,name in enumerate(names):
                flow = run.solution["link_flows"][name]
                m = flow["mass_flow_kg_s"]
                rates.append(m)
                elevations[flow["left"]] = elevations[pipe.start_node]+rise*i/pipe.segments
                elevations[flow["right"]] = elevations[pipe.start_node]+rise*(i+1)/pipe.segments
                upstream,downstream = (flow["left"],flow["right"]) if m >= 0 else (flow["right"],flow["left"])
                net[flow["left"]] -= m
                net[flow["right"]] += m
                edges.append(EnergyMixingEdge(id=name,upstream_node_id=upstream,downstream_node_id=downstream,
                                             mass_flow_kg_s=abs(m),heat_into_flow_W=pipe.heat_into_fluid_w/pipe.segments,
                                             evidence_id=q.network.parameter_source_id))
                segment_receipts.append(dict(pipe_id=pipe.id,native_link=name,
                    prescribed_heat_W=pipe.heat_into_fluid_w/pipe.segments,
                    potential_work_W=m*9.80665*rise/pipe.segments,
                    equivalent_static_enthalpy_heat_W=pipe.heat_into_fluid_w/pipe.segments-m*9.80665*rise/pipe.segments))
            # The original qualifier independently checks each segment's mass
            # continuity before this equivalent static-energy audit can pass.
            equivalent_heat = pipe.heat_into_fluid_w-rates[0]*9.80665*rise
            effective_pipes.append(pipe.model_copy(update={"heat_into_fluid_w":equivalent_heat}))
        data = q.network.model_dump(mode="json")
        data["pipes"] = [p.model_dump(mode="json") for p in effective_pipes]
        static_spec = GasNetwork.model_validate_json(json.dumps(data))
        qualification = _qualify(static_spec,c,replace(run,specification_digest=digest(static_spec)))
        if qualification.status != "computed":
            failure = "native gas/plenum hydraulic or equivalent-static-energy qualification: "+qualification.status
        else:
            boundary_ids = {b.id for b in q.network.boundaries}
            withdrawals = {j.id:j.withdrawal_kg_s for j in q.network.junctions}
            nodes = []
            for name,state in run.states.items():
                source = max(-net[name],0.0) if name in boundary_ids else 0.0
                sink = max(net[name],0.0) if name in boundary_ids else withdrawals.get(name,0.0)
                # Reservoir boundaries must have a single flow role. Simultaneous
                # ingress/egress needs an explicit reservoir heat/port model.
                if name in boundary_ids:
                    incoming = any(e.downstream_node_id == name and e.mass_flow_kg_s > q.network.mass_tolerance_kg_s for e in edges)
                    outgoing = any(e.upstream_node_id == name and e.mass_flow_kg_s > q.network.mass_tolerance_kg_s for e in edges)
                    if incoming and outgoing:
                        raise ValueError("a plenum pressure boundary with simultaneous in/out ports requires an explicit reservoir energy model")
                supplied = state["enthalpy_j_kg"]+9.80665*elevations[name] if source > 0 else None
                nodes.append(EnergyMixingNode(id=name,supplied_mass_flow_kg_s=source,
                    supplied_specific_total_energy_J_kg=supplied,withdrawn_mass_flow_kg_s=sink,
                    evidence_id=q.plenum_and_port_loss_evidence_id))
            energy_request = EnergyNetworkRequest(id=q.id+"/total_energy",asset_id=q.asset_id,clock_id=q.clock_id,
                time_s=q.time_s,fluid_composition_id=q.network.fluid,total_energy_reference_id=q.network.property_basis,
                hydraulic_source_digest=digest(run.solution),nodes=tuple(nodes),edges=tuple(edges),
                absolute_mass_balance_tolerance_kg_s=q.network.mass_tolerance_kg_s,
                relative_mass_balance_tolerance=q.network.relative_closure_tolerance,
                absolute_energy_balance_tolerance_W=q.network.static_energy_tolerance_w,
                relative_energy_balance_tolerance=q.network.relative_closure_tolerance,
                fixed_flow_complete_mixing_and_common_energy_basis_reviewed=True,
                evidence_ids=tuple(sorted(required)))
            energy_record = evaluate_energy_network(energy_request,c)
            if energy_record["status"] != "computed":
                failure = "shared plenum total-energy network failed its mass/energy audit"
            predicted = {r["node_id"]:r for r in energy_record["nodes"]}
            reference_node = q.network.boundaries[0].id
            reference_energy = run.states[reference_node]["enthalpy_j_kg"]+9.80665*elevations[reference_node]
            for node in nodes:
                # A receiving pressure reservoir's original temperature is only
                # its reverse-flow supply condition; transmitted sink energy is
                # the incoming stream energy calculated by the shared mixer.
                if node.id in boundary_ids and node.withdrawn_mass_flow_kg_s > 0:
                    continue
                actual = run.states[node.id]["enthalpy_j_kg"]+9.80665*elevations[node.id]
                mixed = predicted[node.id]["specific_total_energy_J_kg"]
                throughflow = math.fsum(e.mass_flow_kg_s for e in edges if e.upstream_node_id == node.id)+node.withdrawn_mass_flow_kg_s
                residual = throughflow*(actual-mixed)
                tolerance = q.network.static_energy_tolerance_w+q.network.relative_closure_tolerance*max(
                    abs(throughflow*(actual-reference_energy)),abs(throughflow*(mixed-reference_energy)))
                comparisons.append(dict(node_id=node.id,total_energy_feedback_residual_W=residual,tolerance_W=tolerance))
                if abs(residual) > tolerance:
                    failure = "plenum native state and shared total-energy mixer do not close"
    except (ValueError,RuntimeError,ArithmeticError) as error:
        failure = type(error).__name__+": "+str(error)
    if gas_potential_path_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("gas plenum source/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.gas_potential_plenums.v1",request=q.model_dump(mode="json"),
                context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",
                failure=failure,implementation=pins,segment_potential_work=segment_receipts,
                static_surrogate_qualification=None if qualification is None else qualification.model_dump(mode="json"),
                total_energy_mixing_record=energy_record,node_energy_feedback_checks=comparisons,
                energy_meaning="zero_velocity_plenum_h_plus_g_z",finite_port_velocity_mixing_solved=False,
                prescribed_pressure_reservoir_sink_temperature_predicted=False,numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("gas plenum receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_gas_potential_plenums_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("gas plenum receipt changed")
    q = GasPotentialPlenumRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("gas plenum caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("gas plenum caller context differs")
    if record.get("status") == "withheld":
        raise ValueError("withheld gas plenum states do not establish a completed replay")
    if evaluate_gas_potential_plenums(q,c) != record:
        raise ValueError("gas plenum replay differs")
    return True
