"""Independent manufactured mechanical/thermal limits for reviewed scalar owners."""
import math,runpy
import numpy as np
import pytest
from scipy.integrate import quad,solve_bvp
from pipeline_integrity.screw_pump import ScrewPumpRequest,evaluate_screw_pump
from pipeline_integrity.weld_wall_thermal_limit import WeldHeatStage,WeldWallThermalLimitRequest,evaluate_weld_wall_thermal_limit
from pipeline_integrity.slick_observation import SlickThicknessBand,SlickObservationRequest,evaluate_slick_observation
from pipeline_integrity.fabrication_exchange import evaluate_fabrication

@pytest.mark.parametrize('speed,mu,pressure',[(2.,.02,1e5),(10.,.1,4e5),(0.,.05,2e5)])
def test_screw_supplied_gap_closures_against_independent_stokes_fields(speed,mu,pressure):
    width=.1;gap=1e-4;length=.02;ri=.04;ro=.041;drag_length=.1;omega=2*math.pi*speed
    y=np.linspace(0,gap,11)
    planar=solve_bvp(lambda x,u:np.vstack((u[1],np.full_like(x,-pressure/(mu*length)))),lambda a,b:np.array([a[0],b[0]]),y,np.zeros((2,len(y))),tol=1e-10)
    assert planar.success
    leakage=width*quad(lambda x:float(planar.sol(x)[0]),0,gap,epsabs=1e-15)[0]
    radii=np.linspace(ri,ro,11)
    azimuth=solve_bvp(lambda r,v:np.vstack((v[1],-v[1]/r+v[0]/r**2)),lambda a,b:np.array([a[0]-omega*ri,b[0]]),radii,np.zeros((2,len(radii))),tol=1e-10)
    assert azimuth.success
    traction=mu*(azimuth.y[1,0]-omega)
    drag_torque=-2*math.pi*drag_length*ri**2*traction
    q=ScrewPumpRequest(displacement_m3_rev=.002,speed_rev_s=speed,pressure_rise_pa=pressure,dynamic_viscosity_pa_s=mu,
        leakage_conductance_m3=width*gap**3/(12*length),viscous_torque_geometry_m3=4*math.pi*drag_length*ri**2*ro**2/(ro**2-ri**2),
        calibration_id='manufactured-planar-gap-and-annular-drag',operating_regime_id='Newtonian-Stokes',newtonian_linear_leakage_and_viscous_drag_reviewed=True)
    r=evaluate_screw_pump(q)
    assert r.pressure_leakage_m3_s==pytest.approx(leakage,rel=1e-10)
    assert r.viscous_torque_N_m==pytest.approx(drag_torque,rel=1e-8,abs=1e-12)
    assert r.net_delivery_m3_s==pytest.approx(.002*speed-leakage)
    assert abs(r.power_balance_residual_W)<1e-8


def wall(**changes):
    d=dict(basis_id='manufactured-cylinder',inside_radius_m=.2,wall_thickness_m=.01,conductivity_w_m_k=40.,density_kg_m3=7800.,specific_heat_j_kg_k=500.,initial_temperature_k=300.,supplied_inner_surface_ceiling_k=1200.,stages=(WeldHeatStage(duration_s=2000.,effective_outer_heat_flux_w_m2=2e4,gas_temperature_k=300.,gas_film_w_m2_k=1000.),),radial_cells=12,maximum_step_s=2.,radial_constant_property_heat_flux_and_film_basis_reviewed=True)
    return WeldWallThermalLimitRequest(**(d|changes))

@pytest.mark.parametrize('cells',[6,12,24])
def test_weld_wall_steady_log_profile_and_gas_heat_balance(cells):
    q=wall(radial_cells=cells);r=evaluate_weld_wall_thermal_limit(q);assert r['status']=='computed'
    ro=q.inside_radius_m+q.wall_thickness_m;power=2*math.pi*ro*2e4
    inner=300+power/(2*math.pi*q.inside_radius_m*1000.)
    for radius,temperature in zip(r['radii_m'],r['final_cell_temperature_k']):
        expected=inner+power*math.log(radius/q.inside_radius_m)/(2*math.pi*q.conductivity_w_m_k)
        assert temperature==pytest.approx(expected,abs=1e-7)
    assert abs(r['energy_residual_j_per_m'])/r['applied_heat_j_per_m']<1e-10
    assert not r['burnthrough_or_weld_acceptance_qualified']


def test_weld_wall_high_conductivity_time_refinement_matches_lumped_energy_ode():
    errors=[];ri=.2;ro=.21;capacity=7800*500*math.pi*(ro**2-ri**2);conductance=2*math.pi*ri*1e4;power=2*math.pi*ro*5e4
    expected=300+power/conductance*(1-math.exp(-conductance*10/capacity))
    for dt in (.2,.1,.05):
        q=wall(conductivity_w_m_k=1e7,maximum_step_s=dt,radial_cells=6,stages=(WeldHeatStage(duration_s=10.,effective_outer_heat_flux_w_m2=5e4,gas_temperature_k=300.,gas_film_w_m2_k=1e4),))
        r=evaluate_weld_wall_thermal_limit(q);last=r['stage_end_observations'][-1]['inner_surface_temperature_k'];errors.append(abs(last-expected))
    assert errors[0]>errors[1]>errors[2] and errors[0]/errors[1]>1.7
    assert errors[-1]<.01

@pytest.mark.parametrize('friction',[0.,.1,.2])
def test_nut_factor_from_independent_helical_contact_work_recovers_axial_load(friction):
    cases,context=runpy.run_path('examples/fabrication_methods.py')['make_cases']();base=cases['fabrication.bolt_preload']
    force=25000.;diameter=.02;radius=.009;collar=.014;lead=.002;angle=math.atan(lead/(2*math.pi*radius));normal=force/(math.cos(angle)-friction*math.sin(angle))
    work=quad(lambda theta:force*lead/(2*math.pi)+friction*normal*radius/math.cos(angle)+friction*force*collar,0,2*math.pi)[0]
    torque=work/(2*math.pi)
    factor=(radius*math.tan(angle)+friction*radius/(math.cos(angle)*(math.cos(angle)-friction*math.sin(angle)))+friction*collar)/diameter
    q=base.model_copy(update={'parameters':dict(torque_N_m=torque,nominal_diameter_m=diameter,nut_factor=factor,tensile_area_m2=2e-4)})
    r=evaluate_fabrication(q,context);assert r.status=='computed'
    assert r.values['preload_N']==pytest.approx(force,rel=1e-12)
    assert r.values['nominal_tensile_stress_Pa']*2e-4==pytest.approx(force)

@pytest.mark.parametrize('lo,hi',[(0.,1e-6),(1e-6,1e-5),(1e-4,2e-4)])
def test_slick_volume_bounds_enclose_independent_variable_thickness_surface_integral(lo,hi):
    a=2.;b=3.;mid=(lo+hi)/2;amp=(hi-lo)/2
    actual=quad(lambda x:quad(lambda y:mid+amp*math.sin(2*math.pi*x/a)*math.cos(math.pi*y/b),0,b)[0],0,a)[0]
    q=SlickObservationRequest(appearance='supplied-band',observed_area_m2=a*b,calibrated_bands=(SlickThicknessBand(appearance='supplied-band',minimum_thickness_m=lo,maximum_thickness_m=hi),),calibration_id='manufactured-thickness-bounds',environment_and_visual_method_reviewed=True)
    r=evaluate_slick_observation(q);assert r['status']=='computed'
    assert r['minimum_volume_m3']<=actual<=r['maximum_volume_m3']
    assert actual==pytest.approx(mid*a*b)
