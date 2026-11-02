"""Event-split discrete vapor cavities on the shared fractional liquid MOC.

Pure vapor pockets hold the supplied saturation pressure while their volume
changes with the two liquid-column flows. A proven zero-volume event permits
rejoining and its acoustic pressure rise. Event roots belong to the declared
piecewise characteristic-foot interpolation, not an exact continuum solution.
Thermal/latent energy and vapor mass are not solved by this acoustic model.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest
from .datasets import file_digest
from .moc_characteristics import characteristic_foot_polynomials,interpolated_characteristic_values
from .quadrature import integrate_linear_interval
from .transient import LiquidTransient


class VaporCavityMOCRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    liquid: LiquidTransient
    initial_cavity_volume_m3: tuple[Nonnegative,...] = Field(default=(),max_length=10000)
    maximum_local_void_fraction: Positive = Field(default=0.05,le=0.1)
    maximum_absolute_pressure_Pa: Positive
    event_time_tolerance_s: Positive
    event_pressure_tolerance_Pa: Positive
    event_volume_tolerance_m3: Positive
    global_liquid_volume_tolerance_m3: Positive
    relative_global_volume_tolerance: Positive = Field(default=1e-7,le=1e-3)
    polynomial_real_root_imaginary_tolerance: Positive = Field(default=1e-9,le=1e-3)
    maximum_events: int = Field(default=10000,ge=1,le=100000)
    maximum_substeps: int = Field(default=50000,ge=1,le=1000000)
    maximum_polynomial_root_calls: int = Field(default=1000000,ge=1,le=100000000)
    maximum_event_residual_evaluations: int = Field(default=1000000,ge=1,le=100000000)
    maximum_root_iterations: int = Field(default=100,ge=1,le=1000)
    maximum_retained_values: int = Field(default=4000000,ge=100,le=20000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=256000000)
    parameter_evidence_id: Name
    saturation_and_initial_void_evidence_id: Name
    discretization_review_evidence_id: Name
    pure_vapor_negligible_vapor_mass_and_constant_saturation_pressure_reviewed: bool = False
    characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed: bool = False
    small_void_fraction_acoustic_liquid_and_omitted_thermal_energy_reviewed: bool = False

    @model_validator(mode="after")
    def scope(self):
        s = self.liquid
        if len(s.pipes) != 1 or len(s.nodes) != 2 or s.valves or s.pumps:
            raise ValueError("event-split vapor cavities require one liquid pipe and no separate pump/valve model")
        p = s.pipes[0]
        nodes = {n.id:n for n in s.nodes}
        if nodes[p.start_node].boundary != "head" or nodes[p.end_node].boundary != "withdrawal":
            raise ValueError("vapor-cavity path requires upstream reservoir head and downstream actual liquid withdrawal")
        if any(any(v != 0 for v in node.atmospheric_emitter.values) for node in s.nodes):
            raise ValueError("pure-vapor cavity scope excludes air ingress through atmospheric emitters")
        ratio = s.duration_s/s.requested_timestep_s
        if not math.isfinite(ratio) or ratio > s.maximum_steps:
            raise ValueError("vapor-cavity nominal time grid exceeds finite step support")
        steps = math.ceil(ratio)
        dt = s.duration_s/steps
        travel = p.wave_speed_m_s*dt
        if not math.isfinite(travel) or travel <= 0:
            raise ValueError("vapor-cavity nominal characteristic travel is not representable")
        mesh_ratio = p.length_m/travel
        if not math.isfinite(mesh_ratio):
            raise ValueError("vapor-cavity nominal mesh is not representable")
        sections = round(mesh_ratio)
        if sections < 2 or sections > min(10000,s.maximum_total_segments) or steps > s.maximum_steps:
            raise ValueError("vapor-cavity characteristic mesh/clock exceeds budget")
        if self.initial_cavity_volume_m3 and len(self.initial_cavity_volume_m3) != sections:
            raise ValueError("initial vapor volumes must follow each nonreservoir grid section")
        if (steps+1)*(8*sections+20) > self.maximum_retained_values:
            raise ValueError("nominal vapor-cavity history exceeds its value budget")
        if self.maximum_absolute_pressure_Pa <= s.vapour_pressure_pa_abs:
            raise ValueError("maximum vapor-cavity pressure must exceed saturation pressure")
        end = self.start_time_s+s.duration_s
        if not math.isfinite(end) or end <= self.start_time_s:
            raise ValueError("vapor-cavity physical horizon is not representable")
        return self


def vapor_cavity_metadata():
    return {name:file_digest(Path(__file__).with_name(name+".py"))
            for name in ("vapor_cavity_moc","moc_characteristics","transient","quadrature","contracts","datasets")}


def evaluate_vapor_cavity_moc(request: VaporCavityMOCRequest,context: RunContext):
    import numpy as np
    import scipy
    from numpy.polynomial import Polynomial
    from scipy.optimize import brentq
    q = VaporCavityMOCRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id) != (c.asset_id,c.clock_id):
        raise ValueError("vapor-cavity asset/clock differs from context")
    if not all((q.pure_vapor_negligible_vapor_mass_and_constant_saturation_pressure_reviewed,
                q.characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed,
                q.small_void_fraction_acoustic_liquid_and_omitted_thermal_energy_reviewed)):
        raise ValueError("pure-vapor, event interpolation/grid and acoustic/thermal-domain reviews are required")
    evidence = {e.id:e for e in c.evidence}
    required = {q.parameter_evidence_id,q.saturation_and_initial_void_evidence_id,q.discretization_review_evidence_id}
    if len(evidence) != len(c.evidence) or not required <= set(evidence) or any(evidence[key].sha256 is None for key in required):
        raise ValueError("vapor-cavity geometry/saturation/discretization evidence requires source SHA-256 pins")
    pins,q_pin,c_pin = vapor_cavity_metadata(),digest(q),digest(c)
    fixed_result_bytes = len(json.dumps(dict(request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),implementation=pins),allow_nan=False).encode("utf-8"))+8192
    if fixed_result_bytes >= q.maximum_result_bytes:
        raise ValueError("vapor-cavity source request/context exceeds its retained-result budget")
    s = q.liquid
    pipe = s.pipes[0]
    nodes = {v.id:v for v in s.nodes}
    reservoir,outlet = nodes[pipe.start_node],nodes[pipe.end_node]
    nominal_steps = math.ceil(s.duration_s/s.requested_timestep_s)
    grid_dt = s.duration_s/nominal_steps
    n = round(pipe.length_m/(pipe.wave_speed_m_s*grid_dt))
    dx = pipe.length_m/n
    wave = dx/grid_dt
    wave_adjustment = abs(wave/pipe.wave_speed_m_s-1)
    if wave_adjustment > s.maximum_wave_speed_adjustment+1e-14:
        raise ValueError("vapor-cavity wave-speed adjustment exceeds the declared bound")
    area = math.pi*pipe.diameter_m**2/4
    b = wave/(s.gravity_m_s2*area)
    r = pipe.darcy_friction_factor*dx/(2*s.gravity_m_s2*pipe.diameter_m*area**2)
    density_gravity = s.density_kg_m3*s.gravity_m_s2
    if not all(math.isfinite(v) and v > 0 for v in (grid_dt,dx,wave,area,b,density_gravity)) or not math.isfinite(r) or r < 0:
        raise ValueError("vapor-cavity geometric/characteristic scales exceed finite arithmetic")
    head = initial_head = np.linspace(reservoir.initial_head_m,outlet.initial_head_m,n+1)
    q0 = pipe.initial_volume_flow_m3_s
    if abs(q0)/(area*wave) > s.maximum_acoustic_mach:
        raise ValueError("initial vapor-cavity flow exceeds the reviewed acoustic Mach domain")
    if abs(head[0]-head[-1]-r*n*q0*abs(q0)) > s.initial_head_tolerance_m or abs(outlet.history.at(0)-q0) > s.initial_flow_tolerance_m3_s:
        raise ValueError("initial vapor-cavity liquid head/flow is not in equilibrium")
    chainage = np.linspace(0,pipe.length_m,n+1)
    elevation = (np.interp(chainage,[p.chainage_m for p in pipe.elevation_profile],[p.elevation_m for p in pipe.elevation_profile])
                 if pipe.elevation_profile else np.linspace(reservoir.elevation_m,outlet.elevation_m,n+1))
    vapor_head = elevation+(s.vapour_pressure_pa_abs-s.ambient_pressure_pa_abs)/density_gravity
    volume0 = np.asarray(q.initial_cavity_volume_m3 if q.initial_cavity_volume_m3 else (0.0,)*n,dtype=float)
    volume = volume0.copy()
    active = volume > 0
    if np.any(head[1:] < vapor_head[1:]-q.event_pressure_tolerance_Pa/density_gravity):
        raise ValueError("initial inactive liquid is below saturation pressure")
    if np.any(active & (np.abs(head[1:]-vapor_head[1:])*density_gravity > q.event_pressure_tolerance_Pa)):
        raise ValueError("initial positive vapor cavities must have the declared saturation pressure")
    initial_pressure = s.ambient_pressure_pa_abs+density_gravity*(head-elevation)
    if not np.all(np.isfinite(initial_pressure)) or np.any(initial_pressure > q.maximum_absolute_pressure_Pa):
        raise ValueError("initial vapor-cavity pressure exceeds finite maximum-pressure support")
    for value in reservoir.history.values:
        pressure = s.ambient_pressure_pa_abs+density_gravity*(value-elevation[0])
        if not s.vapour_pressure_pa_abs < pressure <= q.maximum_absolute_pressure_Pa:
            raise ValueError("upstream reservoir cavitation/overpressure lies outside this boundary model")
    dual_volume = np.full(n,area*dx)
    dual_volume[-1] *= 0.5
    max_volume = q.maximum_local_void_fraction*dual_volume
    if np.any(volume > max_volume):
        raise ValueError("initial vapor cavity exceeds the reviewed reach-volume ratio")
    left = np.full(n+1,q0)
    right = left.copy()
    growth = np.zeros(n)
    condensation = np.zeros(n)
    projection = np.zeros(n)
    external_in = external_out = net_exchange = 0.0
    time = 0.0
    states,steps,events = [],[],[]
    root_calls = residual_calls = 0
    retained_bytes = fixed_result_bytes
    retained_values = 0
    max_mach = max_strain = 0.0
    maximum_onset_pressure_roundoff_bound_Pa = 0.0
    failure = None
    knots = sorted(set((0.0,s.duration_s,*reservoir.history.time_s,*outlet.history.time_s)))

    def record_state():
        nonlocal max_mach,max_strain
        pressure = s.ambient_pressure_pa_abs+density_gravity*(head-elevation)
        storage = s.gravity_m_s2*area/wave**2*float(np.trapezoid(head-initial_head,dx=dx))
        void_change = float(np.sum(volume-volume0))
        max_mach = max(max_mach,float(max(np.max(np.abs(left)),np.max(np.abs(right)))/(area*wave)))
        max_strain = max(max_strain,float(s.gravity_m_s2*np.max(np.abs(head-initial_head))/wave**2))
        return dict(time_s=q.start_time_s+time,head_m=head.tolist(),absolute_pressure_Pa=pressure.tolist(),
            flow_from_left_m3_s=left.tolist(),flow_to_right_m3_s=right.tolist(),
            cavity_volume_m3=volume.tolist(),active_vapor_cavity=active.tolist(),
            local_void_fraction=(volume/dual_volume).tolist(),linearized_liquid_pressure_storage_m3=storage,
            total_cavity_volume_change_m3=void_change,net_external_liquid_inflow_m3=net_exchange,
            liquid_volume_residual_m3=storage-void_change-net_exchange)

    states.append(record_state())
    retained_bytes += len(json.dumps(states[-1],allow_nan=False).encode("utf-8"))
    retained_values += 8*n+20
    if retained_bytes > q.maximum_result_bytes or retained_values > q.maximum_retained_values:
        raise ValueError("initial vapor-cavity trajectory exceeds its retained-result budget")

    def critical_points(poly,a,z):
        nonlocal root_calls
        root_calls += 1
        if root_calls > q.maximum_polynomial_root_calls:
            raise ValueError("vapor-cavity polynomial root-work budget exhausted")
        points = [a,z]
        coefficients = np.asarray(poly.coef,dtype=float)
        if not np.all(np.isfinite(coefficients)):
            raise ValueError("vapor-cavity event polynomial exceeds finite arithmetic")
        for root in poly.deriv().roots():
            if abs(root.imag) <= q.polynomial_real_root_imaginary_tolerance*max(1.0,abs(root.real)) and a < root.real < z:
                points.append(float(root.real))
        return sorted(set(points))

    def first_downcrossing(poly,a,z,tolerance,*,onset_roundoff_head_m=0.0):
        nonlocal residual_calls
        points = critical_points(poly,a,z)
        for u,v in zip(points,points[1:]):
            fu,fv = float(poly(u)),float(poly(v))
            if not math.isfinite(fu) or not math.isfinite(fv):
                raise ValueError("vapor-cavity event residual is nonfinite")
            if onset_roundoff_head_m:
                # Each interval is monotone (its derivative roots split it).
                # A saturation plateau must not nucleate from cancellation
                # below the arithmetic error of its characteristic head terms.
                if min(fu,fv) >= -onset_roundoff_head_m:
                    continue
                if abs(fu) <= onset_roundoff_head_m and fv < -onset_roundoff_head_m:
                    return dict(alpha=u,initial_bracket=(u,u),residual=fu,
                                onset_root_within_characteristic_roundoff=True,
                                onset_pressure_roundoff_bound_Pa=onset_roundoff_head_m*density_gravity)
            # A small *positive* pocket still needs a positive-time root. The
            # residual tolerance must not erase inventory at the current clock.
            if u == 0 and fu == 0:
                tendency = next((coefficient for coefficient in poly.coef[1:] if coefficient != 0),0.0)
                if tendency < 0:
                    return dict(alpha=0.0,initial_bracket=(0.0,0.0),residual=fu)
            if fu > 0 and fv < 0:
                def value(alpha):
                    nonlocal residual_calls
                    residual_calls += 1
                    if residual_calls > q.maximum_event_residual_evaluations:
                        raise ValueError("vapor-cavity event-residual evaluation budget exhausted")
                    return float(poly(alpha))
                root = brentq(value,u,v,xtol=min(q.event_time_tolerance_s/grid_dt,(v-u)/4),
                              rtol=4*np.finfo(float).eps,maxiter=q.maximum_root_iterations)
                return dict(alpha=float(root),initial_bracket=(u,v),residual=float(poly(root)))
            if fu == 0 and fv < 0:
                return dict(alpha=u,initial_bracket=(u,u),residual=0.0)
            if fv == 0 and float(poly.deriv()(v)) < 0:
                return dict(alpha=v,initial_bracket=(v,v),residual=0.0)
            if fu < -tolerance:
                raise ValueError("vapor-cavity event search begins beyond an unresolved crossing")
        return None

    try:
        for substep in range(q.maximum_substeps):
            if time >= s.duration_s:
                break
            remaining = min(grid_dt,s.duration_s-time)
            future_knots = [k for k in knots if k > time]
            if future_knots:
                remaining = min(remaining,future_knots[0]-time)
            alpha_max = remaining/grid_dt
            if not 0 < alpha_max <= 1 or q.start_time_s+time+remaining <= q.start_time_s+time:
                raise ValueError("vapor-cavity remaining time is not representable")
            if r*max(float(np.max(np.abs(left))),float(np.max(np.abs(right))))/b >= 0.5:
                raise ValueError("vapor-cavity friction source is too stiff for the characteristic step")
            all_pieces = characteristic_foot_polynomials(head,right,left,b,r)
            largest_flow = max(float(np.max(np.abs(left))),float(np.max(np.abs(right))))
            characteristic_operation_scale = (float(np.max(np.abs(head)))
                + b*largest_flow + r*largest_flow**2)
            outlet_q0 = outlet.history.at(time)
            outlet_q1 = outlet.history.at(time+remaining)
            tail_flow = Polynomial((outlet_q0,(outlet_q1-outlet_q0)/alpha_max))
            old_jump = right[1:]-left[1:]
            alpha_poly = Polynomial((0,1))
            volume_pieces = []
            pending = []
            for j,pieces in enumerate(all_pieces):
                local = []
                for a,z,cp,cm in pieces:
                    if a >= alpha_max:
                        break
                    z = min(z,alpha_max)
                    free_head = (cp+cm)/2 if cm is not None else cp-b*tail_flow
                    jump = (2*vapor_head[j+1]-cp-cm)/b if cm is not None else tail_flow-(cp-vapor_head[j+1])/b
                    volume_poly = Polynomial((volume[j],))+0.5*grid_dt*alpha_poly*(old_jump[j]+jump)
                    local.append((a,z,volume_poly))
                    polynomial = volume_poly if active[j] else free_head-vapor_head[j+1]
                    tolerance = q.event_volume_tolerance_m3 if active[j] else q.event_pressure_tolerance_Pa/density_gravity
                    onset_roundoff = 0.0
                    if not active[j]:
                        head_scale = max(1.0,abs(vapor_head[j+1]),characteristic_operation_scale,
                            float(np.sum(np.abs(cp.coef))),
                            float(np.sum(np.abs(cm.coef))) if cm is not None else
                            b*float(np.sum(np.abs(tail_flow.coef))))
                        # Linear-foot polynomials and the cubic Darcy source
                        # use fewer than 64 rounded operations per coefficient
                        # and evaluation. Their absolute coefficient sums bound
                        # magnitudes over 0<=alpha<=1; 64*eps/(1-64*eps) is the
                        # forward-error allowance, not a fitted pressure margin.
                        roundoff_factor = 64*np.finfo(float).eps/(1-64*np.finfo(float).eps)
                        onset_roundoff = roundoff_factor*head_scale
                        pressure_roundoff = onset_roundoff*density_gravity
                        maximum_onset_pressure_roundoff_bound_Pa = max(
                            maximum_onset_pressure_roundoff_bound_Pa,pressure_roundoff)
                        if pressure_roundoff > q.event_pressure_tolerance_Pa:
                            raise ValueError("onset pressure tolerance is below characteristic arithmetic resolution")
                    event = first_downcrossing(polynomial,a,z,tolerance,onset_roundoff_head_m=onset_roundoff)
                    if event is not None:
                        pending.append(dict(section_index=j+1,kind="collapse" if active[j] else "onset",**event))
                        break
                volume_pieces.append(local)
            earliest = min((e["alpha"] for e in pending),default=alpha_max)
            fraction = min(alpha_max,earliest)
            elapsed = grid_dt*fraction
            event_group = [e for e in pending if abs(e["alpha"]-fraction)*grid_dt <= q.event_time_tolerance_s]
            plus,minus = interpolated_characteristic_values(head,right,left,b,r,fraction)
            new_head = head.copy()
            new_left,new_right = left.copy(),right.copy()
            new_volume,new_active = volume.copy(),active.copy()
            new_growth,new_condensation,new_projection = growth.copy(),condensation.copy(),projection.copy()
            new_head[0] = reservoir.history.at(time+elapsed)
            new_left[0] = new_right[0] = (new_head[0]-minus[0])/b
            qtail = outlet.history.at(time+elapsed)
            event_rows = []
            for j in range(n):
                cp = float(plus[j])
                cm = float(minus[j+1]) if j < n-1 else None
                free_h = (cp+cm)/2 if cm is not None else cp-b*qtail
                if active[j]:
                    h = vapor_head[j+1]
                    qi = (cp-h)/b
                    qo = (h-cm)/b if cm is not None else qtail
                    new_volume[j] = volume[j]+0.5*elapsed*(old_jump[j]+qo-qi)
                    # Recover every turning point of the declared within-step
                    # volume trajectory before splitting positive/negative work.
                    for a,z,poly in volume_pieces[j]:
                        if a >= fraction:
                            break
                        points = critical_points(poly,a,min(z,fraction))
                        for u,v in zip(points,points[1:]):
                            change = float(poly(v)-poly(u))
                            new_growth[j] += max(change,0.0)
                            new_condensation[j] += max(-change,0.0)
                else:
                    h = free_h
                    qi = (cp-cm)/(2*b) if cm is not None else qtail
                    qo = qi
                    new_volume[j] = 0.0
                new_head[j+1],new_left[j+1],new_right[j+1] = h,qi,qo
                event = next((e for e in event_group if e["section_index"] == j+1),None)
                if event is not None:
                    if event["kind"] == "onset":
                        residual = (free_h-vapor_head[j+1])*density_gravity
                        if abs(residual) > q.event_pressure_tolerance_Pa:
                            raise ValueError("onset root does not reproduce the saturation crossing from the same accepted start")
                        new_active[j] = True
                        new_head[j+1] = vapor_head[j+1]
                        new_left[j+1] = (cp-vapor_head[j+1])/b
                        new_right[j+1] = (vapor_head[j+1]-cm)/b if cm is not None else qtail
                        event_rows.append(dict(**event,event_time_s=q.start_time_s+time+elapsed,
                            pressure_crossing_residual_Pa=residual,cavity_volume_m3=0.0))
                    else:
                        if abs(new_volume[j]) > q.event_volume_tolerance_m3 or qo-qi > s.initial_flow_tolerance_m3_s:
                            raise ValueError("collapse root does not reproduce zero volume with liquid-column approach")
                        if free_h < vapor_head[j+1]-q.event_pressure_tolerance_Pa/density_gravity:
                            raise ValueError("post-collapse characteristic state would immediately require unresolved recavitation")
                        projected = -float(new_volume[j])
                        new_projection[j] += projected
                        new_volume[j] = 0.0
                        new_active[j] = False
                        new_head[j+1] = free_h
                        new_left[j+1] = new_right[j+1] = (cp-cm)/(2*b) if cm is not None else qtail
                        event_rows.append(dict(**event,event_time_s=q.start_time_s+time+elapsed,
                            proven_volume_residual_m3=-projected,event_roundoff_volume_projection_m3=projected,
                            pre_rejoining_pressure_Pa=s.vapour_pressure_pa_abs,
                            post_rejoining_pressure_Pa=s.ambient_pressure_pa_abs+density_gravity*(free_h-elevation[j+1]),
                            upstream_flow_before_m3_s=qi,downstream_flow_before_m3_s=qo,
                            rejoined_flow_m3_s=float(new_left[j+1])))
            if len(events)+len(event_rows) > q.maximum_events:
                raise ValueError("vapor-cavity onset/collapse event budget exhausted")
            if elapsed == 0 and not event_rows:
                raise ValueError("vapor-cavity event loop cannot advance its clock or phase state")
            if np.any(new_volume < 0) or np.any(new_volume > max_volume):
                raise ValueError("vapor-cavity step leaves nonnegative volume/reach-volume support; no clipping applied")
            pressure = s.ambient_pressure_pa_abs+density_gravity*(new_head-elevation)
            if not np.all(np.isfinite(pressure)) or np.any(pressure < s.vapour_pressure_pa_abs-q.event_pressure_tolerance_Pa) or np.any(pressure > q.maximum_absolute_pressure_Pa):
                raise ValueError("vapor-cavity pressure leaves the reviewed saturation/maximum-pressure domain")
            added_in = added_out = 0.0
            if elapsed > 0:
                for first,last in ((right[0],new_right[0]),(-right[-1],-new_right[-1])):
                    incoming,outgoing = integrate_linear_interval(float(first),float(last),elapsed)
                    added_in += incoming
                    added_out += outgoing
            next_net = net_exchange+added_in-added_out
            storage = s.gravity_m_s2*area/wave**2*float(np.trapezoid(new_head-initial_head,dx=dx))
            void_change = float(np.sum(new_volume-volume0))
            balance = storage-void_change-next_net
            budget = q.global_liquid_volume_tolerance_m3+q.relative_global_volume_tolerance*max(abs(storage),abs(void_change),abs(next_net))
            if not math.isfinite(balance) or abs(balance) > budget:
                raise ValueError("vapor-cavity liquid storage/void/external-flow budget failed, including any rejoining pressure-storage quadrature jump")
            cavity_ledger = new_volume-volume0-new_growth+new_condensation-new_projection
            if np.any(np.abs(cavity_ledger) > q.event_volume_tolerance_m3):
                raise ValueError("gross vapor-volume growth/condensation/event-roundoff ledger does not close")
            mach = float(max(np.max(np.abs(new_left)),np.max(np.abs(new_right)))/(area*wave))
            strain = float(s.gravity_m_s2*np.max(np.abs(new_head-initial_head))/wave**2)
            if mach > s.maximum_acoustic_mach or strain > s.maximum_linearized_storage_strain:
                raise ValueError("vapor-cavity rejoining step exceeds the acoustic Mach/storage-strain domain")
            values = 8*n+20+len(event_rows)*20
            estimate = 3000+500*n+len(json.dumps(event_rows,allow_nan=False).encode("utf-8"))
            if retained_values+values > q.maximum_retained_values or retained_bytes+estimate > q.maximum_result_bytes:
                raise ValueError("vapor-cavity event trajectory exceeds retained values/bytes budget")
            previous_time = time
            if elapsed > 0 and q.start_time_s+time+elapsed <= q.start_time_s+time:
                raise ValueError("vapor-cavity physical event clock cannot resolve its positive elapsed time")
            time += elapsed
            if elapsed > 0 and time <= previous_time:
                raise ValueError("vapor-cavity event elapsed time is not representable")
            head,left,right,volume,active = new_head,new_left,new_right,new_volume,new_active
            growth,condensation,projection = new_growth,new_condensation,new_projection
            external_in += added_in
            external_out += added_out
            net_exchange = next_net
            events.extend(event_rows)
            steps.append(dict(start_time_s=q.start_time_s+previous_time,finish_time_s=q.start_time_s+time,
                cfl_fraction=fraction,external_liquid_inflow_m3=added_in,external_liquid_outflow_m3=added_out,
                liquid_volume_residual_m3=balance,onset_or_collapse_sections=[e["section_index"] for e in event_rows]))
            states.append(record_state())
            retained_values += values
            retained_bytes += estimate
        else:
            raise ValueError("vapor-cavity event/substep budget exhausted before the full horizon")
    except (ValueError,RuntimeError,ArithmeticError,np.linalg.LinAlgError) as error:
        failure = type(error).__name__+": "+str(error)
    if vapor_cavity_metadata() != pins or digest(q) != q_pin or digest(c) != c_pin:
        raise ValueError("vapor-cavity source/request/context changed during evaluation")
    body = dict(schema="pipeline_integrity.vapor_cavity_moc.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="withheld" if failure else "computed_diagnostic",failure=failure,
        implementation=pins,states=states,steps=steps,events=events,
        grid=dict(chainage_m=chainage.tolist(),elevation_m=elevation.tolist(),segments=n,nominal_characteristic_step_s=grid_dt,
                  adjusted_wave_speed_m_s=wave,relative_wave_speed_adjustment=wave_adjustment),
        gross_cavity_growth_m3=growth.tolist(),gross_cavity_condensation_m3=condensation.tolist(),
        event_roundoff_volume_projection_m3=projection.tolist(),gross_external_liquid_inflow_m3=external_in,
        gross_external_liquid_outflow_m3=external_out,final_liquid_volume_residual_m3=states[-1]["liquid_volume_residual_m3"],
        maximum_acoustic_mach=max_mach,maximum_linearized_storage_strain=max_strain,
        polynomial_root_calls=root_calls,event_residual_evaluations=residual_calls,
        event_definition="roots_of_declared_fractional_characteristic_discrete_model",
        onset_pressure_roundoff_policy="negative_excursion_must_exceed_characteristic_forward_error_bound",
        maximum_onset_pressure_roundoff_bound_Pa=maximum_onset_pressure_roundoff_bound_Pa,
        positive_cavity_inventory_roundoff_threshold_applied=False,
        reported_event_brackets_are_initial_monotone_search_intervals=True,
        grid_independent_pressure_peak_claim=False,gas_seed_or_dissolution_inferred=False,
        vapor_mass_latent_energy_or_two_fluid_slip_solved=False,source_of_pressure_peak="rejoining_characteristic_state_after_proven_zero_volume",
        theoretical_source="https://doi.org/10.1016/j.jfluidstructs.2005.08.008",
        scipy_version=scipy.__version__,numpy_version=np.__version__,numerical_validation_completed=False)
    if len(json.dumps(body).encode("utf-8")) > q.maximum_result_bytes:
        raise ValueError("complete vapor-cavity receipt exceeds result-byte budget")
    body["record_digest"] = digest(body)
    return body


def verify_vapor_cavity_moc_record(record, *, request=None,context=None):
    if digest({k:v for k,v in record.items() if k != "record_digest"}) != record.get("record_digest"):
        raise ValueError("vapor-cavity receipt changed")
    q = VaporCavityMOCRequest.model_validate_json(json.dumps(record["request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json") != q.model_dump(mode="json"):
        raise ValueError("vapor-cavity caller request differs")
    if context is not None and context.model_dump(mode="json") != c.model_dump(mode="json"):
        raise ValueError("vapor-cavity caller context differs")
    if record.get("status") != "computed_diagnostic":
        raise ValueError("withheld vapor-cavity trajectories do not establish a completed replay")
    if evaluate_vapor_cavity_moc(q,c) != record:
        raise ValueError("vapor-cavity event/split-flow replay differs")
    return True


def vapor_cavity_moc_signal_frames(record, *, section_index: int, stream_id: str):
    """Publish post-event states with every same-time source row in the lineage.

