"""Prescribed-gradient piezometric routing with an equilibrium pressure floor.

Stations are horizontal survey run. The minimal backward head envelope keeps
the stated downstream pressure and identifies candidate gravity sections.
It is the negligible-velocity-head routing approximation, not a conservative
moving-interface solution or a prediction of gas volume or transition loss.
"""
from __future__ import annotations

import math

import numpy as np
from pydantic import Field,model_validator

from .contracts import Calculation,Contract,Name,Nonnegative,Positive
from .linear_running_envelope import piecewise_linear_running_max
from .steady_circular_depth import SteadyCircularFlow,CircularDepthRootRequest,solve_circular_normal_depth


class PartialNormalDepthCheck(Contract):
    inner_diameter_m: Positive
    volume_flow_m3_s: Positive
    chezy_coefficient_m_half_per_s: Positive
    depth_bracket_m: tuple[Positive,Positive]
    maximum_velocity_head_change_m: Nonnegative | None = None

    @model_validator(mode="after")
    def support(self):
        lo,hi=self.depth_bracket_m
        if not 0<lo<hi<self.inner_diameter_m:
            raise ValueError("partial-flow depth bracket must lie strictly inside the diameter")
        return self


class SaturationHeadRouteRequest(Contract):
    asset_id: Name
    fluid_state_basis_id: Name
    horizontal_stations_m: tuple[Nonnegative,...] = Field(min_length=2,max_length=10000)
    elevations_m: tuple[float,...] = Field(min_length=2,max_length=10000)
    full_flow_friction_gradients_m_m: tuple[Nonnegative,...] = Field(min_length=1,max_length=9999)
    density_kg_m3: Positive
    gravity_m_s2: Positive = 9.80665
    saturation_pressure_Pa_abs: Positive
    downstream_pressure_Pa_abs: Positive
    query_stations_m: tuple[Nonnegative,...] = Field(default=(),max_length=10000)
    partial_normal_depth_check: PartialNormalDepthCheck | None = None
    head_equation_tolerance_m: Positive = 1e-9
    absolute_pressure_tolerance_Pa: Positive = 1e-5
    prescribed_gradients_constant_density_equilibrium_floor_and_small_velocity_head_reviewed: bool = False

    @model_validator(mode="after")
    def survey(self):
        x=self.horizontal_stations_m
        if x[0]!=0 or any(b<=a for a,b in zip(x,x[1:])):
            raise ValueError("horizontal survey stations must start at zero and strictly increase")
        if len(self.elevations_m)!=len(x) or len(self.full_flow_friction_gradients_m_m)!=len(x)-1:
            raise ValueError("survey needs one elevation per station and one supplied gradient per interval")
        if self.downstream_pressure_Pa_abs<self.saturation_pressure_Pa_abs:
            raise ValueError("downstream pressure lies below the declared equilibrium pressure floor")
        if any(v>x[-1] for v in self.query_stations_m) or any(b<=a for a,b in zip(self.query_stations_m,self.query_stations_m[1:])):
            raise ValueError("query stations must strictly increase within the survey")
        return self


