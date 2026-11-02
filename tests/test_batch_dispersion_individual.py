"""Independent heat-kernel checks and positive-dispersion finite-pipe checks."""
import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.special import erfc, erfcx

from pipeline_integrity.batch_dispersion import (
    AxialDispersionRequest, BatchInterfaceRequest,
    evaluate_axial_dispersion, evaluate_batch_interface,
)
from pipeline_integrity.batch_transport_solver import evaluate_conservative_batch_transport
from test_batch_transport_physical_qualification import _case


@pytest.mark.parametrize("speed", [-1.7, 0, 2.3])
@pytest.mark.parametrize("diffusivity", [0.015, 0.4])
def test_interface_equals_independent_gaussian_convolution(speed, diffusivity):
    time, origin = 3.2, 0.7
    centre = origin + speed * time
    xs = tuple(centre + z * math.sqrt(2 * diffusivity * time) for z in (-3, -0.5, 0, 1, 4))
    q = BatchInterfaceRequest(axial_positions_m=xs, elapsed_time_s=time,
        axial_dispersion_m2_s=diffusivity, mean_velocity_m_s=speed,
        initial_interface_position_m=origin)
    got = evaluate_batch_interface(q)
    expected = [quad(lambda y: math.exp(-(x - speed*time - y)**2/(4*diffusivity*time))
                     / math.sqrt(4*math.pi*diffusivity*time), -math.inf, origin,
                     epsabs=1e-12)[0] for x in xs]
    assert got.displacing_component_volume_fractions == pytest.approx(expected, abs=2e-12)
    assert got.mixing_volume_m3 is None


@pytest.mark.parametrize("diffusivity,time", [(0, 2), (0.3, 0)])
def test_sharp_front_and_half_value_convention(diffusivity, time):
    centre = 0.8 * time
    got = evaluate_batch_interface(BatchInterfaceRequest(axial_positions_m=(centre-1, centre, centre+1),
        elapsed_time_s=time, mean_velocity_m_s=0.8, axial_dispersion_m2_s=diffusivity,
        cross_section_area_m2=0.9))
    assert got.displacing_component_volume_fractions == (1, 0.5, 0)
    assert got.mixing_length_m == got.mixing_volume_m3 == 0


def test_interface_threshold_inverse_and_exact_time_area_similarity():
    q = BatchInterfaceRequest(axial_positions_m=(0,), elapsed_time_s=2,
        axial_dispersion_m2_s=0.3, mean_velocity_m_s=-0.4,
        lower_volume_fraction=0.07, upper_volume_fraction=0.91, cross_section_area_m2=0.7)
    r = evaluate_batch_interface(q)
    at = evaluate_batch_interface(q.model_copy(update={"axial_positions_m":r.mixing_interval_m}))
    assert at.displacing_component_volume_fractions == pytest.approx((0.91, 0.07), abs=2e-15)
    assert r.mixing_volume_m3 == pytest.approx(0.7*r.mixing_length_m)
    later = evaluate_batch_interface(q.model_copy(update={"elapsed_time_s":8}))
    assert later.mixing_length_m == pytest.approx(2*r.mixing_length_m)
    assert later.interface_centre_m == pytest.approx(4*r.interface_centre_m)


def test_heat_kernel_solves_positive_diffusion_pde_and_spreads_variance():
    def theta(x, t):
        return evaluate_batch_interface(BatchInterfaceRequest(axial_positions_m=(x,), elapsed_time_s=t,
            axial_dispersion_m2_s=0.3, mean_velocity_m_s=0.8)).displacing_component_volume_fractions[0]
    x, t, dx, dt = 1.1, 1.3, 2e-4, 2e-5
    ct = (theta(x,t+dt)-theta(x,t-dt))/(2*dt)
    cx = (theta(x+dx,t)-theta(x-dx,t))/(2*dx)
    cxx = (theta(x+dx,t)-2*theta(x,t)+theta(x-dx,t))/dx**2
    assert ct+0.8*cx == pytest.approx(0.3*cxx, abs=2e-8)
    # The negative spatial derivative is a normalized Gaussian with variance 2Dt.
    density = lambda y: math.exp(-(y-0.8*t)**2/(4*0.3*t))/math.sqrt(4*math.pi*0.3*t)
    assert quad(density,-math.inf,math.inf)[0] == pytest.approx(1, abs=1e-10)
    assert quad(lambda y:(y-0.8*t)**2*density(y),-math.inf,math.inf)[0] == pytest.approx(2*0.3*t)


