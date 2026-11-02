"""Independent resistor ladder, Kirchhoff BVP, current and Joule heat tests."""
import math
import numpy as np
import pytest
from scipy.integrate import quad,solve_bvp,solve_ivp
from pipeline_integrity.distributed_pipe_cp import (
    CPLineBoundary as B,UniformCPLineRequest as Request,evaluate_uniform_cp_line as solve)


def request(**changes):
    args=dict(mode='finite',longitudinal_resistance_ohm_m=.02,transverse_conductance_S_m=.003,
        length_m=100.,left=B(potential_displacement_V=-1.2),right=B(inward_current_A=0.),
        sample_positions_m=tuple(np.linspace(0,100,11)),asset_id='pipe',coordinate_frame_id='positive-chainage',
        potential_reference_id='uniform-zero-drain-baseline',coefficient_basis_id='authored-r-g',
        uniform_passive_ohmic_pipe_and_shunt_reference_reviewed=True)
    args.update(changes);return Request(**args)


def ladder(n,r=.02,g=.003,length=100.,voltage=-1.2):
    # Independent finite-volume network: every physical link is r*dx and each
    # node leaks to a common reference through a g*dx shunt (half at endpoints).
    dx=length/n;conduct=1/(r*dx);A=np.zeros((n,n));rhs=np.zeros(n)
    for j in range(1,n+1):
        index=j-1;A[index,index]+=g*dx*(.5 if j==n else 1.)
        A[index,index]+=conduct
        if j==1:rhs[index]+=conduct*voltage
        else:A[index,index-1]-=conduct
        if j<n:A[index,index]+=conduct;A[index,index+1]-=conduct
    v=np.r_[voltage,np.linalg.solve(A,rhs)]
    drain=(v[0]-v[1])*conduct+g*dx*.5*v[0]
    shunt=sum(v[j]*g*dx*(.5 if j in (0,n) else 1.) for j in range(n+1))
    joule=sum((v[j]-v[j+1])**2*conduct for j in range(n))
    joule+=sum(v[j]**2*g*dx*(.5 if j in (0,n) else 1.) for j in range(n+1))
    return v,drain,shunt,joule


def test_resistor_ladder_refinement_converges_to_potential_current_and_power():
    errors=[]
    for n in (20,40,80):
        q=request(sample_positions_m=tuple(np.linspace(0,100,n+1)));a=solve(q)
        v,drain,leak,power=ladder(n)
        assert a.status=='computed'
        exact=np.array([p.potential_displacement_V for p in a.points])
        errors.append(max(abs(v-exact)))
        assert drain==pytest.approx(leak,abs=3e-13)
        assert -1.2*drain==pytest.approx(power,rel=3e-12)
        if n==80:
            assert drain==pytest.approx(a.left_inward_current_A,rel=2e-5)
            assert power==pytest.approx(a.boundary_power_into_line_W,rel=2e-5)
    assert errors[1]<errors[0]/3.9 and errors[2]<errors[1]/3.9


@pytest.mark.parametrize('left,right',[(B(potential_displacement_V=1.4),B(potential_displacement_V=-.2)),
    (B(potential_displacement_V=-1.2),B(inward_current_A=.03)),
    (B(inward_current_A=.08),B(potential_displacement_V=-.4)),
    (B(inward_current_A=.08),B(inward_current_A=-.03))])
def test_actual_endpoint_combinations_against_independent_Kirchhoff_BVP(left,right):
    q=request(left=left,right=right);a=solve(q);assert a.status=='computed'
    def rhs(x,y):return np.array([-.02*y[1],-.003*y[0]])
    def bc(ya,yb):
        l=ya[0]-left.potential_displacement_V if left.potential_displacement_V is not None else ya[1]-left.inward_current_A
        rr=yb[0]-right.potential_displacement_V if right.potential_displacement_V is not None else -yb[1]-right.inward_current_A
        return np.array([l,rr])
    x=np.linspace(0,100,9);reference=solve_bvp(rhs,bc,x,np.zeros((2,len(x))),tol=1e-10,max_nodes=3000)
    assert reference.success
    for p in a.points:
        v,i=reference.sol(p.position_m)
        assert p.potential_displacement_V==pytest.approx(v,rel=2e-9,abs=1e-10)
        assert p.positive_x_axial_current_A==pytest.approx(i,rel=2e-9,abs=1e-10)
    assert abs(a.charge_balance_residual_A)<1e-12
    assert abs(a.power_balance_residual_W)<1e-12


