"""Conditional inventory/feed budget and independent engineering-unit conversion."""
import itertools
import pytest
from pipeline_integrity.offshore_release_ledger import OffshoreReleaseLedgerRequest as Request
from pipeline_integrity.offshore_release_ledger import evaluate_offshore_release_ledger as evaluate


def calc(**changes):
    p=dict(initial_pipe_liquid_volume_m3=10.,maximum_release_fraction=.6,gas_oil_reduction_factor=.8,
        pre_shutin_feed_volume_flow_m3_s=.03,time_until_shutin_s=600.,reference_volume_basis="authored_common_liquid_reference",
        factor_source_id="authored_factors",supplied_factors_and_same_liquid_volume_basis_reviewed=True,
        horizontal_single_full_bore_segment_reviewed=True)
    p.update(changes)
    return evaluate(Request(**p))


@pytest.mark.parametrize("pipe_ft3,rate_bpd,minutes,frel,fgor",[(700.,8000.,5.,.37,.81),(1500.,15000.,12.,.6,.9)])
def test_same_basis_si_aggregation_matches_independent_us_volume_and_time_coordinates(pipe_ft3,rate_bpd,minutes,frel,fgor):
    barrel=42*231*.0254**3
    out=calc(initial_pipe_liquid_volume_m3=pipe_ft3*.3048**3,maximum_release_fraction=frel,
        gas_oil_reduction_factor=fgor,pre_shutin_feed_volume_flow_m3_s=rate_bpd*barrel/86400,
        time_until_shutin_s=minutes*60)
    expected_bbl=pipe_ft3*(.3048**3/barrel)*frel*fgor+rate_bpd*(minutes/1440)
    assert out.estimated_total_release_m3/barrel==pytest.approx(expected_bbl,rel=3e-15)
    assert .17805 <= .3048**3/barrel <= .17815


def test_complete_factor_domain_grid_preserves_initial_plus_feed_volume_budget():
    for frel,fgor in itertools.product((0.,.2,.65,1.),repeat=2):
        out=calc(maximum_release_fraction=frel,gas_oil_reduction_factor=fgor)
        assert out.status=="computed"
        assert out.pre_shutin_feed_release_m3<=out.estimated_total_release_m3<=out.initial_inventory_plus_feed_m3
        assert out.estimated_total_release_m3+out.remaining_initial_inventory_m3==pytest.approx(out.initial_inventory_plus_feed_m3)
        assert out.factor_selection_performed is False


def test_immediate_shutin_has_no_feed_release():
    out=calc(time_until_shutin_s=0.)
    assert out.pre_shutin_feed_release_m3==0
    assert out.estimated_total_release_m3==out.initial_inventory_release_m3


@pytest.mark.parametrize("field",["maximum_release_fraction","gas_oil_reduction_factor","pre_shutin_feed_volume_flow_m3_s","time_until_shutin_s"])
def test_missing_factor_or_feed_input_is_not_assumed(field):
    out=calc(**{field:None})
    assert out.status=="needs_data" and out.estimated_total_release_m3 is None


def test_mismatched_or_unreviewed_volume_basis_is_withheld():
    assert calc(supplied_factors_and_same_liquid_volume_basis_reviewed=False).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(maximum_release_fraction=1.1),dict(gas_oil_reduction_factor=-.1),dict(time_until_shutin_s=-1.)])
def test_invalid_factor_or_time_bounds_are_rejected(changes):
    with pytest.raises(ValueError):calc(**changes)
