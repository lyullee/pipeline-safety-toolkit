"""Exact linear-curve station assemblies and independent serial-head budgets."""
import math

import pytest

from pipeline_integrity.models.hydraulics import LiquidHeadLoss
from pipeline_integrity.models.pump_curves import (
    PumpSeriesBranch,PumpStationAssembly,ReferencePumpCurve,
    SteadyPumpPathStation,SteadyPumpStationPath,solve_pump_station_path,
)

G=9.80665


def _curve(intercept,slope):
    return ReferencePumpCurve(volume_flow_m3_s=(0.0,0.003,0.006),
        head_m=tuple(intercept-slope*x for x in (0.0,0.003,0.006)),source_id="authored_linear_curve")


def _request(*,minor=0.0,datum=0.0,density=1000.0,pressure_scale=1.0):
    parallel=PumpStationAssembly(parallel_branches=(
        PumpSeriesBranch(curves=(_curve(100,10000),)),
        PumpSeriesBranch(curves=(_curve(120,20000),))))
    series=PumpStationAssembly(parallel_branches=(
        PumpSeriesBranch(curves=(_curve(70,4000),_curve(80,6000))),))
    stations=[]
    for index,(elevation,length,assembly) in enumerate(((30,500,parallel),(65,700,series))):
        pipe=LiquidHeadLoss(diameter_m=0.08,length_m=float(length),roughness_m=0.0,
            volume_flow_m3_s=0.0,kinematic_viscosity_m2_s=0.0005,minor_loss_coefficient=minor)
        stations.append(SteadyPumpPathStation(id=f"station_{index}",elevation_m=elevation+datum,
            pumps=assembly,following_pipe_head_loss_templates=(pipe,)))
    return SteadyPumpStationPath(stations=tuple(stations),upstream_pressure_pa_abs=1.5e6*pressure_scale,
        downstream_pressure_pa_abs=1.3e6*pressure_scale,downstream_elevation_m=100.0+datum,
        density_kg_m3=density,absolute_flow_tolerance_m3_s=1e-14,maximum_head_residual_m=1e-7)


def _exact(q):
    # Parallel linear branches: H=(sum(a_i/b_i)-Q)/sum(1/b_i).
    intercept=(100/10000+120/20000)/(1/10000+1/20000)+70+80
    slope=1/(1/10000+1/20000)+4000+6000
    static=(q.downstream_pressure_pa_abs-q.upstream_pressure_pa_abs)/(q.density_kg_m3*G)+70
    viscous=sum(128*s.kinematic_viscosity_m2_s*s.length_m/(math.pi*G*s.diameter_m**4)
                for station in q.stations for s in station.following_pipe_head_loss_templates)
    quadratic=sum(s.minor_loss_coefficient/(2*G*(math.pi*s.diameter_m**2/4)**2)
                  for station in q.stations for s in station.following_pipe_head_loss_templates)
    c=intercept-static
    flow=c/(slope+viscous) if quadratic==0 else 2*c/(slope+viscous+math.sqrt((slope+viscous)**2+4*quadratic*c))
    return flow,viscous,quadratic


@pytest.mark.parametrize("minor",[0.0,1.5,12.0])
def test_nonidentical_series_parallel_path_matches_exact_root_and_station_pressures(minor):
    q=_request(minor=minor)
    expected,viscous,quadratic=_exact(q)
    r=solve_pump_station_path(q)
    assert r.status == "computed",r.notes
    v=r.values
    assert v["volume_flow_m3_s"] == pytest.approx(expected,rel=2e-11)
    assert abs(v["head_balance_residual_m"])<1e-7
    assert abs(v["downstream_pressure_residual_Pa"])<1e-3
    rows=v["station_results"]
    first_head=(100/10000+120/20000-expected)/(1/10000+1/20000)
    assert rows[0]["pump_differential_head_m"] == pytest.approx(first_head)
    branch_flows=((100-first_head)/10000,(120-first_head)/20000)
    assert rows[0]["parallel_branch_flows_m3_s"] == pytest.approx(branch_flows)
    assert sum(branch_flows) == pytest.approx(expected)
    assert rows[1]["parallel_branch_flows_m3_s"] == pytest.approx((expected,))
    assert rows[1]["pump_differential_head_m"] == pytest.approx(150-10000*expected)
    for row in rows:
        assert row["discharge_pressure_pa_abs"]-row["suction_pressure_pa_abs"] == pytest.approx(
            q.density_kg_m3*G*row["pump_differential_head_m"])
    first_pipe=q.stations[0].following_pipe_head_loss_templates[0]
    first_loss=128*first_pipe.kinematic_viscosity_m2_s*first_pipe.length_m*expected/(math.pi*G*first_pipe.diameter_m**4)
    first_loss+=minor*(expected/(math.pi*first_pipe.diameter_m**2/4))**2/(2*G)
    second_suction=q.upstream_pressure_pa_abs+q.density_kg_m3*G*(first_head-first_loss-35)
    assert rows[1]["suction_pressure_pa_abs"] == pytest.approx(second_suction,abs=1e-4)
    pipe_work=q.density_kg_m3*G*expected*(viscous*expected+quadratic*expected**2)
    boundary_work=expected*(q.downstream_pressure_pa_abs-q.upstream_pressure_pa_abs)+q.density_kg_m3*G*expected*70
    assert v["hydraulic_power_W"] == pytest.approx(pipe_work+boundary_work,rel=2e-11)
    assert v["caller_pressure_constraints_status"] == "not_supplied"


