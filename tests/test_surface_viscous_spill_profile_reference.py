"""Independent thin-film gravity-current profile and Darcy-drainage references.

The production radius is a rounded similarity reduction. The oracle solves
the dimensionless nonlinear free-front profile and its source mass flux,
instead of substituting a radius coefficient from the implementation.
"""
from functools import lru_cache
import math
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.porous_release import SurfaceViscousSpillRequest as R,evaluate_surface_viscous_spill as evaluate


@lru_cache(maxsize=8)
def similarity_profile(front_cutoff):
    # Lubrication momentum gives depth-integrated flux -g*h^3*grad(h)/(3nu).
    # For constant axisymmetric feed, h has constant similarity scale and
    # r is proportional to sqrt(t). Write W=F^3 and G=x*F^3*F'.
    # The free-front local solution is W~3(1-x)/2, G~-x*W^(1/3)/2.
    x0=1-front_cutoff;w0=1.5*front_cutoff;g0=-.5*x0*w0**(1/3)
    def rhs(x,y):
        w,g=y
        return [3*g/(x*w**(1/3)),-x*g/(2*w)]
    origin_cutoff=1e-7
    solution=solve_ivp(rhs,(x0,origin_cutoff),[w0,g0],method='DOP853',dense_output=True,rtol=1e-10,atol=1e-12,max_step=.015)
    assert solution.success
    integral=quad(lambda x:x*solution.sol(x)[0]**(1/3),origin_cutoff,x0,epsabs=1e-10)[0]
    # Analytical leading front tail; the omitted origin contributes O(x^2)
    # times a weak logarithmic height and is refined separately below.
    integral+=.75*1.5**(1/3)*front_cutoff**(4/3)
    front=(1/(2*math.pi*integral))**(3/8)
    point_feed=-2*math.pi*front**(8/3)*solution.y[1,-1]
    return front,point_feed,solution,integral


def test_profile_cutoff_convergence_with_preserved_coarse_solutions():
    fronts=[similarity_profile(e)[0] for e in [1e-3,1e-4,1e-5,1e-6]]
    changes=[abs(b-a) for a,b in zip(fronts,fronts[1:])]
    assert all(b < .08*a for a,b in zip(changes,changes[1:]))
    assert changes[-1] < 1e-7
    # Published three-decimal coefficient precision, declared before the run;
    # this is not a tightened claim of exact identity with a rounded constant.
    assert .7145 <= fronts[-1] <= .7155


def test_independent_profile_source_flux_volume_and_free_front_residuals():
    front,feed,solution,I=similarity_profile(1e-6)
    assert feed==pytest.approx(1.,abs=2e-8)
    assert 2*math.pi*front**(8/3)*I==pytest.approx(1.,rel=2e-15)
    for x in [.15,.4,.75]:
        h=1e-5;wm,gm=solution.sol(x-h);wp,gp=solution.sol(x+h);w,g=solution.sol(x)
        fprime=(wp**(1/3)-wm**(1/3))/(2*h)
        pressure_flux_divergence=(gp-gm)/(2*h)/x
        time_derivative=-x*fprime/2
        assert pressure_flux_divergence==pytest.approx(time_derivative,rel=2e-7)
    assert solution.sol(1-1e-6)[0] > 0


@pytest.mark.parametrize('rate,nu,g',[(1e-6,2e-4,9.80665),(2e-6,2e-4,9.80665),(1e-6,.002,9.80665),(2e-6,.002,3.71)])
def test_radius_against_independent_nonlinear_profile_in_actual_si_states(rate,nu,g):
    times=(0.,100.,400.,1600.)
    out=evaluate(R(feed_m3_s=rate,kinematic_viscosity_m2_s=nu,gravity_m_s2=g,times_s=times,flat_axisymmetric_viscous_regime_reviewed=True))
    assert out.status=='computed';assert out.radius_m[0]==0
    front=similarity_profile(1e-6)[0];K=g/(3*nu)
    for t,radius,volume in zip(times[1:],out.radius_m[1:],out.feed_volume_m3[1:]):
        independent_radius=front*(K*rate**3)**(1/8)*math.sqrt(t)
        # Radius-coordinate uncertainty due to the printed 3-decimal front
        # coefficient, not measured site/model uncertainty.
        assert abs(radius-independent_radius) <= .0005*(K*rate**3)**(1/8)*math.sqrt(t)
        height_scale=(rate/K)**.25
        profile_volume=2*math.pi*(K*rate**3)**.25*t*height_scale*front**(8/3)*similarity_profile(1e-6)[3]
        assert profile_volume==pytest.approx(rate*t,rel=2e-14)
        assert volume==pytest.approx(rate*t)


@pytest.mark.parametrize('permeability',[1e-11,1e-10,1e-9])
def test_darcy_steady_draining_radius_and_transition_rounding_bound(permeability):
    rate=2e-6;nu=.001;g=9.80665
    # Hydrostatic vertical head gradient1 drives saturated Darcy drainage.
    drainage_velocity=permeability*g/nu
    steady_area=rate/drainage_velocity;steady_radius=math.sqrt(steady_area/math.pi)
    front=similarity_profile(1e-6)[0];scale=(g*rate**3/(3*nu))**(1/8)
    transition_reference=(steady_radius/(front*scale))**2
    dimensional_transition_scale=(6/math.pi*rate*nu**5/(g**5*permeability**4))**.25
    assert .6965*dimensional_transition_scale <= transition_reference <= .6975*dimensional_transition_scale
    out=evaluate(R(feed_m3_s=rate,kinematic_viscosity_m2_s=nu,soil_permeability_m2=permeability,gravity_m_s2=g,times_s=(0.,transition_reference/2,2*transition_reference,4*transition_reference),flat_axisymmetric_viscous_regime_reviewed=True))
    assert out.status=='computed'
    # Propagate both printed coefficient precision intervals to the steady
    # mass-balance ratio; do not assert an exact equality of rounded constants.
    cap_ratio=math.pi*out.maximum_radius_m**2*drainage_velocity/rate
    lower=(.7145/.715)**2*(.6965/.697)
    upper=(.7155/.715)**2*(.6975/.697)
    assert lower <= cap_ratio <= upper
    assert lower <= (out.maximum_radius_m/steady_radius)**2 <= upper
    assert out.radius_m[-1]==out.radius_m[-2]==out.maximum_radius_m
    assert out.feed_volume_m3[-1] > out.feed_volume_m3[-2]
    assert out.post_transition_storage_partition_resolved is False


def test_no_invented_permeability_evaporation_or_field_partition():
    q=R(feed_m3_s=1e-6,kinematic_viscosity_m2_s=.001,times_s=(0.,10.),flat_axisymmetric_viscous_regime_reviewed=True)
    r=evaluate(q);assert r.drainage_transition_s is None;assert r.maximum_radius_m is None;assert r.evaporation_included is False
    assert evaluate(q.model_copy(update={'flat_axisymmetric_viscous_regime_reviewed':False})).status=='out_of_scope'


@pytest.mark.parametrize('update',[{'feed_m3_s':0},{'kinematic_viscosity_m2_s':0},{'soil_permeability_m2':-1},{'times_s':(-1,)}])
def test_invalid_physical_inputs_are_not_clipped(update):
    with pytest.raises(ValueError):R.model_validate({'feed_m3_s':1e-6,'kinematic_viscosity_m2_s':.001,'times_s':(0.,10.),**update})
