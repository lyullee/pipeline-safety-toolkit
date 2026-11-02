"""Ideal nozzle pressure inversion and explicitly referenced gas opening flow.

The existing normalized nozzle kernel remains the sole capacity law. Throat
states are ideal capacity-equivalent states, not measured valve vena-contracta
states. No constant-gamma extension to a real EOS or wet steam is inferred.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Positive,Nonnegative,RunContext
from .gas_acoustic_pipe import IdealGasInternalEnergyEOS
from .gas_restricted_vent import ideal_vent_capacity
from .gas_basis import GasBasisRequest,evaluate_gas_basis,R_SI_J_MOL_K


class IdealOpeningGas(Contract):
    composition_id: Name
    molar_mass_kg_mol: Positive | None
    heat_capacity_ratio: float | None = Field(gt=1)
    property_basis_id: Name
    dry_calorically_perfect_gas_and_molar_mass_reviewed: bool = False


class IdealOpeningGeometry(Contract):
    actual_throat_area_m2: Positive | None
    discharge_coefficient: Positive | None = Field(le=1)
    coefficient_basis_id: Name | None = None
    supplied_geometry_and_coefficient_reviewed: bool = False


class SonicPressureInverseRequest(Contract):
    gas: IdealOpeningGas
    geometry: IdealOpeningGeometry
    target_mass_flow_kg_s: Nonnegative | None
    temperature_K: Positive | None
    temperature_definition: Literal['throat_static','upstream_stagnation']
    back_pressure_Pa_abs: Positive | None
    asset_id: Name
    clock_id: Name
    event_id: Name
    observation_time_s: Nonnegative | None = None


class IdealOpeningFlowRequest(Contract):
    gas: IdealOpeningGas
    geometry: IdealOpeningGeometry
    upstream_stagnation_pressure_Pa_abs: Positive | None
    upstream_stagnation_temperature_K: Positive | None
    back_pressure_Pa_abs: Positive | None
    asset_id: Name
    clock_id: Name
    event_id: Name
    observation_time_s: Nonnegative | None = None
    constant_boundary_duration_s: Nonnegative | None = None
    constant_boundaries_during_interval_reviewed: bool = False


class IdealOpeningObservation(Contract):
    status: Literal['computed','needs_data','not_sonic','zero_flow_no_unique_sonic_pressure']
    gas_composition_id: Name
    molar_mass_kg_mol: Positive | None
    property_basis_id: Name
    coefficient_basis_id: Name | None
    asset_id: Name
    clock_id: Name
    event_id: Name
    observation_time_s: Nonnegative | None
    mass_flow_kg_s: Nonnegative | None = None
    upstream_stagnation_pressure_Pa_abs: Positive | None = None
    upstream_stagnation_temperature_K: Positive | None = None
    ideal_throat_static_pressure_Pa_abs: Positive | None = None
    ideal_throat_static_temperature_K: Positive | None = None
    ideal_throat_density_kg_m3: Positive | None = None
    ideal_throat_velocity_m_s: Nonnegative | None = None
    effective_CdA_m2: Positive | None = None
    upstream_stagnation_volume_flow_m3_s: Nonnegative | None = None
    constant_boundary_mass_amount_kg: Nonnegative | None = None
    constant_boundary_upstream_volume_amount_m3: Nonnegative | None = None
    constant_boundary_duration_s: Nonnegative | None = None
    constant_boundary_interval_end_time_s: Nonnegative | None = None
    regime: Literal['choked','subcritical','no_outflow'] | None = None
    normalized_kernel_mass_residual_kg_s: float | None = None
    ideal_continuity_residual_kg_s: float | None = None
    specific_total_enthalpy_residual_J_kg: float | None = None
    issues: tuple[str,...] = ()
    field_or_device_qualified: Literal[False] = False


def _base(q):
    return dict(gas_composition_id=q.gas.composition_id,molar_mass_kg_mol=q.gas.molar_mass_kg_mol,
        property_basis_id=q.gas.property_basis_id,coefficient_basis_id=q.geometry.coefficient_basis_id,
        asset_id=q.asset_id,clock_id=q.clock_id,event_id=q.event_id,observation_time_s=q.observation_time_s)


def _known(q):
    if q.gas.heat_capacity_ratio is not None and q.gas.heat_capacity_ratio-1<1e-6:
        raise ValueError('gamma too close to one for the upstream nozzle kernel numerical support')
    return (q.gas.molar_mass_kg_mol is not None and q.gas.heat_capacity_ratio is not None
        and q.gas.dry_calorically_perfect_gas_and_molar_mass_reviewed
        and q.geometry.actual_throat_area_m2 is not None and q.geometry.discharge_coefficient is not None
        and q.geometry.coefficient_basis_id is not None and q.geometry.supplied_geometry_and_coefficient_reviewed)


def _capacity(q,p0,t0,pb):
    eos=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=R_SI_J_MOL_K/q.gas.molar_mass_kg_mol,gamma=q.gas.heat_capacity_ratio)
    return ideal_vent_capacity(stagnation_pressure_pa_abs=p0,stagnation_temperature_K=t0,
        ambient_pressure_pa_abs=pb,vent_area_m2=q.geometry.actual_throat_area_m2,
        discharge_coefficient=q.geometry.discharge_coefficient,eos=eos)


def _observation(q,p0,t0,pb,mass,regime):
    k=q.gas.heat_capacity_ratio;gas_r=R_SI_J_MOL_K/q.gas.molar_mass_kg_mol
    critical_ratio=(2/(k+1))**(k/(k-1));pressure=p0*critical_ratio if regime=='choked' else min(pb,p0)
    temperature=t0*(pressure/p0)**((k-1)/k)
    cp=gas_r*(k/(k-1));speed=math.sqrt(max(0.,2*cp*(t0-temperature)));density=pressure/(gas_r*temperature)
    area=q.geometry.actual_throat_area_m2*q.geometry.discharge_coefficient
    source_volume=mass*gas_r*t0/p0
    vals=[p0,t0,pressure,temperature,speed,density,area,source_volume,mass]
    if not all(math.isfinite(x) for x in vals):raise ValueError('opening observation exceeds finite support')
    return dict(mass_flow_kg_s=mass,upstream_stagnation_pressure_Pa_abs=p0,upstream_stagnation_temperature_K=t0,
        ideal_throat_static_pressure_Pa_abs=pressure,ideal_throat_static_temperature_K=temperature,
        ideal_throat_density_kg_m3=density,ideal_throat_velocity_m_s=speed,effective_CdA_m2=area,
        upstream_stagnation_volume_flow_m3_s=source_volume,regime=regime,
        ideal_continuity_residual_kg_s=mass-area*density*speed,
        specific_total_enthalpy_residual_J_kg=cp*(t0-temperature)-speed*speed/2)


def invert_sonic_throat_pressure(q: SonicPressureInverseRequest) -> IdealOpeningObservation:
    """Infer ideal sonic static/total pressures from explicit mass, area and T.

    A one-Pascal capacity call normalizes a pressure-linear operator; it is not
    a property or continuum-validity claim for a physical one-Pascal experiment.
    Back pressure must not exceed the inferred critical static pressure.
    Neither gauge pressure nor an unspecified 'flowing temperature' is inferred.
    """
    q=SonicPressureInverseRequest.model_validate(q.model_dump(mode='python'));base=_base(q)
    if not _known(q) or None in (q.target_mass_flow_kg_s,q.temperature_K,q.back_pressure_Pa_abs):
        return IdealOpeningObservation(status='needs_data',**base,issues=('reviewed ideal gas, geometry/Cd, explicit T basis, target mass and absolute back pressure required',))
    if q.target_mass_flow_kg_s==0:
        return IdealOpeningObservation(status='zero_flow_no_unique_sonic_pressure',**base,mass_flow_kg_s=0,
            issues=('zero flow does not identify a positive sonic pressure; sealed/no-drive states are not a sonic inverse',))
    k=q.gas.heat_capacity_ratio;ratio=(2/(k+1))**(k/(k-1))
    t0=q.temperature_K*(k+1)/2 if q.temperature_definition=='throat_static' else q.temperature_K
    unit=_capacity(q,1.,t0,ratio/2)[0]
    p0=q.target_mass_flow_kg_s/unit;critical=p0*ratio
    if not math.isfinite(p0) or p0<=0:raise ValueError('inferred sonic pressure exceeds finite positive support')
    if q.back_pressure_Pa_abs>critical*(1+2e-13):
        return IdealOpeningObservation(status='not_sonic',**base,upstream_stagnation_pressure_Pa_abs=p0,
            upstream_stagnation_temperature_K=t0,issues=('supplied back pressure exceeds the pressure compatible with this prescribed sonic mass/area state',))
    actual,regime,*_=_capacity(q,p0,t0,q.back_pressure_Pa_abs)
    data=_observation(q,p0,t0,q.back_pressure_Pa_abs,q.target_mass_flow_kg_s,'choked')
    data['normalized_kernel_mass_residual_kg_s']=actual-q.target_mass_flow_kg_s
    return IdealOpeningObservation(status='computed',**base,**data)


def observe_ideal_opening_flow(q: IdealOpeningFlowRequest) -> IdealOpeningObservation:
    """Observe one-way quasi-steady capacity and optional constant-state amount.

    Supplied upstream values are stagnation quantities. A nearby pipe's static
    pressure requires a separate velocity/state mapping; no rest-state shortcut
    or fixed specific gravity/reference-temperature coefficient is inserted.
    """
    q=IdealOpeningFlowRequest.model_validate(q.model_dump(mode='python'));base=_base(q)
    if not _known(q) or None in (q.upstream_stagnation_pressure_Pa_abs,q.upstream_stagnation_temperature_K,q.back_pressure_Pa_abs):
        return IdealOpeningObservation(status='needs_data',**base,issues=('complete reviewed ideal-gas state and supplied area/Cd required',))
    p,t,pb=q.upstream_stagnation_pressure_Pa_abs,q.upstream_stagnation_temperature_K,q.back_pressure_Pa_abs
    mass,regime,*_=_capacity(q,p,t,pb);data=_observation(q,p,t,pb,mass,regime);issues=[]
    if q.constant_boundary_duration_s is not None:
        if not q.constant_boundaries_during_interval_reviewed:
            issues.append('flow is known but accumulated amount requires reviewed constant boundary duration')
        else:
            data['constant_boundary_mass_amount_kg']=mass*q.constant_boundary_duration_s
            data['constant_boundary_upstream_volume_amount_m3']=data['upstream_stagnation_volume_flow_m3_s']*q.constant_boundary_duration_s
            data['constant_boundary_duration_s']=q.constant_boundary_duration_s
            data['constant_boundary_interval_end_time_s']=None if q.observation_time_s is None else q.observation_time_s+q.constant_boundary_duration_s
    return IdealOpeningObservation(status='computed',**base,**data,issues=tuple(issues))


def opening_reference_gas_ledger(observation: IdealOpeningObservation, request: GasBasisRequest, context: RunContext):
    """Bind an opening observation to the existing same-dry-species mole ledger.

    Source flow/amount is set from this observation; the original request is
    immutable. Explicit source P0/T0, ideal Z=1, dry composition identity,
    asset/event/clock and time must agree. Target P/T/Z are supplied conventions,
    not universal standards. Reference Z does not change the ideal nozzle law.
    """
    o=IdealOpeningObservation.model_validate(observation.model_dump(mode='python'));q=GasBasisRequest.model_validate(request.model_dump(mode='python'))
    if o.status!='computed':raise ValueError('a computed opening observation is required')
    if (q.asset_id,q.clock_id,q.event_id,q.time_s,q.dry_composition_id)!=(o.asset_id,o.clock_id,o.event_id,o.observation_time_s,o.gas_composition_id):
        raise ValueError('reference gas composition/asset/event/clock/time differs from opening observation')
    if (q.Z_mode!='supplied' or q.source.compressibility_Z_common_R!=1 or q.source.water_vapor_mole_fraction!=0
        or q.target.water_vapor_mole_fraction!=0 or q.source.volume_basis!='dry_gas' or q.target.volume_basis!='dry_gas'):
        raise ValueError('opening source requires explicitly supplied ideal dry gas and unchanged dry target composition')
    if not (math.isclose(q.source.pressure_pa_abs,o.upstream_stagnation_pressure_Pa_abs,rel_tol=1e-12)
        and math.isclose(q.source.temperature_K,o.upstream_stagnation_temperature_K,rel_tol=1e-12)):
        raise ValueError('reference source must use opening upstream stagnation P/T coordinate')
    source=o.upstream_stagnation_volume_flow_m3_s if q.quantity_kind=='volume_flow' else o.constant_boundary_upstream_volume_amount_m3
    expected_mass=o.mass_flow_kg_s if q.quantity_kind=='volume_flow' else o.constant_boundary_mass_amount_kg
    if source is None:raise ValueError('accumulated amount is unknown without a reviewed constant boundary duration')
    result=evaluate_gas_basis(q.model_copy(update={'source_value':source}),context)
    if result['status']=='computed':
        implied_mass=result['molar_ledger']['source_total_molar_quantity']*o.molar_mass_kg_mol
        if not math.isclose(implied_mass,expected_mass,rel_tol=2e-12,abs_tol=1e-300):raise ValueError('opening mass and reference mole ledger disagree')
    return result