@pytest.mark.parametrize("datum",[-200.0,500.0,10000.0])
def test_elevation_datum_does_not_change_pressure_or_flow(datum):
    base=solve_pump_station_path(_request()).values
    shifted=solve_pump_station_path(_request(datum=datum)).values
    assert shifted["volume_flow_m3_s"] == pytest.approx(base["volume_flow_m3_s"],rel=2e-12)
    for got,reference in zip(shifted["station_results"],base["station_results"],strict=True):
        assert got["suction_pressure_pa_abs"] == pytest.approx(reference["suction_pressure_pa_abs"],abs=2e-6)
        assert got["discharge_pressure_pa_abs"] == pytest.approx(reference["discharge_pressure_pa_abs"],abs=2e-6)


def test_density_pressure_similarity_preserves_common_flow_and_scales_power():
    base=solve_pump_station_path(_request()).values
    double=solve_pump_station_path(_request(density=2000,pressure_scale=2)).values
    assert double["volume_flow_m3_s"] == pytest.approx(base["volume_flow_m3_s"])
    assert double["hydraulic_power_W"] == pytest.approx(2*base["hydraulic_power_W"])
    assert double["mass_flow_kg_s"] == pytest.approx(2*base["mass_flow_kg_s"])


def test_station_and_pipe_order_preserve_flow_but_change_intermediate_pressure():
    q=_request()
    base=solve_pump_station_path(q).values
    swapped=solve_pump_station_path(q.model_copy(update={"stations":tuple(reversed(q.stations))})).values
    # Preserve the same upstream datum by shifting the swapped first station to z=30.
    stations=tuple(s.model_copy(update={"elevation_m":s.elevation_m-35}) for s in reversed(q.stations))
    rq=q.model_copy(update={"stations":stations})
    switched=solve_pump_station_path(rq).values
    assert switched["volume_flow_m3_s"] == pytest.approx(base["volume_flow_m3_s"],rel=2e-11)
    assert switched["station_results"][1]["suction_pressure_pa_abs"] != pytest.approx(
        base["station_results"][1]["suction_pressure_pa_abs"],rel=1e-3)
    assert swapped["volume_flow_m3_s"] != pytest.approx(base["volume_flow_m3_s"],rel=1e-3)


def test_serial_pipe_partition_and_branch_permutation_do_not_change_root():
    q=_request(minor=3)
    stations=[]
    for station in q.stations:
        pipe=station.following_pipe_head_loss_templates[0]
        fragments=tuple(pipe.model_copy(update={"length_m":pipe.length_m/3,
            "minor_loss_coefficient":pipe.minor_loss_coefficient/3}) for _ in range(3))
        assembly=station.pumps.model_copy(update={"parallel_branches":tuple(reversed(station.pumps.parallel_branches))})
        stations.append(station.model_copy(update={"pumps":assembly,"following_pipe_head_loss_templates":fragments}))
    base=solve_pump_station_path(q).values
    changed=solve_pump_station_path(q.model_copy(update={"stations":tuple(stations)})).values
    assert changed["volume_flow_m3_s"] == pytest.approx(base["volume_flow_m3_s"],rel=2e-11)
    assert changed["hydraulic_power_W"] == pytest.approx(base["hydraulic_power_W"])


@pytest.mark.parametrize("violate",[False,True])
def test_caller_pressure_thresholds_are_visible_without_claiming_cavitation_acceptance(violate):
    q=_request()
    first=q.stations[0].model_copy(update={"minimum_suction_pressure_pa_abs":2e6 if violate else 1e6,
        "maximum_discharge_pressure_pa_abs":2e6 if violate else 5e6})
    r=solve_pump_station_path(q.model_copy(update={"stations":(first,q.stations[1])}))
    assert r.status == "computed"
    assert r.values["caller_pressure_constraints_status"] == ("violated" if violate else "satisfied")
    assert r.values["station_results"][1]["minimum_suction_pressure_margin_Pa"] is None


def test_unsupported_parallel_head_ranges_are_not_hidden_by_branch_shutoff():
    q=_request()
    unsupported=PumpStationAssembly(parallel_branches=(
        PumpSeriesBranch(curves=(_curve(100,10000),)),
        PumpSeriesBranch(curves=(_curve(30,4000),))))
    first=q.stations[0].model_copy(update={"pumps":unsupported})
    r=solve_pump_station_path(q.model_copy(update={"stations":(first,q.stations[1])}))
    assert r.status == "out_of_scope" and not r.values
    assert "common supported differential head" in " ".join(r.notes)


def test_unsupported_serial_flow_ranges_and_unbracketed_path_are_withheld():
    q=_request()
    disjoint=ReferencePumpCurve(volume_flow_m3_s=(0.01,0.02),head_m=(40.0,10.0),source_id="authored_disjoint")
    series=PumpStationAssembly(parallel_branches=(PumpSeriesBranch(curves=(_curve(70,4000),disjoint)),))
    first=q.stations[0].model_copy(update={"pumps":series})
    r=solve_pump_station_path(q.model_copy(update={"stations":(first,q.stations[1])}))
    assert r.status == "out_of_scope"
    impossible=solve_pump_station_path(q.model_copy(update={"downstream_pressure_pa_abs":1e8}))
    assert impossible.status == "out_of_scope" and not impossible.values


def test_duplicate_stations_and_conflicting_pipe_flow_are_rejected():
    q=_request()
    with pytest.raises(ValueError,match="identifiers"):
        SteadyPumpStationPath.model_validate({**q.model_dump(),"stations":(q.stations[0],q.stations[0])})
    first=q.stations[0]
    pipe=first.following_pipe_head_loss_templates[0].model_copy(update={"volume_flow_m3_s":0.001})
    with pytest.raises(ValueError,match="placeholder"):
        SteadyPumpPathStation.model_validate({**first.model_dump(),"following_pipe_head_loss_templates":(pipe,)})
