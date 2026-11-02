"""Prescribed rainfall and drain-capacity depth inventory with a dry boundary."""
from __future__ import annotations
import math
from bisect import bisect_left
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive,Calculation
from .linear_running_envelope import piecewise_linear_running_max


class RainfallInventoryRequest(Contract):
    id: Name
    time_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=100001)
    rain_depth_rate_m_s: tuple[Nonnegative,...]
    drain_capacity_depth_rate_m_s: tuple[Nonnegative,...]
    footprint_area_m2: Positive
    initial_water_depth_m: Nonnegative = 0.
    water_density_kg_m3: Positive | None = None
    gravity_m_s2: Positive | None = None
    observation_times_s: tuple[Nonnegative,...] = Field(default=(),max_length=100001)
    uniform_footprint_prescribed_rates_and_dry_boundary_reviewed: bool = False

    @model_validator(mode='after')
    def grids(self):
        if any(b<=a for a,b in zip(self.time_s,self.time_s[1:])):raise ValueError('strictly increasing interval edges required')
        if len(self.rain_depth_rate_m_s)!=len(self.time_s)-1 or len(self.drain_capacity_depth_rate_m_s)!=len(self.time_s)-1:
            raise ValueError('one rain and capacity rate per half-open interval required')
        if any(t<self.time_s[0] or t>self.time_s[-1] for t in self.observation_times_s):raise ValueError('observation outside prescribed clock support')
        if self.gravity_m_s2 is not None and self.water_density_kg_m3 is None:raise ValueError('pressure/weight requires supplied density')
        return self


def evaluate_rainfall_inventory(q:RainfallInventoryRequest)->Calculation:
    """Reflect integrated net depth exactly at zero using a shared envelope.

    Drain capacity is an imposed depth rate, not a head-dependent prediction.
    Unused capacity has no outflow; rain may flow straight through a dry roof.
    No evaporation, infiltration, overtopping, variable footprint, roof tilt,
    structural failure or rainfall return-period statistics are calculated.
    """
    q=RainfallInventoryRequest.model_validate(q.model_dump(mode='python'))
    if not q.uniform_footprint_prescribed_rates_and_dry_boundary_reviewed:
        return Calculation(status='needs_data',values={},notes=('prescribed rates, uniform footprint and dry-boundary meaning require review',))
    t=q.time_s;rain=[0.];cap=[0.];x=[q.initial_water_depth_m]
    for a,b,r,d in zip(t,t[1:],q.rain_depth_rate_m_s,q.drain_capacity_depth_rate_m_s):
        rain.append(math.fsum((rain[-1],r*(b-a))));cap.append(math.fsum((cap[-1],d*(b-a))))
        x.append(math.fsum((x[-1],(r-d)*(b-a))))
    if not all(math.isfinite(v) for v in (*rain,*cap,*x)):raise ValueError('integrated inventory exceeds finite support')
    envelope=piecewise_linear_running_max(t,[-v for v in x],0.)
    pieces=[];events=[]
    for index,p in enumerate(envelope['pieces']):
        i=p['source_interval'];r=q.rain_depth_rate_m_s[i];d=q.drain_capacity_depth_rate_m_s[i];slope=r-d
        xa=x[i]+slope*(p['start']-t[i]);xb=x[i]+slope*(p['end']-t[i])
        ha=0. if p['tracking_input'] else xa+p['level_start'];hb=0. if p['tracking_input'] else xb+p['level_end']
        # A neighboring tracking piece certifies an exact dry crossing, even
        # when evaluating X at its rounded event time leaves a few residual ulps.
        if index and envelope['pieces'][index-1]['tracking_input']:ha=0.
        if index+1<len(envelope['pieces']) and envelope['pieces'][index+1]['tracking_input']:hb=0.
        tol=32*max(math.ulp(v) for v in (xa,xb,p['level_start'],p['level_end']))
        if min(ha,hb)<-tol:raise ValueError('dry-boundary depth exceeds rounding support')
        ha=max(0.,ha);hb=max(0.,hb)
        unused=p['level_end']-p['level_start'];drained=d*(p['end']-p['start'])-unused
        if drained<-tol:raise ValueError('negative actual drainage exceeds rounding support')
        drained=max(0.,drained)
        pieces.append(dict(source_interval=i,start_time_s=p['start'],end_time_s=p['end'],start_depth_m=ha,end_depth_m=hb,
            rain_depth_m=r*(p['end']-p['start']),capacity_depth_m=d*(p['end']-p['start']),
            unmet_drawdown_depth_m=unused,actual_drained_depth_m=drained,dry_throughout=ha==0. and hb==0.))
        if ha==0. and hb>0.:events.append(dict(kind='dry_to_wet',time_s=p['start']))
        if ha>0. and hb==0.:events.append(dict(kind='wet_to_dry',time_s=p['end']))
    piece_ends=[p['end_time_s'] for p in pieces]
    def observe(time):
        p=pieces[bisect_left(piece_ends,time)]
        h=p['start_depth_m']+(p['end_depth_m']-p['start_depth_m'])*(time-p['start_time_s'])/(p['end_time_s']-p['start_time_s'])
        volume=h*q.footprint_area_m2
        mass=None if q.water_density_kg_m3 is None else volume*q.water_density_kg_m3
        pressure=None if q.gravity_m_s2 is None else h*q.water_density_kg_m3*q.gravity_m_s2
        return dict(time_s=time,depth_m=h,volume_m3=volume,mass_kg=mass,bottom_hydrostatic_pressure_pa=pressure)
    final=observe(t[-1]);unused=envelope['maximum_values'][-1]
    actual=math.fsum(p['actual_drained_depth_m'] for p in pieces)
    residual=math.fsum((q.initial_water_depth_m,rain[-1],-actual,-final['depth_m']))
    maximum=max([q.initial_water_depth_m,*[p['end_depth_m'] for p in pieces]])
    return Calculation(values=dict(pieces=pieces,events=events,observations=[observe(v) for v in q.observation_times_s],
        final=final,maximum_depth_m=maximum,maximum_volume_m3=maximum*q.footprint_area_m2,
        cumulative_rain_depth_m=rain[-1],cumulative_drain_capacity_depth_m=cap[-1],cumulative_unmet_drawdown_depth_m=unused,
        cumulative_actual_drained_depth_m=actual,cumulative_actual_drained_volume_m3=actual*q.footprint_area_m2,
        depth_budget_residual_m=residual,volume_budget_residual_m3=residual*q.footprint_area_m2,
        drain_head_law_or_roof_failure_qualified=False))
