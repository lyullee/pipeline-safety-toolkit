"""Caller-owned centrifugal blower maps in reduced similarity coordinates.

The table stores pressure ratio and power divided by inlet density at nominal
shaft speed. It is distinct from an isentropic-efficiency compressor map.
No manufacturer curve, gas composition, or map extrapolation is supplied.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive, RunContext, digest
from .datasets import file_digest


class BlowerReducedMap(Contract):
    id: Name
    material_basis_id: Name
    evidence_id: Name
    nominal_angular_speed_rad_s: Positive
    reference_inlet_pressure_Pa_abs: Positive
    reference_inlet_density_kg_m3: Positive
    reduced_speed_ratios: tuple[Positive, ...] = Field(min_length=2, max_length=100)
    reduced_volume_flows_m3_s: tuple[Positive, ...] = Field(min_length=2, max_length=500)
    pressure_ratios: tuple[tuple[Positive | None, ...], ...]
    density_specific_powers_W_m3_kg: tuple[tuple[Positive | None, ...], ...]

    @model_validator(mode="after")
    def grid(self):
        for axis in (self.reduced_speed_ratios, self.reduced_volume_flows_m3_s):
            if any(b <= a for a,b in zip(axis,axis[1:])):
                raise ValueError("blower map axes must strictly increase")
        shape=(len(self.reduced_speed_ratios),len(self.reduced_volume_flows_m3_s))
        for table in (self.pressure_ratios,self.density_specific_powers_W_m3_kg):
            if len(table)!=shape[0] or any(len(row)!=shape[1] for row in table):
                raise ValueError("blower map tables must follow speed rows and volume-flow columns")
        if any(v is not None and v <= 1 for row in self.pressure_ratios for v in row):
            raise ValueError("blower map pressure ratios must exceed one")
        return self


class BlowerSimilarityRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    performance_map: BlowerReducedMap
    material_basis_id: Name
    inlet_pressure_Pa_abs: Positive
    inlet_density_kg_m3: Positive
    mass_flow_kg_s: Positive
    angular_speed_rad_s: Positive
    same_geometry_material_and_similarity_domain_reviewed: bool = False


class BlowerSpeedRequest(BlowerSimilarityRequest):
    angular_speed_rad_s: None = None
    target_pressure_ratio: float = Field(gt=1)
    angular_speed_bracket_rad_s: tuple[Positive,Positive]
    absolute_speed_tolerance_rad_s: Positive = 1e-8
    absolute_pressure_ratio_tolerance: Positive = 1e-8
    maximum_evaluations: int = Field(default=80,ge=5,le=1000)

    @model_validator(mode="after")
    def bracket(self):
        if self.angular_speed_bracket_rad_s[0]>=self.angular_speed_bracket_rad_s[1]:
            raise ValueError("blower speed bracket must increase")
        return self


class _UnsupportedMap(Exception):
    pass


def evaluate_blower_similarity(request: BlowerSimilarityRequest, context: RunContext):
    """Evaluate a supplied map or invert one reviewed speed bracket.

    Positive pressure ratio and mapped shaft power are independent supplied
    observations. No efficiency, outlet temperature or surge margin is inferred.
    """
    import numpy as np
    from scipy.interpolate import RegularGridInterpolator
    from scipy.optimize import brentq

    inverse=isinstance(request,BlowerSpeedRequest)
    request_type=BlowerSpeedRequest if inverse else BlowerSimilarityRequest
    q=request_type.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("blower asset/clock differs from context")
    if not q.same_geometry_material_and_similarity_domain_reviewed:
        raise ValueError("blower geometry, material and similarity applicability require review")
    if q.material_basis_id!=q.performance_map.material_basis_id:
        raise ValueError("blower map and observed state material bases differ")
    evidence=[e.id for e in c.evidence]
    if evidence.count(q.performance_map.evidence_id)!=1:
        raise ValueError("blower map evidence is absent or ambiguous")
    mp=q.performance_map
    axes=(mp.reduced_speed_ratios,mp.reduced_volume_flows_m3_s)
    # Interpolation of an incomplete cell must remain unknown, never zero.
    tables=[np.array([[np.nan if v is None else v for v in row] for row in table])
            for table in (mp.pressure_ratios,mp.density_specific_powers_W_m3_kg)]
    interpolators=[RegularGridInterpolator(axes,t,bounds_error=True) for t in tables]
    log_correction=0.5*(math.log(mp.reference_inlet_pressure_Pa_abs)
        -math.log(mp.reference_inlet_density_kg_m3)-math.log(q.inlet_pressure_Pa_abs)
        +math.log(q.inlet_density_kg_m3))
    try:
        correction=math.exp(log_correction)
    except OverflowError as error:
        raise ValueError("blower similarity state is outside finite arithmetic") from error
    if not math.isfinite(correction) or correction<=0:
        raise ValueError("blower similarity state is outside finite arithmetic")
    trials=[]

    def trial(speed):
        if inverse and len(trials)>=q.maximum_evaluations:
            raise _UnsupportedMap("blower speed-search evaluation budget exhausted")
        ratio=speed/mp.nominal_angular_speed_rad_s
        if not math.isfinite(ratio) or ratio<=0:
            raise _UnsupportedMap("blower speed ratio is outside finite positive arithmetic")
        reduced_speed=ratio*correction
        reduced_flow=(q.mass_flow_kg_s/q.inlet_density_kg_m3)/ratio
        point=(reduced_speed,reduced_flow)
        if any(not axis[0]<=value<=axis[-1] for axis,value in zip(axes,point)):
            raise _UnsupportedMap("blower trial leaves the supplied map; extrapolation is forbidden")
        values=[float(f([point])[0]) for f in interpolators]
        if not all(math.isfinite(v) for v in values):
            raise _UnsupportedMap("blower interpolation cell contains missing map values")
        pressure_ratio,specific_power=values
        power=q.inlet_density_kg_m3*ratio**3*specific_power
        outlet=q.inlet_pressure_Pa_abs*pressure_ratio
        if not math.isfinite(power) or power<=0 or not math.isfinite(outlet):
            raise _UnsupportedMap("blower power/pressure exceeds finite positive arithmetic")
        result=dict(angular_speed_rad_s=float(speed),actual_speed_ratio=ratio,
            reduced_speed_ratio=reduced_speed,reduced_volume_flow_m3_s=reduced_flow,
            pressure_ratio=pressure_ratio,outlet_pressure_Pa_abs=outlet,
            shaft_power_W=power,density_specific_reference_power_W_m3_kg=specific_power,
            mapped_specific_shaft_work_J_kg=power/q.mass_flow_kg_s)
        trials.append(result)
        return result

    final=None
    issue=None
    try:
        if inverse:
            lo,hi=q.angular_speed_bracket_rad_s
            left,right=trial(lo),trial(hi)
            fl=left["pressure_ratio"]-q.target_pressure_ratio
            fr=right["pressure_ratio"]-q.target_pressure_ratio
            if abs(fl)<=q.absolute_pressure_ratio_tolerance:
                root=lo
            elif abs(fr)<=q.absolute_pressure_ratio_tolerance:
                root=hi
            elif (fl>0)==(fr>0):
                raise _UnsupportedMap("selected speed bracket does not enclose the target pressure ratio")
            else:
                root=brentq(lambda speed:trial(speed)["pressure_ratio"]-q.target_pressure_ratio,
                    lo,hi,xtol=q.absolute_speed_tolerance_rad_s,rtol=1e-12,maxiter=q.maximum_evaluations-3)
            final=trial(root)
            if abs(final["pressure_ratio"]-q.target_pressure_ratio)>q.absolute_pressure_ratio_tolerance:
                raise _UnsupportedMap("blower speed root fails the requested pressure-ratio tolerance")
        else:
            final=trial(q.angular_speed_rad_s)
    except (_UnsupportedMap,ValueError,RuntimeError,OverflowError) as error:
        issue=str(error)
        final=None
    body=dict(schema="pipeline_integrity.blower_similarity.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="computed_diagnostic" if final is not None else "withheld",
        values=final,trials=trials,issue=issue,
        global_uniqueness_proved=False,outlet_temperature_K=None,isentropic_efficiency=None,
        surge_dynamics_or_margins_inferred=False,field_map_accuracy_claim=False,
        implementation_sha256=file_digest(Path(__file__)))
    body["record_digest"]=digest(body)
    return body


def verify_blower_similarity_record(record,*,request=None,context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("blower receipt changed")
    cls=BlowerSpeedRequest if "target_pressure_ratio" in record["request"] else BlowerSimilarityRequest
    q=cls.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("blower caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("blower caller context differs")
    if evaluate_blower_similarity(q,c)!=record:
        raise ValueError("blower replay differs")
    return True
