"""Measured bulk mass partition and separately declared liquid-equivalent volumes.

The composition owner is used in its phase-neutral molar-mass-only mode. Actual
mixture density is measured; additive component volumes and a mixture EOS are
not assumed. Liquid-equivalent volumes are reporting coordinates, not in-situ
component volumes or a predicted contraction law.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Nonnegative,Positive
from .gas_caloric_mixture import GasCaloricComponent,GasCaloricMixtureRequest,evaluate_gas_caloric_mixture


class MassMeterFlowingState(Contract):
    asset_id: Name
    stream_id: Name
    state_id: Name
    pressure_Pa_abs: Positive
    temperature_K: Positive
    clock_id: Name
    observation_time_s: float


class LiquidEquivalentReference(Contract):
    kind: Literal['actual_single_liquid_state','declared_liquid_equivalent_convention']
    reference_basis_id: Name
    reporting_set_id: Name | None = None
    density_kg_m3: Positive | None = None
    pressure_Pa_abs: Positive | None = None
    temperature_K: Positive | None = None
    density_reference_applicability_reviewed: bool = False


class MassPartitionComponent(Contract):
    id: Name
    fraction: Nonnegative
    molar_mass_kg_mol: Positive | None
    liquid_equivalent_reference: LiquidEquivalentReference | None = None


class ComponentMassReferenceRequest(Contract):
    quantity_kind: Literal['volume','volume_flow']
    metered_actual_volume_quantity: Nonnegative | None = None
    supplied_meter_factor: Positive | None = None
    meter_factor_basis_id: Name | None = None
    measured_bulk_density_kg_m3: Positive | None = None
    volume_state: MassMeterFlowingState
    density_state: MassMeterFlowingState
    fraction_basis: Literal['mole','mass']
    components: tuple[MassPartitionComponent,...] = Field(min_length=1,max_length=64)
    composition_sample_id: Name
    composition_basis_id: Name
    reference_reporting_set_id: Name | None = None
    component_reference_volumes_requested: bool = True
    actual_meter_factor_density_and_composite_composition_basis_reviewed: bool = False


class ComponentMassReferenceObservation(Contract):
    id: Name
    mole_fraction: Nonnegative
    mass_fraction: Nonnegative
    mass_quantity: Nonnegative
    molar_quantity: Nonnegative
    liquid_equivalent_reference: LiquidEquivalentReference | None = None
    reference_equivalent_volume_quantity: Nonnegative | None = None
    reference_volume_status: Literal['computed','needs_data','not_requested']
    reference_equivalent_volume_fraction: Nonnegative | None = None


class ComponentMassReferenceResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    mass_partition_status: Literal['computed','needs_data','out_of_scope']
    reference_volume_status: Literal['computed','needs_data','not_requested']
    quantity_kind: Literal['volume','volume_flow']
    volume_state: MassMeterFlowingState
    composition_sample_id: Name
    composition_basis_id: Name
    reference_reporting_set_id: Name | None = None
    corrected_actual_volume_quantity: Nonnegative | None = None
    total_mass_quantity: Nonnegative | None = None
    total_molar_quantity: Nonnegative | None = None
    mixture_molar_mass_kg_mol: Positive | None = None
    components: tuple[ComponentMassReferenceObservation,...] = ()
    sum_reference_equivalent_volume_quantity: Nonnegative | None = None
    mass_quantity_unit: Literal['kg','kg/s']
    molar_quantity_unit: Literal['mol','mol/s']
    volume_quantity_unit: Literal['m^3','m^3/s']
    mass_partition_residual: float | None = None
    molar_partition_residual: float | None = None
    issues: tuple[str,...] = ()
    actual_component_volume_fractions_inferred: Literal[False] = False
    bulk_volume_equals_reference_volume_sum_assumed: Literal[False] = False
    EOS_or_component_density_predicted: Literal[False] = False
    reference_volume_fractions_are_reporting_coordinates: Literal[True] = True


def observe_component_mass_reference(q:ComponentMassReferenceRequest)->ComponentMassReferenceResult:
    """Map actual corrected volume*rho to constituent mass and reference volumes.

    Meter quantity and density must describe the identical flowing state and
    observation coordinate. Composition must represent that metered quantity.
    A declared conventional liquid-equivalent density is distinct from an actual
    stable single-liquid PT state, particularly for volatile constituents. All
    values/reference applicability remain supplied; no property table is built in.
    """
    q=ComponentMassReferenceRequest.model_validate(q.model_dump(mode='python'))
    common=dict(quantity_kind=q.quantity_kind,volume_state=q.volume_state,
        composition_sample_id=q.composition_sample_id,composition_basis_id=q.composition_basis_id,
        reference_reporting_set_id=q.reference_reporting_set_id,
        mass_quantity_unit='kg' if q.quantity_kind=='volume' else 'kg/s',
        molar_quantity_unit='mol' if q.quantity_kind=='volume' else 'mol/s',
        volume_quantity_unit='m^3' if q.quantity_kind=='volume' else 'm^3/s')
    requested='needs_data' if q.component_reference_volumes_requested else 'not_requested'
    if q.volume_state!=q.density_state:
        return ComponentMassReferenceResult(status='out_of_scope',mass_partition_status='out_of_scope',
            reference_volume_status=requested,issues=('meter volume and bulk density require an explicitly reconciled identical PT/state/asset/stream/clock coordinate',),**common)
    if any(x is None for x in (q.metered_actual_volume_quantity,q.supplied_meter_factor,q.meter_factor_basis_id,q.measured_bulk_density_kg_m3)) or not q.actual_meter_factor_density_and_composite_composition_basis_reviewed:
        return ComponentMassReferenceResult(status='needs_data',mass_partition_status='needs_data',reference_volume_status=requested,
            issues=('actual meter factor, same-state density and representative composition review required',),**common)
    mixture=evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(mode='molar_mass_only',fraction_basis=q.fraction_basis,
        components=tuple(GasCaloricComponent(id=c.id,fraction=c.fraction,molar_mass_kg_mol=c.molar_mass_kg_mol) for c in q.components),
        temperature_K=q.volume_state.temperature_K,property_basis_id=q.composition_basis_id,
        nonreacting_component_composition_ledger_reviewed=True))
    if mixture.status!='computed':
        return ComponentMassReferenceResult(status=mixture.status,mass_partition_status=mixture.status,
            reference_volume_status=requested,issues=mixture.issues,**common)
    volume=q.metered_actual_volume_quantity*q.supplied_meter_factor
    mass=volume*q.measured_bulk_density_kg_m3;moles=mass/mixture.mixture_molar_mass_kg_mol
    if not all(math.isfinite(x) for x in (volume,mass,moles)) or (q.metered_actual_volume_quantity>0 and min(volume,mass,moles)==0):
        return ComponentMassReferenceResult(status='out_of_scope',mass_partition_status='out_of_scope',reference_volume_status=requested,
            issues=('positive actual quantity outside finite mass/mole arithmetic support',),**common)
    observations=[]
    for c in q.components:
        mi=mass*mixture.mass_fractions[c.id];ni=moles*mixture.mole_fractions[c.id]
        if (mass>0 and mixture.mass_fractions[c.id]>0 and mi==0) or (moles>0 and mixture.mole_fractions[c.id]>0 and ni==0):
            raise ValueError('positive component amount underflow is not an absent constituent')
        ref=c.liquid_equivalent_reference;equivalent=None;state='not_requested'
        if q.component_reference_volumes_requested:
            state='needs_data'
            if (ref is not None and ref.density_kg_m3 is not None and ref.density_reference_applicability_reviewed
                and q.reference_reporting_set_id is not None and ref.reporting_set_id==q.reference_reporting_set_id):
                if ref.kind=='declared_liquid_equivalent_convention' or (ref.pressure_Pa_abs is not None and ref.temperature_K is not None):
                    equivalent=mi/ref.density_kg_m3
                    if not math.isfinite(equivalent) or (mi>0 and equivalent==0):
                        raise ValueError('positive reference-volume quantity outside finite support')
                    state='computed'
        observations.append(ComponentMassReferenceObservation(id=c.id,mole_fraction=mixture.mole_fractions[c.id],
            mass_fraction=mixture.mass_fractions[c.id],mass_quantity=mi,molar_quantity=ni,
            liquid_equivalent_reference=ref,reference_equivalent_volume_quantity=equivalent,reference_volume_status=state))
    mass_sum=math.fsum(x.mass_quantity for x in observations);molar_sum=math.fsum(x.molar_quantity for x in observations)
    if not math.isclose(mass_sum,mass,rel_tol=2e-12,abs_tol=0.) or not math.isclose(molar_sum,moles,rel_tol=2e-12,abs_tol=0.):
        raise ValueError('component mass or mole partition does not close')
    reference_status='not_requested' if not q.component_reference_volumes_requested else (
        'computed' if all(x.reference_volume_status=='computed' for x in observations) else 'needs_data')
    if reference_status=='computed':
        # Normalize the reference volume per unit bulk mass, so a known zero
        # metered quantity retains composition-derived reporting fractions.
        contributions=[c.mass_fraction/c.liquid_equivalent_reference.density_kg_m3 for c in observations]
        total=math.fsum(contributions)
        if not math.isfinite(total) or total<=0 or any(c.mass_fraction>0 and v==0 for c,v in zip(observations,contributions)):
            raise ValueError('reference-volume fraction weights outside finite support')
        observations=[c.model_copy(update={'reference_equivalent_volume_fraction':v/total}) for c,v in zip(observations,contributions)]
    return ComponentMassReferenceResult(status='needs_data' if reference_status=='needs_data' else 'computed',mass_partition_status='computed',
        reference_volume_status=reference_status,corrected_actual_volume_quantity=volume,total_mass_quantity=mass,total_molar_quantity=moles,
        mixture_molar_mass_kg_mol=mixture.mixture_molar_mass_kg_mol,components=tuple(observations),
        sum_reference_equivalent_volume_quantity=math.fsum(x.reference_equivalent_volume_quantity for x in observations) if reference_status=='computed' else None,
        mass_partition_residual=mass_sum-mass,molar_partition_residual=molar_sum-moles,
        issues=('some component liquid-equivalent densities/references remain unknown',) if reference_status=='needs_data' else (),**common)
