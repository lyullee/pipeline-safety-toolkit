"""A mechanical pseudofluid reference, expressly not a real-mixture critical law.

For a constrained fixed composition with common excluded molecular volume and
arithmetic pair attractions, the van-der-Waals Helmholtz model admits the Kay
coordinates exactly. Composition redistribution and a general phase envelope
are outside this manufactured reference family.
"""
import math
import pytest
from scipy.integrate import quad
from scipy.optimize import root
from pipeline_integrity.gas_pseudocritical import CriticalMixtureComponent as Component, PseudocriticalRequest as Request, estimate_gas_pseudocritical as evaluate

R=8.31446261815324


def pair_inventory(amounts,attractions):
    # Explicit pair-energy inventory, independent of any property mean.
    return sum(ni*nj*(ai+aj)/2 for ni,ai in zip(amounts,attractions) for nj,aj in zip(amounts,attractions))


def critical_root(amounts,attractions,b,tol):
    total=sum(amounts);scale=attractions[0]
    alpha=pair_inventory(amounts,attractions)/(total*total*scale)
    # theta=R*T*b/a0; x=V/(N*b). These are derivatives of -dF/dV,
    # where F=RT sum[n_i ln(n_i/(V-bN))] - sum[n_i n_j a_ij]/V.
    def derivatives(coordinates):
        x,theta=coordinates
        return [-theta/(x-1)**2+2*alpha/x**3,
                2*theta/(x-1)**3-6*alpha/x**4]
    solved=root(derivatives,[2.7,.31*alpha],tol=tol)
    assert solved.success and solved.x[0]>1
    x,theta=solved.x
    assert max(map(abs,derivatives(solved.x)))<max(tol,1e-12)
    return theta*scale/(R*b),(theta/(x-1)-alpha/x**2)*scale/b**2,x


def request(amounts,attractions,b):
    total=sum(amounts)
    # Pure-component critical coordinates follow independently from the
    # single-fluid spinodal-coalescence conditions, not fitted mixture data.
    components=tuple(Component(id=f'component{i}',mole_fraction=n/total,
        critical_temperature_K=8*a/(27*R*b),critical_pressure_Pa_abs=a/(27*b*b))
        for i,(n,a) in enumerate(zip(amounts,attractions)))
    return Request(method='kay_mole_average',components=components,approximation_and_component_basis_reviewed=True)


@pytest.mark.parametrize('amounts,attractions', [((1.,4.),(.2,.7)),((7.,3.),(.3,.9)),((1.,3.,6.),(.15,.5,1.1))])
@pytest.mark.parametrize('b',[2e-5,9e-5])
def test_independent_pair_helmholtz_mechanical_critical_root_and_tolerance_refinement(amounts,attractions,b):
    response=evaluate(request(amounts,attractions,b));assert response.status=='computed'
    roots=[critical_root(amounts,attractions,b,tol) for tol in [1e-5,1e-9,1e-12]]
    for (temperature,pressure,x),tol in zip(roots,[1e-5,1e-9,1e-12]):
        assert temperature==pytest.approx(response.adjusted_pseudo_temperature_K,rel=max(20*tol,2e-12))
        assert pressure==pytest.approx(response.adjusted_pseudo_pressure_Pa_abs,rel=max(20*tol,2e-12))
        assert x==pytest.approx(3.,rel=max(20*tol,2e-12))
    assert roots[1][:2]==pytest.approx(roots[2][:2],rel=2e-11)
    assert not response.actual_mixture_critical_point_established
    assert not response.equation_of_state_or_phase_established


@pytest.mark.parametrize('amounts,attractions',[((1.,4.),(.2,.7)),((1.,3.,6.),(.15,.5,1.1))])
def test_independent_reference_pressure_is_helmholtz_derivative_and_reversible_work(amounts,attractions):
    total=sum(amounts);b=4e-5;pair=pair_inventory(amounts,attractions)
    temperature=1.5*critical_root(amounts,attractions,b,1e-12)[0]
    v1=2*total*b;v2=7*total*b
    def helmholtz(volume):
        return R*temperature*sum(n*math.log(n/(volume-b*total)) for n in amounts)-pair/volume
    pressure=lambda volume:total*R*temperature/(volume-b*total)-pair/volume**2
    work,error=quad(pressure,v1,v2,epsabs=1e-8,epsrel=1e-12)
    assert work==pytest.approx(helmholtz(v1)-helmholtz(v2),rel=1e-12)
    assert error<1e-7
    volume=3.7*total*b;h=volume*1e-5
    gradient=(-helmholtz(volume+2*h)+8*helmholtz(volume+h)-8*helmholtz(volume-h)+helmholtz(volume-2*h))/(12*h)
    assert pressure(volume)==pytest.approx(-gradient,rel=2e-10)


def test_noncommon_excluded_volume_is_not_a_general_true_critical_prediction():
    # A valid different one-fluid closure demonstrates why arbitrary Kay
    # coordinates must not be sold as an actual mixture critical-point solve.
    a=(.2,.8);b=(4e-5,8e-5);fractions=(.5,.5)
    q=Request(method='kay_mole_average',components=tuple(Component(id=str(i),mole_fraction=y,
        critical_temperature_K=8*ai/(27*R*bi),critical_pressure_Pa_abs=ai/(27*bi*bi))
        for i,(y,ai,bi) in enumerate(zip(fractions,a,b))),approximation_and_component_basis_reviewed=True)
    result=evaluate(q)
    am=pair_inventory(fractions,a);bm=sum(y*bi for y,bi in zip(fractions,b))
    constrained_temperature=8*am/(27*R*bm);constrained_pressure=am/(27*bm*bm)
    assert not math.isclose(result.adjusted_pseudo_temperature_K,constrained_temperature,rel_tol=1e-6)
    assert not math.isclose(result.adjusted_pseudo_pressure_Pa_abs,constrained_pressure,rel_tol=1e-6)
    assert not result.actual_mixture_critical_point_established


def test_species_amount_scaling_and_identical_component_splitting_invariance():
    a=evaluate(request((2.,3.),(.2,.7),4e-5))
    scaled=evaluate(request((20.,30.),(.2,.7),4e-5))
    split=evaluate(request((1.,1.,3.),(.2,.2,.7),4e-5))
    for r in [scaled,split]:
        assert r.adjusted_pseudo_temperature_K==pytest.approx(a.adjusted_pseudo_temperature_K)
        assert r.adjusted_pseudo_pressure_Pa_abs==pytest.approx(a.adjusted_pseudo_pressure_Pa_abs)


def test_missing_component_or_reference_review_does_not_select_gravity_fit():
    q=request((2.,3.),(.2,.7),4e-5)
    assert evaluate(q.model_copy(update={'components':()})).status=='needs_data'
    assert evaluate(q.model_copy(update={'approximation_and_component_basis_reviewed':False})).status=='needs_data'


def test_invalid_critical_property_or_non_normalized_composition_rejected():
    q=request((2.,3.),(.2,.7),4e-5)
    for update in [{'critical_temperature_K':0.},{'critical_pressure_Pa_abs':-1.},{'mole_fraction':.9}]:
        part=q.components[0].model_copy(update=update)
        with pytest.raises(ValueError):evaluate(q.model_copy(update={'components':(part,q.components[1])}))
