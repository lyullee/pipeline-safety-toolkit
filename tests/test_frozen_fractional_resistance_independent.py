"""Selected fractional-exponent geometry reductions against decimal constitutive sums."""
from decimal import Decimal,localcontext
import pytest
from pipeline_integrity.pipe_resistance_reduction import FrozenPipeResistance,PipeResistanceReductionRequest,reduce_pipe_resistances

BRANCHES=((27.,.19,1.7),(43.,.26,2.3),(16.,.21,.9))

def request(topology,n,m):
    return PipeResistanceReductionRequest(branches=tuple(FrozenPipeResistance(length_m=l,diameter_m=d,supplied_loss_scale=k) for l,d,k in BRANCHES),topology=topology,
        potential_basis='absolute_pressure_squared_pa2',flow_basis='reference_volume_m3_s',coefficient_basis_id='reviewed-frozen-fractional-law',flow_exponent=n,diameter_exponent=m,
        reference_loss_scale=1.3,reference_diameter_m=.22 if topology=='series' else None,reference_length_m=31. if topology=='parallel' else None,
        common_frozen_exponents_and_coefficient_units_reviewed=True)

@pytest.mark.parametrize('m',[4.8539,4.735])
@pytest.mark.parametrize('flow',[-.37,.37])
def test_series_common_flow_loss_with_fractional_geometry(m,flow):
    r=reduce_pipe_resistances(request('series',1.8539,m))
    with localcontext() as ctx:
        ctx.prec=70;D=lambda x:Decimal(str(x));n=D(1.8539);mm=D(m);q=D(abs(flow))
        direct=sum(D(k)*D(l)/D(d)**mm*q**n for l,d,k in BRANCHES)
        effective=D(1.3)*D(r.equivalent_length_m)/D(.22)**mm*q**n
        assert float(effective/direct)==pytest.approx(1.,rel=8e-15)

@pytest.mark.parametrize('flow',[-.61,.61])
def test_parallel_noninteger_continuity_and_each_branch_potential(flow):
    r=reduce_pipe_resistances(request('parallel',1.8539,4.8539))
    with localcontext() as ctx:
        ctx.prec=70;D=lambda x:Decimal(str(x));n=D(1.8539);m=D(4.8539)
        resistance=[D(k)*D(l)/D(d)**m for l,d,k in BRANCHES]
        def volume_at_potential(p):return sum((p/a)**(1/n) for a in resistance)
        lo=Decimal(0);hi=max(resistance)*D(abs(flow))**n
        for _ in range(260):
            mid=(lo+hi)/2
            if volume_at_potential(mid)<D(abs(flow)):lo=mid
            else:hi=mid
        potential=(lo+hi)/2
        for fraction,a in zip(r.signed_total_flow_fractions,resistance):
            assert float(a*(D(fraction)*D(abs(flow)))**n/potential)==pytest.approx(1.,rel=1e-14)
        equivalent=D(1.3)*D(31)/D(r.equivalent_diameter_m)**m*D(abs(flow))**n
        assert float(equivalent/potential)==pytest.approx(1.,rel=1e-14)
        assert sum(r.signed_total_flow_fractions)==pytest.approx(1.,abs=5e-16)
