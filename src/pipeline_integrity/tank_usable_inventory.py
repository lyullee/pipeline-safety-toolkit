"""State-consistent usable tank inventory assembled from existing owners.

Geometry, calibration interpolation and liquid-property resolution remain
with the tank owner. Reference volume uses the same mass with a separately
declared reference density; raw actual volume is never multiplied by an
unrelated reference-state density. Transfer-time outputs are conditional
constant-mean extrapolations to physical geometric boundaries.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Name,Nonnegative,Positive,RunContext
from .tanks import TankInventoryRequest,TankObservation,TankDensity,evaluate_tank_inventory,_density


class TankUsableInventoryRequest(Contract):
    inventory_request: TankInventoryRequest
    pump_intake_cutoff_observation: TankObservation | None = None
    reference_liquid_density: TankDensity | None = None
    actual_and_reference_common_mass_basis_id: Name | None = None
    geometry_cutoff_same_internal_datum_and_uniform_states_reviewed: bool = False
    actual_and_reference_same_liquid_and_mass_basis_reviewed: bool = False
    previous_observation: TankObservation | None = None
    level_history_common_geometry_state_reviewed: bool = False
    signed_mean_actual_volume_flow_m3_s: float | None = None
    constant_mean_flow_for_boundary_time_reviewed: bool = False

    @model_validator(mode='after')
    def one_flow_source(self):
        if self.previous_observation is not None and self.signed_mean_actual_volume_flow_m3_s is not None:
            raise ValueError('choose an observed volume-history rate or a supplied actual-state rate')
        if self.previous_observation is not None and self.previous_observation.time_s >= self.inventory_request.observation.time_s:
            raise ValueError('previous actual volume observation must be strictly earlier on the same clock')
        if self.signed_mean_actual_volume_flow_m3_s is not None and not math.isfinite(self.signed_mean_actual_volume_flow_m3_s):
            raise ValueError('actual-state flow must be finite')
        return self


class TankUsableInventoryResult(Contract):
    state: Literal['computed','needs_data','out_of_scope']
    reason: str
    gross_actual_volume_m3: Nonnegative | None = None
    physical_capacity_m3: Positive | None = None
    physical_ullage_m3: Nonnegative | None = None
    physical_ullage_fraction: Nonnegative | None = None
    geometric_nonpumpable_capacity_m3: Nonnegative | None = None
    current_nonpumpable_actual_volume_m3: Nonnegative | None = None
    current_pumpable_actual_volume_m3: Nonnegative | None = None
    maximum_pumpable_actual_volume_m3: Nonnegative | None = None
    below_pump_intake_cutoff: bool | None = None
    gross_mass_kg: Nonnegative | None = None
    nonpumpable_mass_kg: Nonnegative | None = None
    pumpable_mass_kg: Nonnegative | None = None
    gross_reference_volume_m3: Nonnegative | None = None
    nonpumpable_reference_volume_m3: Nonnegative | None = None
    pumpable_reference_volume_m3: Nonnegative | None = None
    actual_liquid_density_kg_m3: Positive | None = None
    reference_liquid_density_kg_m3: Positive | None = None
    signed_mean_actual_volume_flow_m3_s: float | None = None
    signed_corresponding_mass_flow_kg_s: float | None = None
    conditional_time_to_physical_full_s: Nonnegative | None = None
    conditional_time_to_pump_intake_cutoff_s: Nonnegative | None = None
    native_inventory_result_digest: Name | None = None
    physical_operating_safety_limits_selected: bool = False
    field_or_standard_qualified: bool = False


def evaluate_tank_usable_inventory(request:TankUsableInventoryRequest,context:RunContext,*,property_provider=None) -> TankUsableInventoryResult:
    """Keep actual/reference volume, pump cutoff and signed transfer bases explicit.

    A below-intake inventory explicitly has zero accessible pumpable volume;
    the actual trapped volume is the current liquid volume, not the larger
    geometrical cutoff capacity. Mean flow from level history requires the
    same actual geometry and liquid state across the window. Neither a mean
    history rate nor a supplied rate alone justifies a future time estimate.
    """
    def fail(state,reason):return TankUsableInventoryResult(state=state,reason=reason)
    q=request
    if q.pump_intake_cutoff_observation is None or not q.geometry_cutoff_same_internal_datum_and_uniform_states_reviewed or not q.actual_and_reference_same_liquid_and_mass_basis_reviewed or q.reference_liquid_density is None or q.actual_and_reference_common_mass_basis_id is None:
        return fail('needs_data','actual cutoff, uniform geometry/state and explicit common-mass reference density require review')
    if q.inventory_request.liquid_density is None:
        return fail('needs_data','actual liquid density is required separately from reference density')
    if q.reference_liquid_density.evidence_id not in {e.id for e in context.evidence}:
        raise ValueError('reference density evidence must be present in the actual context')
    def fluid(spec):return spec.fluid if spec.kind=='declared' else spec.state.fluid
    if fluid(q.reference_liquid_density)!=fluid(q.inventory_request.liquid_density):
        return fail('out_of_scope','actual/reference density specifications identify different liquids')
    native=evaluate_tank_inventory(q.inventory_request,context,property_provider=property_provider)
    if native['status']!='computed':return fail('needs_data' if native['status']=='needs_data' else 'out_of_scope','actual tank observation cannot resolve native geometry')
    cutoff_request=q.inventory_request.model_copy(update={'observation':q.pump_intake_cutoff_observation})
    cutoff=evaluate_tank_inventory(cutoff_request,context,property_provider=property_provider)
    if cutoff['status']!='computed':return fail('needs_data' if cutoff['status']=='needs_data' else 'out_of_scope','pump intake cutoff cannot resolve in the same geometry/datum')
    actual,_,issue=_density(q.inventory_request.liquid_density,'liquid',property_provider)
    reference,_,refissue=_density(q.reference_liquid_density,'liquid',property_provider)
    if issue or refissue or actual is None or reference is None:return fail('out_of_scope','actual/reference density does not resolve as the reviewed liquid phase')
    for result in (native,cutoff):
        observed=result['values'].get('liquid_density_kg_m3')
        if observed is not None and not math.isclose(observed,actual,rel_tol=1e-12):raise ValueError('actual liquid density changed across the same tank observation state')
    volume=native['values']['liquid_volume_m3'];capacity=native['values']['total_internal_volume_m3'];np_capacity=cutoff['values']['liquid_volume_m3'];ullage=capacity-volume
    below=volume<np_capacity
    trapped=volume if below else np_capacity
    available=0. if below else volume-np_capacity
    maximum=capacity-np_capacity
    gross_mass=actual*volume;trapped_mass=actual*trapped;pumpable_mass=actual*available
    flow=q.signed_mean_actual_volume_flow_m3_s
    if q.previous_observation is not None:
        if not q.level_history_common_geometry_state_reviewed:return fail('needs_data','level-history flow needs reviewed common geometry and actual liquid state')
        previous=evaluate_tank_inventory(q.inventory_request.model_copy(update={'observation':q.previous_observation}),context,property_provider=property_provider)
        if previous['status']!='computed':return fail('out_of_scope','previous observation is outside the actual geometry/state')
        flow=(volume-previous['values']['liquid_volume_m3'])/(q.inventory_request.observation.time_s-q.previous_observation.time_s)
    fill_time=empty_time=None
    if flow is not None and q.constant_mean_flow_for_boundary_time_reviewed:
        if flow>0:fill_time=ullage/flow
        elif flow<0:empty_time=available/(-flow)
    mass_flow=None if flow is None else flow*actual
    references=(gross_mass/reference,trapped_mass/reference,pumpable_mass/reference)
    vals=(ullage,trapped,available,maximum,gross_mass,trapped_mass,pumpable_mass,*references,*(() if fill_time is None else (fill_time,)),*(() if empty_time is None else (empty_time,)))
    if not all(math.isfinite(v) and v>=0 for v in vals) or (mass_flow is not None and not math.isfinite(mass_flow)):
        return fail('out_of_scope','tank inventory or transfer lies outside finite physical coordinates')
    return TankUsableInventoryResult(state='computed',reason='explicit below-intake inventory' if below else 'actual/reference consistent usable liquid inventory',gross_actual_volume_m3=volume,physical_capacity_m3=capacity,physical_ullage_m3=ullage,physical_ullage_fraction=ullage/capacity,geometric_nonpumpable_capacity_m3=np_capacity,current_nonpumpable_actual_volume_m3=trapped,current_pumpable_actual_volume_m3=available,maximum_pumpable_actual_volume_m3=maximum,below_pump_intake_cutoff=below,gross_mass_kg=gross_mass,nonpumpable_mass_kg=trapped_mass,pumpable_mass_kg=pumpable_mass,gross_reference_volume_m3=references[0],nonpumpable_reference_volume_m3=references[1],pumpable_reference_volume_m3=references[2],actual_liquid_density_kg_m3=actual,reference_liquid_density_kg_m3=reference,signed_mean_actual_volume_flow_m3_s=flow,signed_corresponding_mass_flow_kg_s=mass_flow,conditional_time_to_physical_full_s=fill_time,conditional_time_to_pump_intake_cutoff_s=empty_time,native_inventory_result_digest=native['result_digest'])
