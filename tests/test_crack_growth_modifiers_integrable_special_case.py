"""Independently integrated crack-depth special case of supplied rate modifiers."""
import math

import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from pipeline_integrity.crack_growth_modifiers import (
    CrackGrowthModifierRequest, evaluate_crack_growth_modifiers,
)


@pytest.mark.parametrize("threshold,critical,rate_scale,cycles",[(.03,.4,1e-4,1000.),(.0,.5,2e-4,800.),(.06,.3,4e-5,1500.),(.015,.7,3e-4,400.)])
def test_actual_modified_depth_rate_matches_independent_integrated_cycle_coordinate(threshold,critical,rate_scale,cycles):
    a0=.01;z0=math.sqrt(a0);intensity_per_sqrt_depth=1e8
    # Independently separate variables after z=sqrt(a), using the reviewed
    # K(a)=k*sqrt(a), baseline da/dN=c*a and unit modifier exponents.
    # dN = (2/c)*(1-z/zc)/(z-b) dz. The elementary antiderivative
    # below does not evaluate either production multiplier or growth rate.
    def elapsed_cycle_coordinate(z):
        return 2/rate_scale*((1-threshold/critical)*math.log((z-threshold)/(z0-threshold))-(z-z0)/critical)
    expected_z=brentq(lambda z:elapsed_cycle_coordinate(z)-cycles,z0,critical*(1-1e-10),xtol=1e-14)
    def derivative(n,state):
        depth=float(state[0]);z=math.sqrt(depth);k=intensity_per_sqrt_depth
        result=evaluate_crack_growth_modifiers(CrackGrowthModifierRequest(
            baseline_rate_m_cycle=rate_scale*depth,intensity_range_pa_sqrt_m=k*z,
            maximum_intensity_pa_sqrt_m=k*z,threshold_range_pa_sqrt_m=k*threshold,
            critical_intensity_pa_sqrt_m=k*critical,threshold_exponent=1.,critical_exponent=1.,
            calibration_basis_id='authored-integrable-depth-law',baseline_and_modifiers_same_calibration_reviewed=True))
        assert result.status=='computed'
        return [result.modified_rate_m_cycle]
    sol=solve_ivp(derivative,(0.,cycles),[a0],method='DOP853',rtol=2e-12,atol=1e-14,dense_output=True)
    assert sol.success
    assert sol.y[0,-1]==pytest.approx(expected_z**2,rel=1e-10)
    for n in (cycles*.2,cycles*.6,cycles):
        assert elapsed_cycle_coordinate(math.sqrt(float(sol.sol(n)[0])))==pytest.approx(n,rel=2e-10,abs=2e-8)
    assert sol.y[0,-1]<critical**2