def test_missing_dispersion_and_invalid_thresholds_are_preserved():
    assert evaluate_batch_interface(BatchInterfaceRequest(axial_positions_m=(0,),elapsed_time_s=1)).status == "needs_data"
    with pytest.raises(ValueError):
        BatchInterfaceRequest(axial_positions_m=(0,),elapsed_time_s=1,lower_volume_fraction=0.9,upper_volume_fraction=0.2)
    with pytest.raises(ValueError):
        BatchInterfaceRequest(axial_positions_m=(float("nan"),),elapsed_time_s=1)


@pytest.mark.parametrize("changes", [
    {"mean_velocity_m_s":1e308,"elapsed_time_s":2},
    {"initial_interface_position_m":1e100},
    {"axial_dispersion_m2_s":1e-300,"elapsed_time_s":1e-300}])
def test_unresolved_or_overflowed_spreading_is_not_zero_mixing(changes):
    q=BatchInterfaceRequest(axial_positions_m=(0,),elapsed_time_s=1,axial_dispersion_m2_s=0.2)
    assert evaluate_batch_interface(q.model_copy(update=changes)).status == "out_of_scope"


def test_supplied_zero_diffusion_does_not_require_empirical_turbulent_review():
    r=evaluate_axial_dispersion(AxialDispersionRequest(supplied_dispersion_m2_s=0))
    assert r.status == "ok" and r.axial_dispersion_m2_s == 0
    assert not r.empirical_accuracy_claim


@pytest.mark.parametrize("factor", [0.008, 0.025, 0.07])
def test_turbulent_dispersion_independent_wall_shear_coordinate(factor):
    q = AxialDispersionRequest(method="turbulent_taylor",mean_velocity_m_s=-2,
        internal_diameter_m=0.8,darcy_friction_factor=factor,reynolds_number=1e5,
        turbulent_calibration_and_similar_properties_reviewed=True)
    r = evaluate_axial_dispersion(q)
    friction_velocity = math.sqrt(factor/8)*2
    # Re-express the declared closure in wall-shear velocity and radius.
    assert r.axial_dispersion_m2_s == pytest.approx(1.785*math.sqrt(32)*friction_velocity*0.4)
    scaled = evaluate_axial_dispersion(q.model_copy(update={"internal_diameter_m":1.6,"mean_velocity_m_s":4}))
    assert scaled.axial_dispersion_m2_s == pytest.approx(4*r.axial_dispersion_m2_s)


def test_paired_width_average_and_identical_branch_limit():
    q = AxialDispersionRequest(method="paired_turbulent",mean_velocity_m_s=1.3,internal_diameter_m=0.4,
        darcy_friction_factor=0.01, second_darcy_friction_factor=0.04,
        reynolds_number=1e5,second_reynolds_number=2e5,
        turbulent_calibration_and_similar_properties_reviewed=True,paired_mixing_width_heuristic_reviewed=True)
    r = evaluate_axial_dispersion(q)
    widths = [math.sqrt(1.785*1.3*0.4*math.sqrt(f)) for f in (0.01,0.04)]
    assert math.sqrt(r.axial_dispersion_m2_s) == pytest.approx(sum(widths)/2)
    same = evaluate_axial_dispersion(q.model_copy(update={"second_darcy_friction_factor":0.01}))
    single = evaluate_axial_dispersion(q.model_copy(update={"method":"turbulent_taylor"}))
    assert same.axial_dispersion_m2_s == pytest.approx(single.axial_dispersion_m2_s)


@pytest.mark.parametrize("changes,status", [({},"needs_data"),
    ({"mean_velocity_m_s":1,"internal_diameter_m":0.4,"darcy_friction_factor":0.02,"reynolds_number":100},"out_of_scope"),
    ({"mean_velocity_m_s":1,"internal_diameter_m":0.4,"darcy_friction_factor":0.02,"reynolds_number":1e5},"needs_review")])
