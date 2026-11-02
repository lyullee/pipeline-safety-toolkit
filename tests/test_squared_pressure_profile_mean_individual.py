"""Independent pressure-profile quadrature and its homogeneous/symmetry limits."""
import math

import pytest
from scipy.integrate import quad

from pipeline_integrity.gas_profile_average import SquaredPressureProfileMeanRequest as Request
from pipeline_integrity.gas_profile_average import evaluate_squared_pressure_profile_mean as evaluate


def calc(a,b):
    return evaluate(Request(first_pressure_pa_abs=a,second_pressure_pa_abs=b,
        squared_pressure_linear_in_distance_reviewed=True)).mean_pressure_pa_abs


@pytest.mark.parametrize("a,b",[(1e5,1e5),(8e6,5e6),(5e6,8e6),(1e8,1e2),(1e5*(1+1e-8),1e5*(1-1e-8))])
def test_mean_matches_independent_linear_squared_pressure_profile_quadrature(a,b):
    scale=max(a,b)
    integral=quad(lambda x:math.hypot(math.sqrt(1-x)*a/scale,math.sqrt(x)*b/scale),
                  0.,1.,epsabs=1e-13,epsrel=1e-12)[0]*scale
    assert calc(a,b)==pytest.approx(integral,rel=3e-12)
    assert min(a,b)<=calc(a,b)<=max(a,b)


def test_profile_mean_is_symmetric_and_homogeneous_in_absolute_pressure():
    a,b=7e6,2e6
    assert calc(a,b)==calc(b,a)
    assert calc(a*3.7,b*3.7)==pytest.approx(calc(a,b)*3.7,rel=3e-15)


def test_uniform_profile_returns_exact_endpoint_across_wide_finite_scales():
    for pressure in (1e-200,1e5,1e300):assert calc(pressure,pressure)==pressure


def test_missing_endpoint_or_unreviewed_profile_does_not_assume_an_average():
    assert evaluate(Request(first_pressure_pa_abs=1e5,squared_pressure_linear_in_distance_reviewed=True)).status=="needs_data"
    assert evaluate(Request(first_pressure_pa_abs=1e5,second_pressure_pa_abs=2e5)).mean_pressure_pa_abs is None
