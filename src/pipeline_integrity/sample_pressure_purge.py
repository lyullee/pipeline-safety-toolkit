"""Ideal fixed-temperature fill/mix/vent provenance ledger for sample cylinders."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Fraction,Name,Nonnegative,Positive
from .gas_caloric_mixture import (R_UNIVERSAL_J_MOL_K,GasCaloricAmountComponent,
    GasCaloricAmountsRequest,gas_caloric_mixture_from_amounts)


class PressurePurgeStage(Contract):
    fill_pressure_Pa_abs: Positive
    vent_pressure_after_Pa_abs: Positive | None = None


class SamplePressurePurgeRequest(Contract):
    cylinder_volume_m3: Positive
    common_temperature_K: Positive
    initial_pressure_Pa_abs: Positive
    original_gas_molar_mass_kg_mol: Positive
    feed_gas_molar_mass_kg_mol: Positive
    initial_original_gas_mole_fraction: Fraction = 1.
    stages: tuple[PressurePurgeStage,...] = Field(max_length=1000)
    target_original_gas_mole_fraction: Fraction | None = None
    clock_id: Name
    property_basis_id: Name
    ideal_fixed_temperature_volume_perfect_mixing_nonselective_vent_and_original_free_feed_reviewed: bool = False


class PressurePurgePoint(Contract):
    stage_number: int
    phase: Literal['initial','filled_and_retained','filled_then_vented']
    pressure_Pa_abs: Positive
    total_moles_mol: Positive
    original_gas_moles_mol: Nonnegative
    feed_gas_moles_mol: Nonnegative
    original_gas_mole_fraction: Fraction
    log_original_gas_mole_fraction: float | None
    original_fraction_underflowed: bool = False
    mass_kg: Nonnegative
    added_feed_moles_mol: Nonnegative
    vented_original_moles_mol: Nonnegative
    vented_feed_moles_mol: Nonnegative
    original_mole_budget_residual_mol: float
    feed_mole_budget_residual_mol: float
    mass_budget_residual_kg: float


class SamplePressurePurgeResult(Contract):
    status: Literal['computed','needs_data']
    clock_id: Name
    property_basis_id: Name
    points: tuple[PressurePurgePoint,...] = ()
    first_target_met_after_fill_number: int | None = None
    final_target_met: bool | None = None
    issues: tuple[str,...] = ()


def evaluate_sample_pressure_purge(q:SamplePressurePurgeRequest)->SamplePressurePurgeResult:
    """Preserve original molecules on fill; proportionally remove each group on vent.

    Gas temperature is the same at all reviewed endpoints, with no adsorption,
    reaction, selective vent, continuous flush or real-gas correction. Final fill
    can be retained: filling dilutes original mole fraction, venting alone does
    not. Original/feed labels track provenance even when their chemistry matches.
    """
    q=SamplePressurePurgeRequest.model_validate(q.model_dump(mode='python'))
    common=dict(clock_id=q.clock_id,property_basis_id=q.property_basis_id)
    pressure=q.initial_pressure_Pa_abs
    for s in q.stages:
        if s.fill_pressure_Pa_abs<pressure:raise ValueError('fill pressure cannot lower the cylinder pressure')
        if s.vent_pressure_after_Pa_abs is not None and s.vent_pressure_after_Pa_abs>s.fill_pressure_Pa_abs:
            raise ValueError('vent cannot raise the cylinder pressure')
        pressure=s.vent_pressure_after_Pa_abs if s.vent_pressure_after_Pa_abs is not None else s.fill_pressure_Pa_abs
    if not q.ideal_fixed_temperature_volume_perfect_mixing_nonselective_vent_and_original_free_feed_reviewed:
        return SamplePressurePurgeResult(status='needs_data',issues=('ideal endpoint inventories and fill/mix/vent process require review',),**common)
    coordinate=q.cylinder_volume_m3/(R_UNIVERSAL_J_MOL_K*q.common_temperature_K)
    initial_n=q.initial_pressure_Pa_abs*coordinate
    old=initial_n*q.initial_original_gas_mole_fraction;fresh=initial_n-old
    initial_old,initial_feed=old,fresh
    log_fraction=math.log(q.initial_original_gas_mole_fraction) if old>0 else None
    added=vented_old=vented_feed=0.;points=[]
    target=q.target_original_gas_mole_fraction
    def target_met():
        if target is None:return None
        if log_fraction is None:return True
        return target>0 and log_fraction<=math.log(target)
    first=0 if target_met() else None

    def point(number,phase,pressure):
        nonlocal old,fresh
        n=old+fresh
        # Reuse the existing neutral amount/composition/molar-mass ledger.
        mixture=gas_caloric_mixture_from_amounts(GasCaloricAmountsRequest(mode='molar_mass_only',
            quantity_basis='molar_amount_mol',components=(
                GasCaloricAmountComponent(id='original-provenance',input_quantity=old,molar_mass_kg_mol=q.original_gas_molar_mass_kg_mol),
                GasCaloricAmountComponent(id='feed-provenance',input_quantity=fresh,molar_mass_kg_mol=q.feed_gas_molar_mass_kg_mol)),
            common_clock_id=q.clock_id,temperature_K=q.common_temperature_K,property_basis_id=q.property_basis_id,
            nonreacting_component_composition_ledger_reviewed=True)).mixture
        if mixture.status!='computed':raise ValueError('provenance composition ledger unavailable')
        mass=n*mixture.mixture_molar_mass_kg_mol
        ro=old+vented_old-initial_old;rf=fresh+vented_feed-initial_feed-added
        return PressurePurgePoint(stage_number=number,phase=phase,pressure_Pa_abs=pressure,total_moles_mol=n,
            original_gas_moles_mol=old,feed_gas_moles_mol=fresh,original_gas_mole_fraction=old/n,
            log_original_gas_mole_fraction=log_fraction,mass_kg=mass,added_feed_moles_mol=added,
            original_fraction_underflowed=log_fraction is not None and old/n==0,
            vented_original_moles_mol=vented_old,vented_feed_moles_mol=vented_feed,
            original_mole_budget_residual_mol=ro,feed_mole_budget_residual_mol=rf,
            mass_budget_residual_kg=ro*q.original_gas_molar_mass_kg_mol+rf*q.feed_gas_molar_mass_kg_mol)
    points.append(point(0,'initial',q.initial_pressure_Pa_abs));pressure=q.initial_pressure_Pa_abs
    for number,s in enumerate(q.stages,1):
        incoming=(s.fill_pressure_Pa_abs-pressure)*coordinate
        fresh+=incoming;added+=incoming
        if log_fraction is not None:log_fraction+=math.log(pressure/s.fill_pressure_Pa_abs)
        if first is None and target_met():first=number
        if s.vent_pressure_after_Pa_abs is not None:
            kept=s.vent_pressure_after_Pa_abs/s.fill_pressure_Pa_abs
            removed_old=old*(1-kept);removed_feed=fresh*(1-kept)
            old*=kept;fresh*=kept;vented_old+=removed_old;vented_feed+=removed_feed
            pressure=s.vent_pressure_after_Pa_abs;phase='filled_then_vented'
        else:pressure=s.fill_pressure_Pa_abs;phase='filled_and_retained'
        points.append(point(number,phase,pressure))
    return SamplePressurePurgeResult(status='computed',points=tuple(points),
        first_target_met_after_fill_number=first,final_target_met=target_met(),**common)
