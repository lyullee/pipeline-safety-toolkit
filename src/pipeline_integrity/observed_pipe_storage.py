"""Inventory of a fixed observed pipe section using injected state/area owners.

Spatial integration of rho*A and optional material chain-rule rates is an
observation operator. It does not advance pressure, velocity, wall temperature
or soil fields. Time-window changes reuse the same observed endpoint inventory.
"""
from __future__ import annotations
import hashlib,inspect,json,math
from pathlib import Path
from typing import Literal,Protocol
import numpy as np
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive,RunContext,SignalFrame,digest
from .quadrature import integrate_linear_interval
from .mass_balance_section import SectionMassBalanceRequest,evaluate_section_mass_balance
from .linearized_liquid_density import LinearizedLiquidDensityLaw,evaluate_linearized_liquid_density
from .pipe_thermoelastic_area import (ThermoelasticAreaRequest,ClosedEndThermoelasticRequest,
    evaluate_thermoelastic_area,evaluate_closed_end_thermoelastic_geometry)
from .thermo import StateRequest,CoolPropProvider

Phase=Literal['liquid','gas','supercritical','supercritical_gas','supercritical_liquid']


class ObservedDensityState(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_pa_abs: Positive
    temperature_K: Positive
    fluid_id: Name
    property_basis_id: Name
    density_kg_m3: Positive | None = None
    phase: str | None = None
    density_pressure_derivative_kg_m3_pa: float | None = None
    density_temperature_derivative_kg_m3_K: float | None = None
    receipt: dict = Field(default_factory=dict)
    issues: tuple[str,...] = ()


class ObservedAreaState(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_pa_abs: Positive
    external_pressure_pa_abs: Positive
    wall_temperature_K: Positive
    geometry_basis_id: Name
    area_m2: Positive | None = None
    area_pressure_derivative_m2_pa: float | None = None
    area_external_pressure_derivative_m2_pa: float | None = None
    area_wall_temperature_derivative_m2_K: float | None = None
    receipt: dict = Field(default_factory=dict)
    issues: tuple[str,...] = ()


class ObservedDensityProvider(Protocol):
    def resolve_density(self,*,position_m:float,pressure_pa_abs:float,temperature_K:float) -> ObservedDensityState: ...


class ObservedAreaProvider(Protocol):
    def resolve_area(self,*,position_m:float,pressure_pa_abs:float,external_pressure_pa_abs:float,wall_temperature_K:float) -> ObservedAreaState: ...


class LinearizedObservedDensityProvider:
    def __init__(self,law:LinearizedLiquidDensityLaw):self.law=LinearizedLiquidDensityLaw.model_validate(law.model_dump(mode='python'))
    def identity(self):return dict(law=self.law.model_dump(mode='json'))
    def resolve_density(self,*,position_m,pressure_pa_abs,temperature_K):
        s=evaluate_linearized_liquid_density(self.law,pressure_pa_abs=pressure_pa_abs,temperature_K=temperature_K)
        return ObservedDensityState(status=s.status,pressure_pa_abs=pressure_pa_abs,temperature_K=temperature_K,
            fluid_id=self.law.fluid_id,property_basis_id=self.law.property_basis_id,density_kg_m3=s.density_kg_m3,
            phase='liquid' if s.status=='computed' else None,density_pressure_derivative_kg_m3_pa=s.density_pressure_derivative_kg_m3_pa,
            density_temperature_derivative_kg_m3_K=s.density_temperature_derivative_kg_m3_K,issues=s.issues,receipt=s.model_dump(mode='json'))


class CoolPropObservedDensityProvider:
    """Reuse the checked state owner; derivatives are optional provider outputs."""
    def __init__(self,fluid,property_basis_id,*,backend='HEOS'):
        self.fluid=fluid;self.property_basis_id=property_basis_id;self.backend=backend;self.owner=CoolPropProvider(use_cache=False)
    def identity(self):return dict(fluid=self.fluid,backend=self.backend,property_basis_id=self.property_basis_id)
    def resolve_density(self,*,position_m,pressure_pa_abs,temperature_K):
        common=dict(pressure_pa_abs=pressure_pa_abs,temperature_K=temperature_K,fluid_id=self.fluid,property_basis_id=self.property_basis_id)
        try:
            state=self.owner.resolve(StateRequest(fluid=self.fluid,backend=self.backend,inputs={'P':pressure_pa_abs,'T':temperature_K}))
        except ValueError as error:return ObservedDensityState(status='out_of_scope',issues=('EOS state unavailable: '+str(error),),**common)
        derivatives=[]
        from CoolProp.CoolProp import PropsSI
        for key in ('d(Dmass)/d(P)|T','d(Dmass)/d(T)|P'):
            try:
                value=float(PropsSI(key,'P',pressure_pa_abs,'T',temperature_K,self.backend+'::'+self.fluid))
                derivatives.append(value if math.isfinite(value) else None)
            except ValueError:derivatives.append(None)
        return ObservedDensityState(status='computed',density_kg_m3=state.density_kg_m3,phase=state.phase,
            density_pressure_derivative_kg_m3_pa=derivatives[0],density_temperature_derivative_kg_m3_K=derivatives[1],
            receipt=state.model_dump(mode='json'),**common)


class ThermoelasticObservedAreaProvider:
    """Existing incremental area owner; fixed spatial length is not expanded again."""
    def __init__(self,template:ThermoelasticAreaRequest | ClosedEndThermoelasticRequest,geometry_basis_id,
        *,reference_pressure_pa_abs,reference_external_pressure_pa_abs):
        self.template=type(template).model_validate(template.model_dump(mode='python'));self.geometry_basis_id=geometry_basis_id
        self.reference_pressure_pa_abs=float(reference_pressure_pa_abs);self.reference_external_pressure_pa_abs=float(reference_external_pressure_pa_abs)
        if not all(math.isfinite(x) and x>0 for x in (self.reference_pressure_pa_abs,self.reference_external_pressure_pa_abs)):raise ValueError('positive absolute area-reference pressures required')
    def identity(self):return dict(template=self.template.model_dump(mode='json'),geometry_basis_id=self.geometry_basis_id,
        reference_pressure_pa_abs=self.reference_pressure_pa_abs,reference_external_pressure_pa_abs=self.reference_external_pressure_pa_abs,fixed_centerline_length_not_axially_expanded=True)
    def resolve_area(self,*,position_m,pressure_pa_abs,external_pressure_pa_abs,wall_temperature_K):
        closed=isinstance(self.template,ClosedEndThermoelasticRequest)
        original=self.template.unconstrained_base if closed else self.template
        dp=(pressure_pa_abs-external_pressure_pa_abs)-(self.reference_pressure_pa_abs-self.reference_external_pressure_pa_abs)
        pressure=original.pressure.model_copy(update={'differential_pressure_increment_pa':dp})
        base=original.model_copy(update={'pressure':pressure,'temperature_K':wall_temperature_K})
        state=evaluate_closed_end_thermoelastic_geometry(self.template.model_copy(update={'unconstrained_base':base})) if closed else evaluate_thermoelastic_area(base)
        common=dict(pressure_pa_abs=pressure_pa_abs,external_pressure_pa_abs=external_pressure_pa_abs,wall_temperature_K=wall_temperature_K,geometry_basis_id=self.geometry_basis_id)
        if state.status!='computed':return ObservedAreaState(status=state.status,issues=state.issues,receipt=state.model_dump(mode='json'),**common)
        value=state.unconstrained_base_result if closed else state
        reference=value.reference_area_m2;diameter=pressure.reference_internal_diameter_m
        area=reference+state.linear_area_increment_m2 if closed else state.linearized_area_m2
        dp_tangent=2*reference*value.pressure_result.diameter_compliance_m_per_pa/diameter
        if closed:dp_tangent*=1-pressure.poisson_ratio/2
        multiplier=1. if closed or pressure.axial_condition=='zero_axial_stress' else 1+pressure.poisson_ratio
        curve=base.thermal_expansion_curve
        # The existing owner defines a continuous piecewise-linear tangent curve.
        alpha=float(np.interp(wall_temperature_K,[k.temperature_K for k in curve.knots],[k.tangent_expansion_per_K for k in curve.knots]))
        if base.coefficient_basis=='free_area_engineering':alpha/=2
        thermal=2*reference*multiplier*alpha
        return ObservedAreaState(status='computed',area_m2=area,area_pressure_derivative_m2_pa=dp_tangent,
            area_external_pressure_derivative_m2_pa=-dp_tangent,area_wall_temperature_derivative_m2_K=thermal,
            receipt=state.model_dump(mode='json'),**common)


class ObservedPipeStation(Contract):
    position_m: float
    pressure_pa_abs: Positive | None = None
    fluid_temperature_K: Positive | None = None
    external_pressure_pa_abs: Positive | None = None
    wall_temperature_K: Positive | None = None
    density_kg_m3: Positive | None = None
    area_m2: Positive | None = None
    pressure_rate_pa_s: float | None = None
    fluid_temperature_rate_K_s: float | None = None
    external_pressure_rate_pa_s: float | None = None
    wall_temperature_rate_K_s: float | None = None
    density_rate_kg_m3_s: float | None = None
    area_rate_m2_s: float | None = None
    valid: bool = True


class ObservedPipeProfile(Contract):
    time_s: Nonnegative
    stations: tuple[ObservedPipeStation,...] = Field(min_length=2,max_length=100000)
    @model_validator(mode='after')
    def ordered(self):
        if any(v.position_m<=u.position_m for u,v in zip(self.stations,self.stations[1:])):raise ValueError('fixed spatial stations must increase strictly without sorting')
        return self


class ObservedPipeStorageRequest(Contract):
    asset_id: Name
    clock_id: Name
    stream_id: Name
    fluid_id: Name
    property_basis_id: Name
    geometry_basis_id: Name
    evidence_id: Name
    origin: Literal['measured','laboratory','simulation','synthetic']
    profiles: tuple[ObservedPipeProfile,...] = Field(min_length=2,max_length=100000)
    density_mode: Literal['observed','provider']
    area_mode: Literal['observed','provider']
    spatial_quadrature: Literal['trapezoid_mass_per_length','gauss2_linear_state'] = 'gauss2_linear_state'
    rate_mode: Literal['none','supplied_material_rates','state_chain_rule'] = 'none'
    allowed_phases: tuple[Phase,...] = ('liquid',)
    fixed_section_and_observed_interpolation_reviewed: bool = False
    maximum_evaluations: int = Field(default=1000000,ge=1,le=10000000)
    retain_state_receipts: bool = False
    @model_validator(mode='after')
    def fixed_space_time(self):
        if any(v.time_s<=u.time_s for u,v in zip(self.profiles,self.profiles[1:])):raise ValueError('observation times must increase strictly without clock repair')
        bounds=(self.profiles[0].stations[0].position_m,self.profiles[0].stations[-1].position_m)
        if not self.allowed_phases:raise ValueError('explicit allowed single-phase support required')
        if any((p.stations[0].position_m,p.stations[-1].position_m)!=bounds for p in self.profiles):raise ValueError('all observed meshes must cover exactly the same fixed spatial control section')
        evaluations=sum(2*(len(p.stations)-1) for p in self.profiles)
        if evaluations>self.maximum_evaluations:raise ValueError('observed state evaluation budget exceeded')
        return self


class ObservedPipeStorageResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    asset_id: Name
    clock_id: Name
    inventory_frames: tuple[SignalFrame,...]
    rate_frames: tuple[SignalFrame,...]
    interval_storage_changes: tuple[dict,...]
    profile_diagnostics: tuple[dict,...]
    specification_digest: Name
    context_digest: Name
    adapter_source_digest: Name
    provider_identities: dict
    result_digest: Name
    future_field_predicted: Literal[False] = False
    def check_integrity(self):return digest(self.model_dump(mode='json',exclude={'result_digest'}))==self.result_digest


def _provider_identity(provider):
    if provider is None:return None
    cls=type(provider)
    try:path=inspect.getsourcefile(cls)
    except TypeError:path=None
    return dict(provider_class=cls.__module__+'.'+cls.__qualname__,configuration=provider.identity() if callable(getattr(provider,'identity',None)) else None,
        source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None)


def evaluate_observed_pipe_storage(request:ObservedPipeStorageRequest,context:RunContext,*,density_provider=None,area_provider=None):
    """Integrate observed rho*A with declared spatial interpolation and SI inputs.

    Gauss2 interpolates the supplied state coordinates inside each cell, then
    evaluates state owners. Trapezoid instead interpolates mass/length between
    observed nodes. Adjacent inventory differences are exact sampled-window
    changes, not a numerical estimate of an instantaneous derivative. Optional
    rates require supplied material rates or explicit P/T rates and owner
    tangents; no rates, clocks, missing states or ambient pressure are invented.
    """
    q=ObservedPipeStorageRequest.model_validate(request.model_dump(mode='python'));c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id,q.origin)!=(c.asset_id,c.clock_id,c.origin) or q.evidence_id not in {e.id for e in c.evidence}:raise ValueError('observed asset, clock, origin and evidence must match context')
    identities=dict(density=_provider_identity(density_provider),area=_provider_identity(area_provider))
    inventory=[];rates=[];diagnostics=[];statuses=[]
    def frame(time,value,channel,unit):return SignalFrame(stream_id=q.stream_id,clock_id=q.clock_id,time_s=time,values={channel:value},units={channel:unit},valid={channel:value is not None},origin=q.origin)
    for profile in q.profiles:
        issues=[];values=[];rate_values=[];receipt_hash=hashlib.sha256();receipts=[];profile_status='computed'
        def evaluate(station):
            nonlocal profile_status
            if not q.fixed_section_and_observed_interpolation_reviewed or not station.valid:
                raise _Missing('needs_data','fixed-section/interpolation review or station quality unavailable')
            density=area=None;ds=ars=None
            if q.density_mode=='observed':density=station.density_kg_m3
            else:
                if density_provider is None or station.pressure_pa_abs is None or station.fluid_temperature_K is None:raise _Missing('needs_data','density owner requires absolute pressure and fluid temperature')
                ds=ObservedDensityState.model_validate(density_provider.resolve_density(position_m=station.position_m,pressure_pa_abs=station.pressure_pa_abs,temperature_K=station.fluid_temperature_K).model_dump(mode='python'))
                if (ds.fluid_id,ds.property_basis_id)!=(q.fluid_id,q.property_basis_id):raise ValueError('density owner changed fluid/property identity')
                if ds.pressure_pa_abs!=station.pressure_pa_abs or ds.temperature_K!=station.fluid_temperature_K:raise ValueError('density owner changed requested state coordinates')
                if ds.status!='computed':raise _Missing(ds.status,'; '.join(ds.issues))
                if ds.phase not in q.allowed_phases:raise _Missing('out_of_scope','density owner phase is outside declared single-phase support')
                density=ds.density_kg_m3
            if q.area_mode=='observed':area=station.area_m2
            else:
                if area_provider is None or any(v is None for v in (station.pressure_pa_abs,station.external_pressure_pa_abs,station.wall_temperature_K)):raise _Missing('needs_data','area owner requires internal/external absolute pressure and wall temperature')
                ars=ObservedAreaState.model_validate(area_provider.resolve_area(position_m=station.position_m,pressure_pa_abs=station.pressure_pa_abs,external_pressure_pa_abs=station.external_pressure_pa_abs,wall_temperature_K=station.wall_temperature_K).model_dump(mode='python'))
                if ars.geometry_basis_id!=q.geometry_basis_id:raise ValueError('area owner changed geometry identity')
                if (ars.pressure_pa_abs,ars.external_pressure_pa_abs,ars.wall_temperature_K)!=(station.pressure_pa_abs,station.external_pressure_pa_abs,station.wall_temperature_K):raise ValueError('area owner changed requested state coordinates')
                if ars.status!='computed':raise _Missing(ars.status,'; '.join(ars.issues))
                area=ars.area_m2
            if density is None or area is None:raise _Missing('needs_data','observed density or occupied area is missing')
            rate=None
            if q.rate_mode=='supplied_material_rates':
                if station.density_rate_kg_m3_s is not None and station.area_rate_m2_s is not None:rate=area*station.density_rate_kg_m3_s+density*station.area_rate_m2_s
            elif q.rate_mode=='state_chain_rule' and ds is not None and ars is not None:
                terms=(ds.density_pressure_derivative_kg_m3_pa,ds.density_temperature_derivative_kg_m3_K,
                    ars.area_pressure_derivative_m2_pa,ars.area_external_pressure_derivative_m2_pa,ars.area_wall_temperature_derivative_m2_K,
                    station.pressure_rate_pa_s,station.fluid_temperature_rate_K_s,station.external_pressure_rate_pa_s,station.wall_temperature_rate_K_s)
                if all(v is not None for v in terms):
                    rp,rt,ap,ae,at,pd,td,ed,wd=terms
                    rate=area*(rp*pd+rt*td)+density*(ap*pd+ae*ed+at*wd)
            item=dict(position_m=station.position_m,density_kg_m3=density,area_m2=area,density_state=None if ds is None else ds.model_dump(mode='json'),area_state=None if ars is None else ars.model_dump(mode='json'))
            receipt_hash.update(json.dumps(item,sort_keys=True,allow_nan=False,separators=(',',':')).encode())
            if q.retain_state_receipts:receipts.append(item)
            return density*area,rate
        for first,last in zip(profile.stations,profile.stations[1:]):
            dx=last.position_m-first.position_m
            fractions=(0.,1.) if q.spatial_quadrature=='trapezoid_mass_per_length' else ((1-1/math.sqrt(3))/2,(1+1/math.sqrt(3))/2)
            local=[]
            try:
                for fraction in fractions:
                    data={}
                    for key in type(first).model_fields:
                        if key=='valid':data[key]=first.valid and last.valid
                        else:
                            lo,hi=getattr(first,key),getattr(last,key)
                            data[key]=None if lo is None or hi is None else lo+(hi-lo)*fraction
                    local.append(evaluate(ObservedPipeStation.model_validate(data)))
                values.append(integrate_linear_interval(local[0][0],local[1][0],dx)[0])
                if all(v[1] is not None for v in local):
                    positive,negative=integrate_linear_interval(local[0][1],local[1][1],dx);rate_values.append(positive-negative)
                else:rate_values.append(None)
            except _Missing as error:
                profile_status='out_of_scope' if error.status=='out_of_scope' else profile_status if profile_status=='out_of_scope' else 'needs_data'
                if len(issues)<100:issues.append(str(error))
                values.append(None);rate_values.append(None)
        mass=math.fsum(values) if all(v is not None for v in values) else None
        rate=math.fsum(rate_values) if q.rate_mode!='none' and all(v is not None for v in rate_values) else None
        if q.rate_mode!='none' and rate is None:
            issues.append('requested rate unavailable: explicit rates/owner tangents are missing')
            if profile_status=='computed':profile_status='needs_data'
        if any(v is not None and not math.isfinite(v) for v in (mass,rate)):raise ValueError('observed storage exceeds finite support')
        inventory.append(frame(profile.time_s,mass,'inventory_mass_kg','kg'))
        if q.rate_mode!='none':rates.append(frame(profile.time_s,rate,'storage_rate_kg_s','kg/s'))
        diagnostics.append(dict(time_s=profile.time_s,status=profile_status,issues=issues,state_receipt_sha256=receipt_hash.hexdigest(),retained_states=receipts if q.retain_state_receipts else None))
        statuses.append(profile_status)
    intervals=[]
    for index,(first,last) in enumerate(zip(inventory,inventory[1:])):
        lo,hi=first.values['inventory_mass_kg'],last.values['inventory_mass_kg'];change=None if lo is None or hi is None else hi-lo
        elapsed=last.time_s-first.time_s;integrated_rate=None
        if rates:
            r0,r1=rates[index].values['storage_rate_kg_s'],rates[index+1].values['storage_rate_kg_s']
            if r0 is not None and r1 is not None:
                positive,negative=integrate_linear_interval(r0,r1,elapsed);integrated_rate=positive-negative
        intervals.append(dict(start_s=first.time_s,finish_s=last.time_s,inventory_change_kg=change,mean_storage_rate_kg_s=None if change is None else change/elapsed,
            integrated_sampled_storage_rate_kg=integrated_rate,
            sampled_rate_vs_inventory_change_kg=None if change is None or integrated_rate is None else change-integrated_rate,
            rate_time_interpolation='point_linear_diagnostic;may_differ_from_exact_endpoint_change'))
    status='out_of_scope' if 'out_of_scope' in statuses else 'needs_data' if 'needs_data' in statuses else 'computed'
    data=dict(status=status,asset_id=q.asset_id,clock_id=q.clock_id,inventory_frames=tuple(inventory),rate_frames=tuple(rates),interval_storage_changes=tuple(intervals),profile_diagnostics=tuple(diagnostics),specification_digest=digest(q),context_digest=digest(c),adapter_source_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),provider_identities=identities,future_field_predicted=False)
    data['result_digest']=digest(data)
    return ObservedPipeStorageResult(**data)


