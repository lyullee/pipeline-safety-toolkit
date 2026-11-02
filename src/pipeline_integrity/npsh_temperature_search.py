"""Temperature-dependent available suction-head search with injected properties.

The native Darcy and NPSH owners evaluate every trial state. Endpoints are
always included. A minimum over the complete requested domain is conditional
on the supplied partition-shape review; no arbitrary property function receives
a false global certificate from a finite numerical search.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive

Shape=Literal['unimodal_minimum','monotone','concave']


class SuctionTemperatureProperties(Contract):
    temperature_K: Positive
    property_reference_pressure_pa_abs: Positive
    density_kg_m3: Positive | None = None
    dynamic_viscosity_pa_s: Positive | None = None
    vapor_pressure_pa_abs: Positive | None = None
    phase: Literal['liquid','gas','two_phase','supercritical','unknown'] = 'unknown'
    provider_basis_id: Name | None = None


class NPSHTemperatureSearchRequest(Contract):
    temperature_partitions_K: tuple[Positive,...] = Field(min_length=2)
    objective_partition_shapes: tuple[Shape,...] | None = None
    objective_shape_reviewed_on_all_partitions: bool = False
    property_provider_basis_id: Name | None = None
    state_range_and_constant_density_per_temperature_reviewed: bool = False
    reservoir_suction_geometry_and_actual_flow_basis_reviewed: bool = False
    fixed_actual_volume_flow_m3_s: Positive
    suction_diameter_m: Positive
    straight_suction_length_m: Nonnegative
    absolute_roughness_m: Nonnegative
    fixed_minor_loss_coefficient: Nonnegative
    reservoir_surface_pressure_pa_abs: Positive
    reservoir_surface_above_pump_m: float
    gravity_m_s2: Positive = 9.80665
    friction_method: Literal['Clamond','Colebrook','Haaland','Swamee_Jain_1976'] = 'Clamond'
    temperature_absolute_tolerance_K: Positive = 1e-5
    maximum_iterations_per_partition: int = Field(default=300,ge=1,strict=True)
    vendor_required_npsh_m: Nonnegative | None = None
    additional_required_margin_m: Nonnegative | None = None
    vendor_requirement_and_margin_basis_id: Name | None = None
    vendor_constant_requirement_over_temperature_reviewed: bool = False
    include_evaluation_trace: bool = False

    @model_validator(mode='after')
    def partition(self):
        if any(b<=a for a,b in zip(self.temperature_partitions_K,self.temperature_partitions_K[1:])):
            raise ValueError('temperature partitions must increase strictly')
        if self.objective_partition_shapes is not None and len(self.objective_partition_shapes)!=len(self.temperature_partitions_K)-1:
            raise ValueError('one objective shape is required per temperature interval')
        if self.temperature_absolute_tolerance_K>=min(b-a for a,b in zip(self.temperature_partitions_K,self.temperature_partitions_K[1:])):
            raise ValueError('temperature tolerance must resolve each partition')
        return self


class NPSHTemperatureEvaluation(Contract):
    properties: SuctionTemperatureProperties
    available_npsh_m: float
    suction_head_loss_m: Nonnegative
    pressure_above_vapor_head_m: float
    reynolds: Nonnegative


class NPSHTemperatureSearchResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    minimum_temperature_K: Positive | None = None
    minimum_available_npsh_m: float | None = None
    minimum_state: NPSHTemperatureEvaluation | None = None
    property_evaluation_count: int = Field(default=0,ge=0)
    evaluations: tuple[NPSHTemperatureEvaluation,...] = ()
    vendor_comparison_status: Literal['not_requested','computed','needs_data'] = 'not_requested'
    available_minus_vendor_requirement_m: float | None = None
    residual_against_supplied_additional_margin_m: float | None = None
    minimum_is_conditional_on_reviewed_partition_shapes: bool = True
    global_certificate_for_arbitrary_property_functions: bool = False
    cavitation_phase_change_or_vendor_acceptance_qualified: bool = False
    issues: tuple[str,...] = ()


def search_minimum_available_npsh(request: NPSHTemperatureSearchRequest,
    property_provider: Callable[[float],SuctionTemperatureProperties | dict | None]) -> NPSHTemperatureSearchResult:
    """Find the adverse temperature in the declared piecewise reviewed domain.

    Properties are evaluated at the actual requested T and reservoir reference
    pressure. Density is uniform along the suction line for each T; mass flow
    therefore changes with rho when the declared actual-volume flow is fixed.
    A provider may wrap measured curves or an existing EOS/transport backend.
    Unavailable/invalid states abort the search and are never skipped in favor
    of a convenient minimum. Negative NPSHa is a head-budget diagnostic only.
    """
    if not isinstance(request,NPSHTemperatureSearchRequest):raise TypeError('NPSHTemperatureSearchRequest required')
    if not callable(property_provider):raise TypeError('callable temperature property provider required')
    q=NPSHTemperatureSearchRequest.model_validate(request.model_dump(mode='python'))
    if q.objective_partition_shapes is None or not q.objective_shape_reviewed_on_all_partitions or not q.state_range_and_constant_density_per_temperature_reviewed or not q.reservoir_suction_geometry_and_actual_flow_basis_reviewed or q.property_provider_basis_id is None:
        return NPSHTemperatureSearchResult(status='needs_data',issues=('temperature/state/geometry/property basis and objective shape on every partition must be reviewed',))
    cache={}
    class UnknownState(Exception):pass
    class Unsupported(Exception):pass
    def at(temperature):
        temperature=float(temperature)
        if temperature in cache:return cache[temperature]
        try:raw=property_provider(temperature)
        except Exception as error:
            raise UnknownState(f'property provider unavailable at {temperature:g} K: {type(error).__name__}') from error
        if raw is None:raise UnknownState('property provider returned unknown state')
        state=SuctionTemperatureProperties.model_validate(raw.model_dump(mode='python') if isinstance(raw,SuctionTemperatureProperties) else raw)
        if state.phase=='unknown' or state.provider_basis_id is None or any(getattr(state,n) is None for n in ('density_kg_m3','dynamic_viscosity_pa_s','vapor_pressure_pa_abs')):
            raise UnknownState('complete liquid rho/viscosity/vapor-pressure state and provider basis are required at every trial')
        if state.temperature_K!=temperature or state.property_reference_pressure_pa_abs!=q.reservoir_surface_pressure_pa_abs or state.provider_basis_id!=q.property_provider_basis_id:
            raise Unsupported('provider changed the requested temperature/reference pressure or property basis')
        if state.phase!='liquid' or state.vapor_pressure_pa_abs>=q.reservoir_surface_pressure_pa_abs:
            raise Unsupported('trial reservoir state leaves the reviewed subcooled-liquid property domain')
        from .models.hydraulics import LiquidHeadLoss,liquid_head_loss
        from .models.equipment import PumpNPSH,pump_npsh
        nu=state.dynamic_viscosity_pa_s/state.density_kg_m3
        re=4*q.fixed_actual_volume_flow_m3_s/(math.pi*q.suction_diameter_m*nu)
        if not math.isfinite(re) or not (0<re<2040. or re>4000.):
            raise Unsupported('search trial leaves a supported laminar or fully turbulent correlation domain')
        loss=liquid_head_loss(LiquidHeadLoss(diameter_m=q.suction_diameter_m,length_m=q.straight_suction_length_m,
            roughness_m=q.absolute_roughness_m,volume_flow_m3_s=q.fixed_actual_volume_flow_m3_s,
            minor_loss_coefficient=q.fixed_minor_loss_coefficient,kinematic_viscosity_m2_s=nu,
            gravity_m_s2=q.gravity_m_s2,friction_method=q.friction_method))
        if loss.status!='computed':raise Unsupported('native suction-loss model unavailable: '+str(loss.notes))
        value=pump_npsh(PumpNPSH(surface_pressure_pa_abs=q.reservoir_surface_pressure_pa_abs,
            vapour_pressure_pa_abs=state.vapor_pressure_pa_abs,density_kg_m3=state.density_kg_m3,
            surface_above_pump_m=q.reservoir_surface_above_pump_m,suction_head_loss_m=loss.values['dissipative_head_loss_m'],
            vendor_required_npsh_m=0.,gravity_m_s2=q.gravity_m_s2))
        # Zero above is a neutral bookkeeping argument of the shared owner;
        # no vendor recommendation is inferred or included in the objective.
        evaluation=NPSHTemperatureEvaluation(properties=state,available_npsh_m=value.values['available_npsh_m'],
            suction_head_loss_m=loss.values['dissipative_head_loss_m'],
            pressure_above_vapor_head_m=(q.reservoir_surface_pressure_pa_abs-state.vapor_pressure_pa_abs)/(state.density_kg_m3*q.gravity_m_s2),
            reynolds=re)
        cache[temperature]=evaluation
        return evaluation
    try:
        from scipy.optimize import minimize_scalar
        candidates=[at(t) for t in q.temperature_partitions_K]
        for (lower,upper),shape in zip(zip(q.temperature_partitions_K,q.temperature_partitions_K[1:]),q.objective_partition_shapes):
            if shape=='unimodal_minimum':
                # Optimize a normalized coordinate so the numerical stopping
                # scale depends on the interval width rather than the large
                # absolute Kelvin origin.
                width=upper-lower
                optimum=minimize_scalar(lambda u:at(lower+u*width).available_npsh_m,bounds=(0.,1.),method='bounded',
                    options=dict(xatol=q.temperature_absolute_tolerance_K/width,maxiter=q.maximum_iterations_per_partition))
                if not optimum.success:raise Unsupported('bounded temperature minimizer did not converge')
                candidates.append(at(lower+optimum.x*width))
        best=min(candidates,key=lambda e:(e.available_npsh_m,e.properties.temperature_K))
        values=dict(status='computed',minimum_temperature_K=best.properties.temperature_K,
            minimum_available_npsh_m=best.available_npsh_m,minimum_state=best,property_evaluation_count=len(cache),
            evaluations=tuple(cache[t] for t in sorted(cache)) if q.include_evaluation_trace else ())
        if q.vendor_required_npsh_m is not None or q.additional_required_margin_m is not None:
            if q.vendor_required_npsh_m is None or q.vendor_requirement_and_margin_basis_id is None or not q.vendor_constant_requirement_over_temperature_reviewed:
                values.update(vendor_comparison_status='needs_data',issues=('available-head minimum is retained; vendor requirement and temperature applicability are unresolved',))
            else:
                margin=best.available_npsh_m-q.vendor_required_npsh_m
                values.update(vendor_comparison_status='computed',available_minus_vendor_requirement_m=margin,
                    residual_against_supplied_additional_margin_m=None if q.additional_required_margin_m is None else margin-q.additional_required_margin_m)
        return NPSHTemperatureSearchResult(**values)
    except UnknownState as error:
        return NPSHTemperatureSearchResult(status='needs_data',property_evaluation_count=len(cache),issues=(str(error),))
    except (Unsupported,ArithmeticError,ValueError) as error:
        return NPSHTemperatureSearchResult(status='out_of_scope',property_evaluation_count=len(cache),issues=(str(error) or 'temperature/property/head arithmetic exceeds finite support',))
