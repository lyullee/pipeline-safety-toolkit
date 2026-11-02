"""Fixed-surface layer outputs against an independent radial heat equation."""
import math

import numpy as np
import pytest
from scipy.integrate import solve_bvp

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.radial_temperature_profile import RadialTemperatureProfileRequest as Request
from pipeline_integrity.radial_temperature_profile import evaluate_radial_temperature_profile as evaluate


def make(a=.03,b=.06,k=.2,length=1.2,inner=500.,outer=300.,points=11,**changes):
    context=RunContext(asset_id='radial_wall',scenario_id='fixed_surface_heat',clock_id='steady',origin='synthetic',
        evidence=(EvidenceRef(id='wall',citation='Authored radial wall and fixed-surface temperatures',origin='synthetic'),))
    values=dict(asset_id=context.asset_id,location_id='wall',clock_id=context.clock_id,time_s=0.,
        evaluation_radii_m=tuple(np.linspace(a,b,points)),conductivity_w_m_k=k,length_m=length,
        inner_surface_temperature_k=inner,outer_surface_temperature_k=outer,
        geometry_and_thermal_evidence_id='wall',steady_homogeneous_radial_conduction_reviewed=True)
    values.update(changes)
    return Request(**values),context


@pytest.mark.parametrize('a,b,k,length,inner,outer',[(.03,.06,.2,1.2,500.,300.),
    (.05,.055,45.,2.3,600.,310.),(.02,.1,12.,.8,280.,330.)])
def test_profile_and_heat_match_independent_radial_equilibrium(a,b,k,length,inner,outer):
    q,c=make(a,b,k,length,inner,outer)
    result=evaluate(q,c)
    assert result.status=='computed',result.issues
    coordinate=np.linspace(1.,b/a,101)
    def ode(x,y):return np.vstack((y[1],-y[1]/x))
    def boundary(left,right):return np.array([left[0]-1.,right[0]])
    initial=np.vstack((np.linspace(1.,0.,len(coordinate)),-np.ones(len(coordinate))/(b/a-1)))
    reference=solve_bvp(ode,boundary,coordinate,initial,tol=1e-9,max_nodes=5000)
    assert reference.success,reference.message
    theta=reference.sol(np.array(q.evaluation_radii_m)/a)
    expected=outer+(inner-outer)*theta[0]
    heat=-2*math.pi*k*length*(inner-outer)*reference.sol(1.)[1]
    assert result.temperatures_k==pytest.approx(expected,rel=2e-9,abs=2e-6)
    assert result.heat_rate_outward_w==pytest.approx(heat,rel=3e-8,abs=1e-6)
    assert result.maximum_surface_error_k<=q.surface_temperature_tolerance_k
    assert result.physical_boundary_films_inferred is False


def test_interface_subdivision_changes_sampling_without_changing_heat():
    q,c=make(points=11);coarse=evaluate(q,c)
    q,c=make(points=21);fine=evaluate(q,c)
    assert coarse.status==fine.status=='computed'
    assert fine.temperatures_k[::2]==pytest.approx(coarse.temperatures_k,rel=2e-12,abs=2e-8)
    assert fine.heat_rate_outward_w==pytest.approx(coarse.heat_rate_outward_w,rel=2e-12)


def test_temperature_reversal_reverses_heat_without_clipping():
    q,c=make(inner=500.,outer=300.);hot=evaluate(q,c)
    q,c=make(inner=300.,outer=500.);cold=evaluate(q,c)
    assert hot.status==cold.status=='computed'
    assert cold.heat_rate_outward_w==pytest.approx(-hot.heat_rate_outward_w,rel=2e-12)
    assert cold.temperatures_k==pytest.approx([800.-v for v in hot.temperatures_k],abs=2e-8)


def test_uniform_surface_temperature_has_no_conduction_rate():
    q,c=make(inner=410.,outer=410.)
    result=evaluate(q,c)
    assert result.status=='computed'
    assert result.heat_rate_outward_w==0
    assert result.temperatures_k==tuple([410.]*len(q.evaluation_radii_m))


def test_unknown_conductivity_does_not_become_an_isothermal_wall():
    q,c=make(conductivity_w_m_k=None)
    result=evaluate(q,c)
    assert result.status=='needs_data'
    assert result.temperatures_k is None


def test_virtual_boundary_conversion_limit_withholds_unresolved_profile():
    q,c=make(k=45.,initial_virtual_boundary_transfer_w_m2_k=1.,maximum_virtual_boundary_transfer_w_m2_k=1.)
    result=evaluate(q,c)
    assert result.status=='solver_withheld'
    assert result.temperatures_k is None


def test_profile_clock_mismatch_is_not_relabelled():
    q,c=make();q=q.model_copy(update={'clock_id':'other_snapshot'})
    with pytest.raises(ValueError):evaluate(q,c)
