"""Measured endpoint efficiency and bounded equal-ratio stage selection.

Both are observations of existing thermodynamic/process owners. Machine maps,
global train optimization and field measurement/calibration are not inferred.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Positive,Nonnegative
from .thermo import CheckedProvider,CoolPropProvider,StateRequest
from .compressor_train import IntercoolerBoundary
from .real_compression_path import DifferentialCompressionStage,RealCompressionPathRequest,evaluate_real_compression_path


class CompressionEfficiencyObservationRequest(Contract):
    fluid: Name
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    inlet_pressure_Pa_abs: Positive | None
    inlet_temperature_K: Positive | None
    outlet_pressure_Pa_abs: Positive | None
    measured_outlet_temperature_K: Positive | None
    endpoint_basis: Literal['stagnation','negligible_velocity_static']
    steady_adiabatic_same_composition_negligible_height_change_reviewed: bool = False
    mass_flow_kg_s: Nonnegative | None = None
    property_basis_id: Name


class CompressionEfficiencyObservation(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    property_basis_id: Name
    endpoint_isentropic_efficiency: float | None = None
    reversible_work_J_kg: float | None = None
    observed_enthalpy_rise_J_kg: float | None = None
    observed_fluid_power_W: Nonnegative | None = None
    entropy_increase_J_kg_K: float | None = None
    issues: tuple[str,...] = ()
    differential_polytropic_efficiency_inferred: Literal[False] = False
    field_qualified: Literal[False] = False


def observe_endpoint_compression_efficiency(q: CompressionEfficiencyObservationRequest, *, property_provider=None) -> CompressionEfficiencyObservation:
    """Infer finite adiabatic eta_s from measured absolute endpoint states.

    An EOS isentropic endpoint supplies reversible work; measured enthalpy rise
    supplies actual fluid work only under the reviewed steady adiabatic basis.
    This is not a differential eta_p, thermal-loss separation or shaft efficiency.
    """
    q=CompressionEfficiencyObservationRequest.model_validate(q.model_dump(mode='python'));base=dict(property_basis_id=q.property_basis_id)
    if None in (q.inlet_pressure_Pa_abs,q.outlet_pressure_Pa_abs,q.inlet_temperature_K,q.measured_outlet_temperature_K) or not q.steady_adiabatic_same_composition_negligible_height_change_reviewed:
        return CompressionEfficiencyObservation(status='needs_data',**base,issues=('measured P/T endpoints and explicit adiabatic endpoint basis required',))
    if q.outlet_pressure_Pa_abs<=q.inlet_pressure_Pa_abs:raise ValueError('compression requires increased absolute pressure')
    owner=CheckedProvider(property_provider if property_provider is not None else CoolPropProvider(use_cache=False));identity=None
    def resolve(inputs):
        nonlocal identity
        s=owner.resolve(StateRequest(fluid=q.fluid,backend=q.backend,inputs=inputs))
        if s.phase not in {'gas','supercritical_gas','supercritical'}:raise ValueError('gaslike single-phase endpoints and reversible reference required')
        marker=(s.provider_version,s.provider_revision)
        if identity is not None and identity!=marker:raise ValueError('property provider changed its reference identity')
        identity=marker
        for key,field in [('P','pressure_pa_abs'),('T','temperature_k'),('Smass','entropy_j_kg_k')]:
            if key in inputs and not math.isclose(inputs[key],getattr(s,field),rel_tol=1e-9,abs_tol=1e-7):raise ValueError('provider changed requested endpoint '+key)
        return s
    first=resolve({'P':q.inlet_pressure_Pa_abs,'T':q.inlet_temperature_K})
    reversible=resolve({'P':q.outlet_pressure_Pa_abs,'Smass':first.entropy_j_kg_k})
    measured=resolve({'P':q.outlet_pressure_Pa_abs,'T':q.measured_outlet_temperature_K})
    ideal_work=reversible.enthalpy_j_kg-first.enthalpy_j_kg;actual=measured.enthalpy_j_kg-first.enthalpy_j_kg
    eta=None if actual==0 else ideal_work/actual;entropy=measured.entropy_j_kg_k-first.entropy_j_kg_k
    data=dict(reversible_work_J_kg=ideal_work,observed_enthalpy_rise_J_kg=actual,endpoint_isentropic_efficiency=eta,entropy_increase_J_kg_K=entropy)
    if ideal_work<=0 or actual<=0 or eta<=0 or eta>1+64*math.ulp(1.):
        return CompressionEfficiencyObservation(status='out_of_scope',**base,**data,issues=('observed states are incompatible with the declared positive-work adiabatic compressor; heat/correction is not inferred',))
    return CompressionEfficiencyObservation(status='computed',**base,**data,
        observed_fluid_power_W=None if q.mass_flow_kg_s is None else q.mass_flow_kg_s*actual)


class EqualRatioStageCountRequest(Contract):
    fluid: Name
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    inlet_pressure_Pa_abs: Positive | None
    target_pressure_Pa_abs: Positive | None
    inlet_and_reset_temperature_K: Positive | None
    maximum_discharge_temperature_K: Positive | None
    common_differential_efficiency: Positive | None = Field(le=1)
    maximum_stages: int = Field(default=16,ge=1,le=16)
    mass_flow_kg_s: Nonnegative | None = None
    identical_stages_perfect_temperature_reset_zero_interstage_loss_reviewed: bool = False
    steady_adiabatic_single_gas_path_reviewed: bool = False
    property_basis_id: Name


class StageCountCandidate(Contract):
    stage_count: int
    maximum_discharge_temperature_K: Positive
    meets_temperature_limit: bool
    specific_fluid_work_J_kg: Positive


class EqualRatioStageCountObservation(Contract):
    status: Literal['computed','needs_data','no_feasible_candidate_within_bound']
    property_basis_id: Name
    least_feasible_stage_count: int | None = None
    selected_specific_fluid_work_J_kg: Positive | None = None
    selected_specific_heat_rejected_J_kg: Nonnegative | None = None
    selected_fluid_power_W: Nonnegative | None = None
    candidates: tuple[StageCountCandidate,...] = ()
    issues: tuple[str,...] = ()
    global_train_optimum_or_vendor_rating_inferred: Literal[False] = False


def observe_minimum_equal_ratio_stage_count(q: EqualRatioStageCountRequest, *, property_provider=None) -> EqualRatioStageCountObservation:
    """Enumerate the declared equal-ratio candidates, reusing the process owner.

    Every earlier candidate is tested; this is the least feasible count only
    within the finite candidate bound, common efficiency and reviewed perfect
    interstage reset/zero-loss rule. Unknown mass flow uses an internal unit-mass
    process normalization, returns only specific work and preserves unknown
    actual power. It is not a hidden one-kg/s field-flow assumption.
    """
    q=EqualRatioStageCountRequest.model_validate(q.model_dump(mode='python'));base=dict(property_basis_id=q.property_basis_id)
    required=(q.inlet_pressure_Pa_abs,q.target_pressure_Pa_abs,q.inlet_and_reset_temperature_K,q.maximum_discharge_temperature_K,q.common_differential_efficiency)
    if None in required or not q.identical_stages_perfect_temperature_reset_zero_interstage_loss_reviewed or not q.steady_adiabatic_single_gas_path_reviewed:
        return EqualRatioStageCountObservation(status='needs_data',**base,issues=('complete state/limit/efficiency and reviewed equal-stage/cooling rule required',))
    if q.target_pressure_Pa_abs<=q.inlet_pressure_Pa_abs:raise ValueError('compression requires increased absolute pressure')
    provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
    log_ratio=math.log(q.target_pressure_Pa_abs)-math.log(q.inlet_pressure_Pa_abs);rows=[];identity=None
    for count in range(1,q.maximum_stages+1):
        stages=[]
        for j in range(1,count+1):
            pressure=q.target_pressure_Pa_abs if j==count else q.inlet_pressure_Pa_abs*math.exp(log_ratio*j/count)
            cooler=IntercoolerBoundary(mode='none' if j==count else 'outlet_temperature',pressure_drop_pa=0.,
                outlet_total_temperature_K=None if j==count else q.inlet_and_reset_temperature_K,evidence_id=q.property_basis_id)
            stages.append(DifferentialCompressionStage(id=f'stage-{j}',outlet_pressure_pa_abs=pressure,
                differential_polytropic_efficiency=q.common_differential_efficiency,intercooler=cooler,evidence_id=q.property_basis_id))
        try:
            result=evaluate_real_compression_path(RealCompressionPathRequest(fluid=q.fluid,backend=q.backend,
                inlet_pressure_pa_abs=q.inlet_pressure_Pa_abs,inlet_temperature_k=q.inlet_and_reset_temperature_K,
                mass_flow_kg_s=1. if q.mass_flow_kg_s is None else q.mass_flow_kg_s,stages=tuple(stages),
                single_phase_steady_adiabatic_stage_path_reviewed=True,efficiency_and_cooler_boundaries_reviewed=True,evidence_id=q.property_basis_id),property_provider=provider)
        except (ValueError,RuntimeError) as exc:
            return EqualRatioStageCountObservation(status='needs_data',**base,candidates=tuple(rows),
                issues=(f'candidate {count} unresolved; cannot certify a minimum: {exc}',))
        if result.status!='computed':return EqualRatioStageCountObservation(status='needs_data',**base,candidates=tuple(rows),issues=(f'candidate {count} unresolved: '+str(result.notes),))
        value=result.values;marker=(value['provider_version'],value['provider_revision'])
        if identity is not None and marker!=identity:raise ValueError('property reference changed between candidate stage counts')
        identity=marker
        maximum=max(s['compressed']['temperature_k'] for s in value['stages']);feasible=maximum<=q.maximum_discharge_temperature_K
        rows.append(StageCountCandidate(stage_count=count,maximum_discharge_temperature_K=maximum,
            meets_temperature_limit=feasible,specific_fluid_work_J_kg=value['specific_fluid_work_j_kg']))
        if feasible:
            return EqualRatioStageCountObservation(status='computed',**base,least_feasible_stage_count=count,
                selected_specific_fluid_work_J_kg=value['specific_fluid_work_j_kg'],selected_specific_heat_rejected_J_kg=value['specific_heat_rejected_j_kg'],
                selected_fluid_power_W=None if q.mass_flow_kg_s is None else value['fluid_power_w'],candidates=tuple(rows))
    return EqualRatioStageCountObservation(status='no_feasible_candidate_within_bound',**base,candidates=tuple(rows))
