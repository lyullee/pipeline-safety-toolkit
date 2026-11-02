"""Quasistatic reeving force balance with caller supplied per-sheave efficiency."""
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative

class ReevedHoistRequest(Contract):
    supported_load_N: Nonnegative | None
    working_rope_parts: int = Field(gt=0,le=1000000)
    per_sheave_efficiency: Positive | None = Field(le=1)
    load_rise_m: Nonnegative | None = None
    successive_loaded_leg_loss_and_direct_hoist_route_reviewed: bool = False

class ReevedHoistResult(Contract):
    status: Literal['computed','needs_data']
    mechanical_advantage: Positive | None = None
    required_lead_pull_N: Nonnegative | None = None
    lifting_efficiency: Positive | None = Field(default=None,le=1)
    lead_rope_travel_m: Nonnegative | None = None
    useful_lifting_work_J: Nonnegative | None = None
    lead_input_work_J: Nonnegative | None = None
    dissipated_work_J: Nonnegative | None = None
    issues: tuple[str,...] = ()

def evaluate_reeved_hoist(q: ReevedHoistRequest) -> ReevedHoistResult:
    """Sum n supported-leg tensions E*Tlead through E**n*Tlead.

    Load and pull are forces. There is no extra redirecting block, dynamic
    acceleration, elasticity, rope rating, brake limit or safety factor inferred.
    The inextensible routing constraint gives lead travel n times load rise.
    """
    q=ReevedHoistRequest.model_validate(q.model_dump(mode='python'))
    if q.supported_load_N is None or q.per_sheave_efficiency is None or not q.successive_loaded_leg_loss_and_direct_hoist_route_reviewed:
        return ReevedHoistResult(status='needs_data',issues=('load, efficiency and exact reeving/routing review required',))
    e=q.per_sheave_efficiency;n=q.working_rope_parts
    if e==1:ma=float(n)
    else:
        le=math.log(e);ma=e*math.expm1(n*le)/math.expm1(le)
    pull=q.supported_load_N/ma
    values=dict(mechanical_advantage=ma,required_lead_pull_N=pull,lifting_efficiency=ma/n)
    if q.load_rise_m is not None:
        travel=n*q.load_rise_m;useful=q.supported_load_N*q.load_rise_m
        incoming=pull*travel;loss=pull*q.load_rise_m*(n-ma)
        values.update(lead_rope_travel_m=travel,useful_lifting_work_J=useful,lead_input_work_J=incoming,dissipated_work_J=loss)
    if not all(math.isfinite(v) and v>=0 for v in values.values()):raise ValueError('hoist result exceeds finite numerical support')
    return ReevedHoistResult(status='computed',**values)