def test_turbulent_closure_is_not_inferred_without_inputs_or_review(changes,status):
    assert evaluate_axial_dispersion(AxialDispersionRequest(method="turbulent_taylor",**changes)).status == status


def _finite_pipe(cells, dt, *, duration=2, thermal=False, steps=None):
    request, context = _case(route=10,area=0.7,flow=0.7,duration=duration,cells=cells,thermal=thermal)
    transport = request.transport.model_copy(update={"inlet_steps":steps or request.transport.inlet_steps})
    config = request.solver.model_copy(update={"axial_dispersion_m2_s":0.2,"maximum_step_s":dt,
        "maximum_scalar_field_values":8000000,"maximum_result_bytes":128000000})
    r = evaluate_conservative_batch_transport(request.model_copy(update={"transport":transport,"solver":config}),context)
    native = r["result"]["native"]
    for ledger in native["component_mass_ledger_kg"].values():
        assert ledger["initial"]+ledger["inlet"]-ledger["outlet"]-ledger["final"]+ledger["numerical_adjustment"] == pytest.approx(0, abs=2e-8)
    assert native["maximum_roundoff_volume_fraction_projection"] < 2e-14
    return native


def _danckwerts_semi_infinite(x, t, speed=1.0, diffusion=0.2):
    a=(x-speed*t)/math.sqrt(4*diffusion*t)
    b=(x+speed*t)/math.sqrt(4*diffusion*t)
    return (0.5*erfc(a)+math.sqrt(speed**2*t/(math.pi*diffusion))*np.exp(-a*a)
            -0.5*(1+speed*x/diffusion+speed**2*t/diffusion)*np.exp(-a*a)*erfcx(b))


def test_danckwerts_continuum_oracle_satisfies_robin_boundary_and_pde():
    t,x,h=2,1.2,1e-4
    c=lambda a,b:_danckwerts_semi_infinite(a,b)
    derivative=(-3*c(0,t)+4*c(h,t)-c(2*h,t))/(2*h)
    assert c(0,t)-0.2*derivative == pytest.approx(1,abs=1e-8)
    ct=(c(x,t+h)-c(x,t-h))/(2*h)
    cx=(c(x+h,t)-c(x-h,t))/(2*h)
    cxx=(c(x+h,t)-2*c(x,t)+c(x-h,t))/h**2
    assert ct+cx == pytest.approx(0.2*cxx,abs=2e-8)


def test_positive_dispersion_continuum_refinement_with_different_component_densities():
    errors=[]
    for cells in (80,160,320):
        r=_finite_pipe(cells,0.2*10/cells)
        x=np.asarray(r["cell_centres_m"])
        phi=np.asarray(r["final_cell_component_volume_fractions"])[:,1]
        exact=_danckwerts_semi_infinite(x,2)
        errors.append(float(np.mean(np.abs(phi-exact))))
        assert np.min(phi)>=0 and np.max(phi)<=1
    assert errors[1] < 0.65*errors[0]
    assert errors[2] < 0.65*errors[1]
    assert errors[-1] < 0.003


def test_positive_dispersion_time_refinement_and_event_mass_budget():
    values=[]
    for dt in (0.2,0.1,0.05):
        r=_finite_pipe(120,dt)
        values.append(np.asarray(r["final_cell_component_volume_fractions"])[:,1])
    coarse=float(np.linalg.norm(values[0]-values[1],1))
    fine=float(np.linalg.norm(values[1]-values[2],1))
    assert coarse > 1.65*fine > 0
    request,_=_case(route=10,area=0.7,flow=0.7,duration=2,cells=48)
    first=request.transport.inlet_steps[0]
    second=first.model_copy(update={"time_s":0.73,"composition":request.transport.initial_composition})
    r=_finite_pipe(48,0.13,steps=(first,second))
    assert 0.73 in r["time_s"]
    index=r["time_s"].index(0.73)*(48+2)
    assert r["fraction_by_component"]["B"][index] == 0
    assert r["component_mass_ledger_kg"]["B"]["inlet"] == pytest.approx(0.7*0.73*1000)
