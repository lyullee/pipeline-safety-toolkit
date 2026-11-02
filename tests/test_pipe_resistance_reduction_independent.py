"""Independent common-flow and common-potential geometry reductions."""
import numpy as np
import pytest
from scipy.optimize import root
from pipeline_integrity.pipe_resistance_reduction import FrozenPipeResistance,PipeResistanceReductionRequest,reduce_pipe_resistances


def request(topology,branches,n=2.,m=5.):
    return PipeResistanceReductionRequest(topology=topology,branches=tuple(FrozenPipeResistance(length_m=l,diameter_m=d,supplied_loss_scale=k) for l,d,k in branches),
        potential_basis='absolute_pressure_squared_pa2',flow_basis='reference_volume_m3_s',coefficient_basis_id='authored-frozen-law',flow_exponent=n,diameter_exponent=m,reference_loss_scale=2.,
        reference_diameter_m=.15 if topology=='series' else None,reference_length_m=9. if topology=='parallel' else None,
        common_frozen_exponents_and_coefficient_units_reviewed=True)


@pytest.mark.parametrize('branches',[[(3.,.1,2.),(5.,.15,2.)],[(4.,.15,2.),(2.,.15,2.)],[(3.,.11,1.),(2.,.17,4.),(1.,.2,3.)]])
def test_series_equivalent_reconstructs_independent_potential_loss(branches):
    q=request('series',branches);r=reduce_pipe_resistances(q)
    for flow in (-.3,.3):
        total=sum(k*l/d**5*flow*abs(flow) for l,d,k in branches)
        equivalent=2*r.equivalent_length_m/.15**5*flow*abs(flow)
        assert equivalent==pytest.approx(total,rel=3e-15)
    if all(d==.15 and k==2. for l,d,k in branches):assert r.equivalent_length_m==pytest.approx(sum(l for l,d,k in branches))


@pytest.mark.parametrize('n,m',[(2.,5.),(1.,4.)])
@pytest.mark.parametrize('total_flow',[-.7,.7])
def test_parallel_fraction_and_equivalent_geometry_match_independent_nonlinear_continuity_root(n,m,total_flow):
    branches=[(3.,.1,2.),(5.,.15,2.),(2.,.12,3.)];q=request('parallel',branches,n,m);r=reduce_pipe_resistances(q)
    resist=np.array([k*l/d**m for l,d,k in branches]);scale=max(resist)
    def equations(flows):
        loss=resist*np.sign(flows)*abs(flows)**n
        return [(loss[0]-loss[1])/scale,(loss[1]-loss[2])/scale,sum(flows)-total_flow]
    solved=root(equations,np.full(3,total_flow/3),tol=1e-11)
    assert solved.success and np.max(abs(np.array(equations(solved.x))))<1e-12
    observed=np.array(r.signed_total_flow_fractions)*total_flow
    np.testing.assert_allclose(observed,solved.x,rtol=4e-13,atol=1e-14)
    equivalent=2*9./r.equivalent_diameter_m**m*np.sign(total_flow)*abs(total_flow)**n
    np.testing.assert_allclose(resist*np.sign(observed)*abs(observed)**n,equivalent,rtol=4e-15)
    assert sum(r.signed_total_flow_fractions)==pytest.approx(1.,abs=5e-16)


@pytest.mark.parametrize('failure',['review','unknown','reference'])
def test_frozen_law_review_and_reference_contracts(failure):
    q=request('series',[(3.,.1,2.)])
    if failure=='review':assert reduce_pipe_resistances(q.model_copy(update={'common_frozen_exponents_and_coefficient_units_reviewed':False})).status=='needs_data'
    else:
        with pytest.raises(ValueError):
            PipeResistanceReductionRequest.model_validate(dict(q.model_dump(),**({'silently_frozen_temperature':300.} if failure=='unknown' else {'reference_diameter_m':None})))
