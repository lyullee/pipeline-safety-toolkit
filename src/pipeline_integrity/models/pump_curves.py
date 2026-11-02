"""Steady pump curves and common-flow head balances with explicit branch support."""

import math
from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Name, Nonnegative, Positive
from .equipment import PumpPower, pump_power
from .hydraulics import LiquidHeadLoss, liquid_head_loss


class ReferencePumpCurve(Contract):
    volume_flow_m3_s: tuple[Nonnegative, ...] = Field(min_length=2)
    head_m: tuple[Nonnegative, ...] = Field(min_length=2)
    source_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def monotone_branch(self):
        if len(self.volume_flow_m3_s) != len(self.head_m):
            raise ValueError("flow and head knots must match")
        if any(b <= a for a, b in zip(self.volume_flow_m3_s, self.volume_flow_m3_s[1:], strict=False)):
            raise ValueError("reference flow knots must strictly increase")
        if any(b >= a for a, b in zip(self.head_m, self.head_m[1:], strict=False)):
            raise ValueError("this operating-point adapter requires a strictly decreasing pump branch")
        return self


class PumpSystem(Contract):
    reference_curve: ReferencePumpCurve
    static_head_m: float
    resistance_head_s2_m5: Nonnegative
    parallel_pumps: int = Field(ge=1, le=10000)
    series_pumps: int = Field(ge=1, le=10000)
    speed_ratio: Positive = 1.0
    geometric_diameter_ratio: Positive = 1.0
    density_kg_m3: Positive
    pump_efficiency: Positive = Field(le=1)
    motor_efficiency: Positive = Field(le=1)
    gravity_m_s2: Positive = 9.80665