def evaluate_saturation_head_route(request:SaturationHeadRouteRequest)->Calculation:
    q=SaturationHeadRouteRequest.model_validate(request.model_dump(mode="python"))
    if not q.prescribed_gradients_constant_density_equilibrium_floor_and_small_velocity_head_reviewed:
        raise ValueError("prescribed friction, equilibrium floor and negligible velocity-head-change assumptions require review")
    x=q.horizontal_stations_m
    z=q.elevations_m
    gradients=q.full_flow_friction_gradients_m_m
    datum=z[-1]
    z_relative=[v-datum for v in z]
    accumulated=[0.0]
    try:
        for i in range(len(x)-1):
            accumulated.append(math.fsum((accumulated[-1],gradients[i]*(x[i+1]-x[i]))))
        floor_head=q.saturation_pressure_Pa_abs/q.density_kg_m3/q.gravity_m_s2
        outlet_head=q.downstream_pressure_Pa_abs/q.density_kg_m3/q.gravity_m_s2
        obstacle=[z_relative[i]+floor_head+accumulated[i] for i in range(len(x))]
        ending=outlet_head+accumulated[-1]
        reflected=piecewise_linear_running_max(tuple(x[-1]-v for v in x[::-1]),obstacle[::-1],ending)
        if reflected["initial_raise"]!=0:
            raise ValueError("supplied downstream boundary cannot be retained at the declared pressure floor")
        nodes_relative=[v-accumulated[i] for i,v in enumerate(reflected["maximum_values"][::-1])]
        pieces=[]
        for raw in reflected["pieces"]:
            i=len(x)-2-raw["source_interval"]
            left,right=x[-1]-raw["end"],x[-1]-raw["start"]
            first=raw["level_end"]-(accumulated[i]+gradients[i]*(left-x[i]))
            last=raw["level_start"]-(accumulated[i]+gradients[i]*(right-x[i]))
            slope=(z_relative[i+1]-z_relative[i])/(x[i+1]-x[i])
            loss=first-last
            baseline=gradients[i]*(right-left)
            extra=loss-baseline
            if extra<-q.head_equation_tolerance_m:
                raise ValueError("head-floor route violates its nonnegative added-loss condition")
            piece=dict(start_station_m=left,end_station_m=right,source_interval=i,
                relative_head_start_m=first,relative_head_end_m=last,
                head_start_m=first+datum,head_end_m=last+datum,
                regime="floor_contact_candidate" if raw["tracking_input"] else "filled_or_marginal",
                prescribed_full_flow_gradient_m_m=gradients[i],elevation_gradient_m_m=slope,
                piezometric_head_loss_m=loss,baseline_full_flow_head_loss_m=baseline,
                additional_floor_head_loss_m=extra,normal_depth_capacity_checked=False)
            check=q.partial_normal_depth_check
            if raw["tracking_input"] and check is not None:
                angle=math.atan(-slope)
                native=SteadyCircularFlow(inner_diameter_m=check.inner_diameter_m,
                    volume_flow_m3_s=check.volume_flow_m3_s,gravity_m_s2=q.gravity_m_s2,
                    signed_downhill_angle_rad=angle,friction_model="chezy",
                    chezy_coefficient_m_half_per_s=check.chezy_coefficient_m_half_per_s,
                    hydrostatic_prismatic_wet_section_reviewed=True)
                depth=solve_circular_normal_depth(CircularDepthRootRequest(flow=native,depth_bracket_m=check.depth_bracket_m))
                if depth.status!="computed":
                    raise ValueError("selected partial-flow capacity branch is unavailable: "+"; ".join(depth.notes))
                velocity=depth.values["mean_velocity_m_s"]
                full_velocity=check.volume_flow_m3_s/(math.pi*check.inner_diameter_m**2/4)
                kinetic_change=(velocity**2-full_velocity**2)/(2*q.gravity_m_s2)
                if check.maximum_velocity_head_change_m is not None and abs(kinetic_change)>check.maximum_velocity_head_change_m:
                    raise ValueError("normal-depth velocity-head change exceeds the declared routing-approximation budget")
                piece.update(normal_depth_capacity_checked=True,normal_depth_m=depth.values["depth_m"],
                    normal_depth_mean_velocity_m_s=velocity,partial_volume_flow_m3_s=check.volume_flow_m3_s,
                    velocity_head_increment_over_full_m=kinetic_change)
            pieces.append(piece)
        pieces.sort(key=lambda row:row["start_station_m"])
        knots=sorted(set((*x,*q.query_stations_m,*(p["start_station_m"] for p in pieces),*(p["end_station_m"] for p in pieces))))
        knot_x=[pieces[0]["start_station_m"],*[p["end_station_m"] for p in pieces]]
        knot_h=[pieces[0]["relative_head_start_m"],*[p["relative_head_end_m"] for p in pieces]]
        rows=[]
        for station in knots:
            relative_elevation=float(np.interp(station,x,z_relative))
            relative_head=float(np.interp(station,knot_x,knot_h))
            pressure=(relative_head-relative_elevation)*q.density_kg_m3*q.gravity_m_s2
            if pressure<q.saturation_pressure_Pa_abs-q.absolute_pressure_tolerance_Pa:
                raise ValueError("routed pressure violates its declared floor beyond numerical tolerance")
            rows.append(dict(station_m=station,elevation_m=relative_elevation+datum,
                head_relative_to_datum_m=relative_head,piezometric_head_m=relative_head+datum,
                pressure_Pa_abs=pressure,pressure_margin_over_saturation_Pa=pressure-q.saturation_pressure_Pa_abs))
        boundary_error=rows[-1]["pressure_Pa_abs"]-q.downstream_pressure_Pa_abs
        if abs(boundary_error)>q.absolute_pressure_tolerance_Pa:
            raise ValueError("head-floor routing failed to retain downstream pressure within its declared precision")
        total_loss=math.fsum(p["piezometric_head_loss_m"] for p in pieces)
        baseline=accumulated[-1]
        extra=math.fsum(p["additional_floor_head_loss_m"] for p in pieces)
        residual=nodes_relative[0]-outlet_head-total_loss
        if abs(residual)>q.head_equation_tolerance_m or abs(total_loss-baseline-extra)>q.head_equation_tolerance_m:
            raise ValueError("head-floor interval head-loss ledger failed")
        events=[]
        for left,right in zip(pieces,pieces[1:]):
            if left["regime"]!=right["regime"]:
                events.append(dict(station_m=left["end_station_m"],
                    kind="gravity_candidate_start" if right["regime"]=="floor_contact_candidate" else "gravity_candidate_end"))
        numeric=[v for row in rows for v in row.values()]
        numeric.extend((total_loss,baseline,extra,residual))
        if not all(math.isfinite(v) for v in numeric):
            raise ValueError("head-floor route exceeds finite numerical support")
    except (ArithmeticError,ValueError,RuntimeError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values=dict(asset_id=q.asset_id,fluid_state_basis_id=q.fluid_state_basis_id,
        rows=rows,segments=pieces,events=events,
        elevation_datum_m=datum,upstream_pressure_Pa_abs=rows[0]["pressure_Pa_abs"],
        downstream_pressure_residual_Pa=boundary_error,total_piezometric_head_loss_m=total_loss,
        baseline_full_flow_head_loss_m=baseline,additional_floor_head_loss_m=extra,
        head_loss_budget_residual_m=residual,partial_capacity_checks_requested=q.partial_normal_depth_check is not None,
        checked_gravity_segment_count=sum(p["normal_depth_capacity_checked"] for p in pieces),
        all_gravity_candidate_capacity_checked=all(p["normal_depth_capacity_checked"] for p in pieces if p["regime"]=="floor_contact_candidate"),
        gas_volume_predicted=False,conservative_moving_interface_solution=False),notes=(
        "Minimal equilibrium-floor piezometric route with prescribed full-flow gradients and fixed downstream pressure; contact means candidate gravity flow, not an identified vapor amount.",
        "A supplied normal-depth check tests only the chosen local capacity branch and optional velocity-head budget. Filled resistance remains independently supplied; no lossless interface/jump or dynamic gas state is inferred.",
        "Velocity-head transition corrections, nonuniform cavity pressure, thermal/composition effects, gas mass and actual filled/free-surface interface momentum require the separate flow model.",))