class _Missing(Exception):
    def __init__(self,status,message):super().__init__(message);self.status=status


def balance_observed_storage_window(result:ObservedPipeStorageResult,*,clock_id,window_start_s,window_finish_s,inlet_masses_kg,outlet_masses_kg,common_window_mass_basis_and_boundary_reviewed=False):
    """Reuse section balance with exact sampled endpoints and integrated port masses.

    Endpoint interpolation/extrapolation is not manufactured. Existing flow
    totalization may provide port masses; rates in kg/s are not mass inputs here.
    """
    if not result.check_integrity() or result.clock_id!=clock_id:raise ValueError('observed storage integrity/clock mismatch')
    frames={frame.time_s:frame for frame in result.inventory_frames}
    def value(time):
        f=frames.get(time)
        return None if f is None or not f.valid['inventory_mass_kg'] else f.values['inventory_mass_kg']
    return evaluate_section_mass_balance(SectionMassBalanceRequest(clock_id=clock_id,window_start_s=window_start_s,window_finish_s=window_finish_s,
        inlet_masses_kg=inlet_masses_kg,outlet_masses_kg=outlet_masses_kg,initial_inventory_mass_kg=value(window_start_s),final_inventory_mass_kg=value(window_finish_s),
        common_window_mass_basis_and_boundary_reviewed=common_window_mass_basis_and_boundary_reviewed))