An onset or rejoining can change a section's discrete state at the current
clock. The calculation retains both sides; this observation stream selects the
last side at that clock, without pretending that it is an interpolated sample.
    """
    verify_vapor_cavity_moc_record(record)
    q = VaporCavityMOCRequest.model_validate_json(json.dumps(record["request"]))
    n = record["grid"]["segments"]
    if type(section_index) is not int or not 1 <= section_index <= n:
        raise ValueError("vapor-cavity signal section must identify a nonreservoir grid node")
    groups = []
    for index,row in enumerate(record["states"]):
        if groups:
            previous_time = record["states"][groups[-1][-1]]["time_s"]
            if row["time_s"] < previous_time:
                raise ValueError("vapor-cavity source clock is reordered")
            if row["time_s"] == previous_time:
                groups[-1].append(index)
                continue
        groups.append([index])
    units = {"pressure_pa_abs":"Pa","head_m":"m","flow_from_left_m3_s":"m^3/s",
             "flow_to_right_m3_s":"m^3/s","cavity_volume_m3":"m^3",
             "local_void_fraction":"1","active_vapor_cavity":"1"}
    frames = []
    for group in groups:
        row = record["states"][group[-1]]
        j = section_index-1
        values = dict(pressure_pa_abs=row["absolute_pressure_Pa"][section_index],
                      head_m=row["head_m"][section_index],
                      flow_from_left_m3_s=row["flow_from_left_m3_s"][section_index],
                      flow_to_right_m3_s=row["flow_to_right_m3_s"][section_index],
                      cavity_volume_m3=row["cavity_volume_m3"][j],
                      local_void_fraction=row["local_void_fraction"][j],
                      active_vapor_cavity=1.0 if row["active_vapor_cavity"][j] else 0.0)
        frames.append(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
            values=values,units=units,valid=dict.fromkeys(values,True),
            origin="synthetic" if record["context"]["origin"]=="synthetic" else "simulation",
            operating_mode="active_vapor_pocket" if row["active_vapor_cavity"][j] else "continuous_or_rejoined_liquid"))
    receipt = dict(schema="pipeline_integrity.vapor_cavity_frames.v1",
                   source_record_digest=record["record_digest"],section_index=section_index,
                   stream_id=stream_id,clock_id=q.clock_id,asset_id=q.asset_id,
                   source_row_groups=groups,raw_source_row_count=len(record["states"]),
                   published_frame_count=len(frames),frames_digest=digest(frames),
                   grouped_source_rows_digest=digest([[record["states"][i] for i in group] for group in groups]),
                   simultaneous_event_observation_side="last_post_event_state_at_each_source_clock",
                   source_rows_interpolated=False,source_pressure_reference="absolute",
                   conserved_volume_and_event_sides_retained_in_original_source=True)
    receipt["record_digest"] = digest(receipt)
    return tuple(frames),receipt