class PumpPipeSystem(PumpSystem):
    """Direct Python API for a pump branch and reviewed serial-pipe geometry.

    The registered quadratic-only input remains ``PumpSystem``. Both input
    types use the same operating-point solver and power owner.
    """
    pipe_head_loss_templates: tuple[LiquidHeadLoss, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def pipe_templates(self):
        # The root owns the common flow; static head owns all elevation and
        # terminal pressure-head requirements. Templates contain losses only.
        if any(s.volume_flow_m3_s != 0 or s.outlet_minus_inlet_elevation_m != 0
               for s in self.pipe_head_loss_templates):
            raise ValueError("pipe templates require zero placeholder flow and zero elevation; the operating root and static head own those inputs")
        return self


def _head_intersection(pump_head, system_head, low, high, absolute_flow_tolerance=1e-14):
    """One operating-point root shared by equivalent and explicit station paths."""
    from scipy.optimize import brentq

    def residual(flow):
        value=float(pump_head(flow)-system_head(flow))
        if not math.isfinite(value):
            raise ValueError("head balance exceeds finite arithmetic")
        return value
    if not low < high or residual(low) < 0 or residual(high) > 0:
        raise ValueError("No operating point lies on the supplied monotone pump branch; extrapolation is withheld.")
    flow=float(brentq(residual,low,high,xtol=absolute_flow_tolerance,rtol=1e-13))
    return flow,float(system_head(flow))


def pump_system(p: PumpSystem):
    from scipy.interpolate import PchipInterpolator

    p=type(p).model_validate(p.model_dump(mode="python"))
    segments=getattr(p,"pipe_head_loss_templates",())
    curve = PchipInterpolator(p.reference_curve.volume_flow_m3_s, p.reference_curve.head_m, extrapolate=False)
    flow_scale = p.parallel_pumps * p.speed_ratio * p.geometric_diameter_ratio**3
    head_scale = p.series_pumps * p.speed_ratio**2 * p.geometric_diameter_ratio**2
    low = float(p.reference_curve.volume_flow_m3_s[0] * flow_scale)
    high = float(p.reference_curve.volume_flow_m3_s[-1] * flow_scale)

    def system_head(flow):
        result = p.static_head_m + p.resistance_head_s2_m5 * flow**2
        for segment in segments:
            loss = liquid_head_loss(segment.model_copy(update={"volume_flow_m3_s": flow}))
            if loss.status != "computed":
                raise ValueError("pipe head-loss owner withheld operating-point trial: " + "; ".join(loss.notes))
            result += loss.values["dissipative_head_loss_m"]
        return result

    def residual(flow):
        return float(head_scale * curve(flow / flow_scale) - system_head(flow))

    try:
        flow,head=_head_intersection(lambda flow:float(head_scale*curve(flow/flow_scale)),system_head,low,high)
    except (ValueError, ArithmeticError) as error:
        return Calculation(
            values={}, status="out_of_scope", notes=(str(error),),
        )
    power = pump_power(
        PumpPower(
            volume_flow_m3_s=flow,
            density_kg_m3=p.density_kg_m3,
            head_m=head,
            pump_efficiency=p.pump_efficiency,
            motor_efficiency=p.motor_efficiency,
            gravity_m_s2=p.gravity_m_s2,
        )
    )
    values={
            "volume_flow_m3_s": flow,
            "total_head_m": head,
            "flow_per_parallel_branch_m3_s": flow / p.parallel_pumps,
            "head_per_series_stage_m": head / p.series_pumps,
            "head_intersection_residual_m": residual(flow),
            **power.values,
        }
    if segments:
        values["pipe_head_losses"] = [liquid_head_loss(s.model_copy(update={"volume_flow_m3_s":flow})).model_dump(mode="json")
                                      for s in segments]
        values["declared_static_head_m"] = p.static_head_m
        values["additional_quadratic_loss_head_m"] = p.resistance_head_s2_m5 * flow**2
    return Calculation(
        values=values,
        notes=(
            "Identical pumps, forward monotone branch; declared static/quadratic head plus optional reviewed serial-pipe losses at the solved common flow.",
            "PCHIP interpolation preserves the supplied branch shape; data are never extrapolated.",
            "Similar geometry and declared constant efficiencies; no surge, cavitation or actuator dynamics qualification.",
        ),
    )


class PumpSeriesBranch(Contract):
    curves: tuple[ReferencePumpCurve, ...] = Field(min_length=1,max_length=100)


class PumpStationAssembly(Contract):
    parallel_branches: tuple[PumpSeriesBranch, ...] = Field(min_length=1,max_length=100)


class SteadyPumpPathStation(Contract):
    id: Name
    elevation_m: float
    pumps: PumpStationAssembly
    following_pipe_head_loss_templates: tuple[LiquidHeadLoss, ...] = Field(min_length=1,max_length=100)
    minimum_suction_pressure_pa_abs: Positive | None = None
    maximum_discharge_pressure_pa_abs: Positive | None = None

    @model_validator(mode="after")
    def loss_ownership(self):
        if any(s.volume_flow_m3_s != 0 or s.outlet_minus_inlet_elevation_m != 0
               for s in self.following_pipe_head_loss_templates):
            raise ValueError("path pipe templates require zero placeholder flow/elevation; station elevations own the static datum")
        return self


class SteadyPumpStationPath(Contract):
    stations: tuple[SteadyPumpPathStation, ...] = Field(min_length=1,max_length=100)
    upstream_pressure_pa_abs: Positive
    downstream_pressure_pa_abs: Positive
    downstream_elevation_m: float
    density_kg_m3: Positive
    gravity_m_s2: Positive = 9.80665
    absolute_flow_tolerance_m3_s: Positive = 1e-13
    maximum_head_residual_m: Positive = 1e-6

    @model_validator(mode="after")
    def unique_stations(self):
        if len({s.id for s in self.stations}) != len(self.stations):
            raise ValueError("station identifiers must be unique")
        return self


def _inverse_decreasing(function,target,lower,upper):
    from scipy.optimize import brentq

    for coordinate in (lower,upper):
        value=float(function(coordinate))
        if abs(value-target) <= 64*math.ulp(max(abs(value),abs(target),1e-300)):
            return coordinate
    # Normalize the independent coordinate; flow and head need different units.
    fraction=brentq(lambda z:function(lower+(upper-lower)*z)-target,0,1,xtol=1e-13,rtol=1e-13)
    return lower+(upper-lower)*fraction


def _compiled_station(assembly):
    from scipy.interpolate import PchipInterpolator

    branches=[]
    for branch in assembly.parallel_branches:
        curves=[PchipInterpolator(c.volume_flow_m3_s,c.head_m,extrapolate=False) for c in branch.curves]
        lower=max(c.volume_flow_m3_s[0] for c in branch.curves)
        upper=min(c.volume_flow_m3_s[-1] for c in branch.curves)
        if not lower < upper:
            raise ValueError("series pump curves have no common forward-flow support")
        def head(flow,curves=curves):
            return math.fsum(float(c(flow)) for c in curves)
        branches.append((head,lower,upper))
    if len(branches)==1:
        head,lower,upper=branches[0]
        return head,lower,upper,lambda flow:[flow]
    head_lower=max(f(upper) for f,lower,upper in branches)
    head_upper=min(f(lower) for f,lower,upper in branches)
    if not head_lower < head_upper:
        raise ValueError("parallel pump branches have no common supported differential head")
    def flows_at_head(value):
        return [_inverse_decreasing(f,value,lower,upper) for f,lower,upper in branches]
    def total_flow(value):
        return math.fsum(flows_at_head(value))
    lower,upper=total_flow(head_upper),total_flow(head_lower)
    def head_at_flow(flow):
        return _inverse_decreasing(total_flow,flow,head_lower,head_upper)
    return head_at_flow,lower,upper,lambda flow:flows_at_head(head_at_flow(flow))


def solve_pump_station_path(request: SteadyPumpStationPath):
    """Common steady flow and station pressures; no dynamic/network qualification.

    Each parallel branch must stay on its supplied active forward curve. Inactive
    branches, check valves and reverse operation are not silently extrapolated.
    """
    q=SteadyPumpStationPath.model_validate(request.model_dump(mode="python"))
    try:
        compiled=[_compiled_station(s.pumps) for s in q.stations]
        low=max(c[1] for c in compiled)
        high=min(c[2] for c in compiled)
        rho_g=q.density_kg_m3*q.gravity_m_s2
        static=(q.downstream_pressure_pa_abs-q.upstream_pressure_pa_abs)/rho_g+q.downstream_elevation_m-q.stations[0].elevation_m
        def pipe_losses(station,flow):
            receipts=[liquid_head_loss(s.model_copy(update={"volume_flow_m3_s":flow}))
                      for s in station.following_pipe_head_loss_templates]
            if any(r.status!="computed" for r in receipts):
                raise ValueError("a station-path pipe trial is outside the selected hydraulic closure")
            return math.fsum(r.values["dissipative_head_loss_m"] for r in receipts),receipts
        def pump_head(flow):
            return math.fsum(c[0](flow) for c in compiled)
        def system_head(flow):
            return static+math.fsum(pipe_losses(s,flow)[0] for s in q.stations)
        flow,_=_head_intersection(pump_head,system_head,low,high,q.absolute_flow_tolerance_m3_s)
        residual=pump_head(flow)-system_head(flow)
        if abs(residual)>q.maximum_head_residual_m:
            raise ValueError("station-path root exceeds the declared head residual")
        total_head=q.upstream_pressure_pa_abs/rho_g+q.stations[0].elevation_m
        rows=[]
        constraints=[]
        for index,(station,native) in enumerate(zip(q.stations,compiled,strict=True)):
            suction=rho_g*(total_head-station.elevation_m)
            rise=native[0](flow)
            discharge=suction+rho_g*rise
            loss,receipts=pipe_losses(station,flow)
            total_head+=rise-loss
            next_elevation=q.stations[index+1].elevation_m if index+1<len(q.stations) else q.downstream_elevation_m
            outlet=rho_g*(total_head-next_elevation)
            if min(suction,discharge,outlet)<=0:
                raise ValueError("the solved path leaves positive absolute pressure at a station or segment endpoint")
            suction_margin=None if station.minimum_suction_pressure_pa_abs is None else suction-station.minimum_suction_pressure_pa_abs
            discharge_margin=None if station.maximum_discharge_pressure_pa_abs is None else station.maximum_discharge_pressure_pa_abs-discharge
            constraints.extend(x for x in (suction_margin,discharge_margin) if x is not None)
            rows.append({"station_id":station.id,"suction_pressure_pa_abs":suction,
                "discharge_pressure_pa_abs":discharge,"following_outlet_pressure_pa_abs":outlet,
                "pump_differential_head_m":rise,"following_pipe_loss_head_m":loss,
                "parallel_branch_flows_m3_s":native[3](flow),
                "hydraulic_power_W":rho_g*flow*rise,
                "pipe_head_loss_receipts":[r.model_dump(mode="json") for r in receipts],
                "minimum_suction_pressure_margin_Pa":suction_margin,
                "maximum_discharge_pressure_margin_Pa":discharge_margin})
        return Calculation(values={"volume_flow_m3_s":flow,"mass_flow_kg_s":q.density_kg_m3*flow,
            "station_results":rows,"head_balance_residual_m":residual,
            "downstream_pressure_residual_Pa":rows[-1]["following_outlet_pressure_pa_abs"]-q.downstream_pressure_pa_abs,
            "hydraulic_power_W":math.fsum(r["hydraulic_power_W"] for r in rows),
            "caller_pressure_constraints_status":("not_supplied" if not constraints else "violated" if min(constraints)<0 else "satisfied")},
            notes=("Steady constant-density path with no intermediate withdrawal; selected active forward pump curves and reviewed pipe closures only.",
                "Pressure margins apply only to explicitly supplied station thresholds, not cavitation or strength acceptance. Internal elevation peaks and dynamics are not inferred."))
    except (ValueError,ArithmeticError,RuntimeError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