@pytest.mark.parametrize('terminal',[-.3,.5,0.])
def test_insulated_end_target_inverse_against_independent_reverse_state_ODE(terminal):
    q=request(mode='insulated_end_target',left=None,right=None,insulated_end_potential_displacement_V=terminal)
    a=solve(q);assert a.status=='computed'
    ode=solve_ivp(lambda x,y:[-.02*y[1],-.003*y[0]],(100,0),[terminal,0.],rtol=1e-12,atol=1e-13,method='DOP853')
    assert ode.success
    assert a.left_potential_displacement_V==pytest.approx(ode.y[0,-1],rel=2e-11,abs=1e-12)
    assert a.left_inward_current_A==pytest.approx(ode.y[1,-1],rel=2e-11,abs=1e-12)
    assert a.points[-1].potential_displacement_V==pytest.approx(terminal,abs=1e-14)
    assert abs(a.points[-1].positive_x_axial_current_A)<1e-14


def test_local_Kirchhoff_derivatives_and_independent_integrated_current_and_heat():
    q=request(left=B(potential_displacement_V=1.2),right=B(inward_current_A=.02));a=solve(q)
    def values(x):return solve(q.model_copy(update={'sample_positions_m':(x,)})).points[0]
    for x in (10.,50.,90.):
        h=.001;p,m=values(x+h),values(x-h);center=values(x)
        assert (p.potential_displacement_V-m.potential_displacement_V)/(2*h)==pytest.approx(-.02*center.positive_x_axial_current_A,rel=1e-8)
        assert (p.positive_x_axial_current_A-m.positive_x_axial_current_A)/(2*h)==pytest.approx(-.003*center.potential_displacement_V,rel=1e-8)
    # Independently integrate the resistor ladder's nodal limiting circuit work.
    assert a.boundary_power_into_line_W==pytest.approx(a.longitudinal_joule_power_W+a.shunt_joule_power_W,rel=2e-12)
    assert a.left_inward_current_A+a.right_inward_current_A==pytest.approx(a.outward_shunt_current_integral_A,rel=2e-12)


def semi(**changes):
    return request(mode='semi_infinite_arm',length_m=None,right=None,left=B(potential_displacement_V=-1.2),
        sample_positions_m=(0.,20.,100.),**changes)


def test_semi_infinite_charge_and_resistor_energy_from_independent_integrals():
    a=solve(semi());assert a.status=='computed';k=a.attenuation_per_m
    # Integrals use independent coordinate-scaled profiles from the physical
    # one-arm Kirchhoff characteristic, not the returned balance quantities.
    y=math.sqrt(.003/.02)
    leak=quad(lambda u:.003*(-1.2)*math.exp(-u)/k,0,np.inf)[0]
    longitudinal=quad(lambda u:.02*(y*-1.2*math.exp(-u))**2/k,0,np.inf)[0]
    shunt=quad(lambda u:.003*(-1.2*math.exp(-u))**2/k,0,np.inf)[0]
    assert a.left_inward_current_A==pytest.approx(leak,rel=2e-12)
    assert a.boundary_power_into_line_W==pytest.approx(longitudinal+shunt,rel=2e-12)


def test_semi_infinite_target_distance_against_independent_ODE_event_and_two_arm_sum():
    q=semi(semi_infinite_target_magnitude_V=.3,symmetric_two_identical_arms_reviewed=True);a=solve(q)
    def event(x,y):return y[0]+.3
    event.terminal=True;event.direction=1
    ref=solve_ivp(lambda x,y:[-.02*y[1],-.003*y[0]],(0,1000),[-1.2,a.left_inward_current_A],
        events=event,rtol=1e-12,atol=1e-13,max_step=2.)
    assert ref.success and len(ref.t_events[0])==1
    assert a.semi_infinite_target_reach_m==pytest.approx(ref.t_events[0][0],rel=2e-10)
    assert a.symmetric_two_arm_total_drain_current_A==2*a.left_inward_current_A
    assert solve(semi(semi_infinite_target_magnitude_V=2.)).semi_infinite_target_status=='not_attained'


