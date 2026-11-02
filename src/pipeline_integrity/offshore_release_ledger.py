"""Conditional offshore inventory/feed release ledger with caller-supplied factors."""
from __future__ import annotations

import math
from typing import Literal
from .contracts import Contract,Fraction,Name,Nonnegative


class OffshoreReleaseLedgerRequest(Contract):
    initial_pipe_liquid_volume_m3: Nonnegative
    maximum_release_fraction: Fraction | None = None
    gas_oil_reduction_factor: Fraction | None = None
    pre_shutin_feed_volume_flow_m3_s: Nonnegative | None = None
    time_until_shutin_s: Nonnegative | None = None
    reference_volume_basis: Name
    factor_source_id: Name | None = None
    supplied_factors_and_same_liquid_volume_basis_reviewed: bool = False
    horizontal_single_full_bore_segment_reviewed: bool = False


class OffshoreReleaseLedgerResult(Contract):
    status: Literal["computed","needs_data"]
    reference_volume_basis: Name
    initial_inventory_release_m3: Nonnegative | None = None
    remaining_initial_inventory_m3: Nonnegative | None = None
    pre_shutin_feed_release_m3: Nonnegative | None = None
    estimated_total_release_m3: Nonnegative | None = None
    initial_inventory_plus_feed_m3: Nonnegative | None = None
    factor_selection_performed: Literal[False] = False
    issues: tuple[str,...] = ()


def evaluate_offshore_release_ledger(request: OffshoreReleaseLedgerRequest) -> OffshoreReleaseLedgerResult:
    """Aggregate supplied inventory-release factors and constant feed until shut-in.

    All liquid volumes/rates use one explicitly reviewed reference basis.
    The factors must come from caller data or a separately reviewed provider;
    no pressure/GOR factor tables, hydrostatic reference convention or field
    calibration is supplied. This is the aggregation stage only.
    """
    if not isinstance(request,OffshoreReleaseLedgerRequest):raise TypeError("OffshoreReleaseLedgerRequest required")
    q=OffshoreReleaseLedgerRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    for name in ("maximum_release_fraction","gas_oil_reduction_factor","pre_shutin_feed_volume_flow_m3_s","time_until_shutin_s"):
        if getattr(q,name) is None:issues.append("missing "+name)
    if not q.supplied_factors_and_same_liquid_volume_basis_reviewed:
        issues.append("supplied factors and same liquid-volume basis require review")
    if not q.horizontal_single_full_bore_segment_reviewed:
        issues.append("single horizontal full-bore segment approximation requires review")
    if issues:return OffshoreReleaseLedgerResult(status="needs_data",reference_volume_basis=q.reference_volume_basis,issues=tuple(issues))
    inventory=q.initial_pipe_liquid_volume_m3*q.maximum_release_fraction*q.gas_oil_reduction_factor
    remaining=q.initial_pipe_liquid_volume_m3-inventory
    feed=q.pre_shutin_feed_volume_flow_m3_s*q.time_until_shutin_s
    total=inventory+feed;available=q.initial_pipe_liquid_volume_m3+feed
    if not all(math.isfinite(x) for x in (inventory,remaining,feed,total,available)):
        raise ValueError("release volume ledger exceeds finite numerical support")
    return OffshoreReleaseLedgerResult(status="computed",reference_volume_basis=q.reference_volume_basis,
        initial_inventory_release_m3=inventory,remaining_initial_inventory_m3=remaining,
        pre_shutin_feed_release_m3=feed,estimated_total_release_m3=total,initial_inventory_plus_feed_m3=available)
