"""Independent branch-flow equilibrium for a conditional meter observation."""
import math

import pytest
from pydantic import ValidationError
from scipy.optimize import brentq

from pipeline_integrity.parallel_meter_runs import ParallelMeterRunsRequest as Request, observe_parallel_meter_run_change as observe


def request(**changes):
    values=dict(initial_run_count=7,remaining_run_count=3,initial_average_meter_differential_pa=7700.,
        common_branch_state_and_coefficient_basis_id="reviewed-identical-meter-state",identical_parallel_runs_reviewed=True,
        unchanged_total_flow_reviewed=True,frozen_quadratic_meter_differential_law_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("before,after",[(3,2),(7,1),(5,4),(1,1)])
@pytest.mark.parametrize("density,area,coefficient,total_flow",[(870.,.0007,.79,.031),(15.,.003,.91,.19)])
def test_independent_frozen_bernoulli_branch_root_and_continuity(before,after,density,area,coefficient,total_flow):
    # Solve each actual branch's Bernoulli volume-flow law at the common
    # differential; this oracle never uses the run-count pressure ratio.
    def branch(dp):return coefficient*area*math.sqrt(2*dp/density)
    def equilibrium(number):
        high=1.
        while math.fsum(branch(high) for _ in range(number))<total_flow:high*=4
        return brentq(lambda dp:math.fsum(branch(dp) for _ in range(number))-total_flow,0.,high,xtol=1e-9,rtol=1e-13)
    dp0,dp1=equilibrium(before),equilibrium(after)
    result=observe(request(initial_run_count=before,remaining_run_count=after,initial_average_meter_differential_pa=dp0))
    assert result.status=="computed"
    assert result.remaining_average_meter_differential_pa==pytest.approx(dp1,rel=2e-12)
    assert math.fsum(branch(dp1) for _ in range(after))==pytest.approx(total_flow,rel=2e-12)
    assert result.initial_total_flow_fraction_per_run==pytest.approx(branch(dp0)/total_flow)
    assert result.remaining_total_flow_fraction_per_run==pytest.approx(branch(dp1)/total_flow)
    assert not result.permanent_loss_or_network_solution_inferred


def test_withdrawal_order_and_unit_scaling():
    direct=observe(request(initial_run_count=9,remaining_run_count=2))
    intermediate=observe(request(initial_run_count=9,remaining_run_count=5))
    staged=observe(request(initial_run_count=5,remaining_run_count=2,initial_average_meter_differential_pa=intermediate.remaining_average_meter_differential_pa))
    assert staged.remaining_average_meter_differential_pa==pytest.approx(direct.remaining_average_meter_differential_pa)
    scaled=observe(request(initial_run_count=9,remaining_run_count=2,initial_average_meter_differential_pa=7700*1000.))
    assert scaled.remaining_average_meter_differential_pa==pytest.approx(1000*direct.remaining_average_meter_differential_pa)


def test_zero_head_identity_and_explicit_measurement_range():
    assert observe(request(initial_average_meter_differential_pa=0.)).remaining_average_meter_differential_pa==0
    identity=observe(request(initial_run_count=3,remaining_run_count=3))
    assert identity.remaining_average_meter_differential_pa==7700.
    r=observe(request(supplied_differential_measurement_limit_pa=10000.))
    assert not r.supplied_differential_limit_satisfied and r.supplied_differential_limit_margin_pa<0
    assert not r.field_or_standard_qualified
    assert observe(request()).supplied_differential_limit_satisfied is None


@pytest.mark.parametrize("field",["initial_run_count","remaining_run_count","initial_average_meter_differential_pa","common_branch_state_and_coefficient_basis_id"])
def test_unknown_required_observation(field):
    assert observe(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["identical_parallel_runs_reviewed","unchanged_total_flow_reviewed","frozen_quadratic_meter_differential_law_reviewed"])
def test_unequal_or_changing_state_not_silently_frozen(field):
    assert observe(request(**{field:False})).status=="needs_data"


@pytest.mark.parametrize("field,value",[("initial_run_count",0),("remaining_run_count",0),("initial_run_count",True),
    ("remaining_run_count",2.5),("initial_average_meter_differential_pa",-1.),("initial_average_meter_differential_pa",float("nan"))])
def test_invalid_counts_or_differential(field,value):
    with pytest.raises(ValidationError):request(**{field:value})


def test_unsupported_count_change_finite_arithmetic_and_replay():
    assert observe(request(initial_run_count=2,remaining_run_count=3)).status=="out_of_scope"
    assert observe(request(initial_average_meter_differential_pa=1e308)).status=="out_of_scope"
    q=request();saved=q.model_dump(mode="python")
    assert observe(Request.model_validate_json(q.model_dump_json()))==observe(q)
    assert q.model_dump(mode="python")==saved
    with pytest.raises(ValidationError):observe(q.model_copy(update={"remaining_run_count":0}))