def test_semi_infinite_current_boundary_and_sign_reversal():
    a=solve(semi());b=solve(semi().model_copy(update={'left':B(inward_current_A=-a.left_inward_current_A)}))
    assert b.left_potential_displacement_V==pytest.approx(-a.left_potential_displacement_V)
    for p,v in zip(a.points,b.points):
        assert v.potential_displacement_V==pytest.approx(-p.potential_displacement_V)
        assert v.longitudinal_joule_power_W_m==pytest.approx(p.longitudinal_joule_power_W_m)


@pytest.mark.parametrize('left,right',[(B(potential_displacement_V=2.),B(potential_displacement_V=.5)),
    (B(potential_displacement_V=2.),B(inward_current_A=-.03)),
    (B(inward_current_A=.03),B(potential_displacement_V=.5))])
def test_perfect_coating_is_exact_series_resistor_with_no_ground_current(left,right):
    a=solve(request(transverse_conductance_S_m=0.,left=left,right=right));assert a.status=='computed'
    assert a.outward_shunt_current_integral_A==0 and a.shunt_joule_power_W==0
    assert a.left_potential_displacement_V-a.right_potential_displacement_V==pytest.approx(2*a.left_inward_current_A)
    assert a.longitudinal_joule_power_W==pytest.approx(2*a.left_inward_current_A**2)


def test_zero_leakage_pure_current_boundaries_do_not_invent_a_potential():
    assert solve(request(transverse_conductance_S_m=0,left=B(inward_current_A=1),right=B(inward_current_A=-1))).status=='non_identifiable'
    assert solve(request(transverse_conductance_S_m=0,left=B(inward_current_A=1),right=B(inward_current_A=0))).status=='inconsistent_boundaries'


def test_leakage_to_zero_limit_and_long_line_to_semi_infinite_limit():
    zero=solve(request(transverse_conductance_S_m=0.))
    errors=[abs(solve(request(transverse_conductance_S_m=g)).left_inward_current_A-zero.left_inward_current_A) for g in (1e-5,1e-6,1e-7)]
    assert errors[1]<errors[0]/9.9 and errors[2]<errors[1]/9.9
    infinite=solve(semi());long=solve(request(length_m=1000.,sample_positions_m=(0.,20.,100.)))
    for p,q in zip(infinite.points,long.points):
        assert p.potential_displacement_V==pytest.approx(q.potential_displacement_V,rel=2e-6)


def test_polarity_and_coordinate_length_coefficient_similarity():
    a=solve(request());b=solve(request(left=B(potential_displacement_V=2.4)))
    c=solve(request(length_m=1000.,longitudinal_resistance_ohm_m=.002,transverse_conductance_S_m=.0003,sample_positions_m=tuple(np.linspace(0,1000,11))))
    for p,q,s in zip(a.points,b.points,c.points):
        assert q.potential_displacement_V==pytest.approx(-2*p.potential_displacement_V)
        assert s.potential_displacement_V==pytest.approx(p.potential_displacement_V)
    assert b.boundary_power_into_line_W==pytest.approx(4*a.boundary_power_into_line_W)
    assert c.boundary_power_into_line_W==pytest.approx(a.boundary_power_into_line_W)


@pytest.mark.parametrize('changes',[{'longitudinal_resistance_ohm_m':None},{'transverse_conductance_S_m':None},
    {'left':None},{'length_m':None},{'uniform_passive_ohmic_pipe_and_shunt_reference_reviewed':False}])
def test_missing_coefficients_boundary_and_reference_review_withheld(changes):
    a=solve(request(**changes));assert a.status=='needs_data' and not a.points


def test_declared_numerical_decay_support_and_semi_infinite_no_leak_are_not_extrapolated():
    assert solve(request(length_m=1e8)).status=='out_of_scope'
    assert solve(semi(transverse_conductance_S_m=0.)).status=='out_of_scope'
    assert solve(semi().model_copy(update={'sample_positions_m':(1e8,)})).status=='out_of_scope'


@pytest.mark.parametrize('changes',[{'longitudinal_resistance_ohm_m':0},{'transverse_conductance_S_m':-.1},
    {'sample_positions_m':(101.,)},{'symmetric_two_identical_arms_reviewed':True}])
def test_invalid_geometry_or_ambiguous_topology_rejected(changes):
    with pytest.raises(ValueError):request(**changes)
