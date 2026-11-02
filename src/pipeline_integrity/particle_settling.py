"""Dilute spherical-particle settling using the original Fluids drag closure.

An absorbing collector sits outside the modeled column. Exact translation of
piecewise-constant initial concentrations avoids numerical advection diffusion.
Carrier motion, hindered settling, resuspension and bed feedback are excluded.
"""
from __future__ import annotations

import bisect
import inspect
import json
import math
from importlib.metadata import version
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Positive, Nonnegative, RunContext, SignalFrame, digest, require_context_extension
from .datasets import file_digest


class SettlingParticleClass(Contract):
    id: Name
    diameter_m: Positive
    density_kg_m3: Positive
    initial_concentration_kg_m3: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    evidence_id: Name


class ParticleSettlingRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    start_time_s: Nonnegative = 0.0
    column_area_m2: Positive
    height_edges_m: tuple[Nonnegative,...] = Field(min_length=2,max_length=10001)
    carrier_density_kg_m3: Positive
    carrier_viscosity_Pa_s: Positive
    particles: tuple[SettlingParticleClass,...] = Field(min_length=1,max_length=200)
    elapsed_sample_time_s: tuple[Nonnegative,...] = Field(min_length=2,max_length=10000)
    drag_method: Name | None = None
    maximum_reviewed_solids_volume_fraction: float = Field(gt=0,le=0.05)
    dilute_noninteracting_spheres_quiescent_newtonian_carrier_reviewed: bool = False
    terminal_regime_and_external_absorbing_collector_reviewed: bool = False
    geometry_and_carrier_evidence_ids: tuple[Name,...] = Field(min_length=1)
    absolute_mass_tolerance_kg: Positive = 1e-8
    relative_mass_tolerance: float = Field(default=1e-9,ge=0,le=1e-3)
    absolute_terminal_force_tolerance_N: Nonnegative = 1e-12
    relative_terminal_force_tolerance: float = Field(default=1e-7,ge=0,le=0.01)
    maximum_profile_values: int = Field(default=1000000,ge=1,le=10000000)
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=1000000000)

    @model_validator(mode="after")
    def layout(self):
        if self.height_edges_m[0]!=0 or any(b<=a for a,b in zip(self.height_edges_m,self.height_edges_m[1:])):
            raise ValueError("settling heights increase from the bottom collector at zero")
        if self.elapsed_sample_time_s[0]!=0 or any(b<=a for a,b in zip(self.elapsed_sample_time_s,self.elapsed_sample_time_s[1:])):
            raise ValueError("settling elapsed sampling starts at zero and strictly increases")
        if len({p.id for p in self.particles})!=len(self.particles):
            raise ValueError("settling particle class IDs must be unique")
        n=len(self.height_edges_m)-1
        if any(len(p.initial_concentration_kg_m3)!=n for p in self.particles):
            raise ValueError("each particle class requires one initial concentration per height cell")
        if n*len(self.particles)*len(self.elapsed_sample_time_s)>self.maximum_profile_values:
            raise ValueError("settling profile exceeds its retained-value budget")
        return self


def evaluate_particle_settling(request: ParticleSettlingRequest, context: RunContext):
    import fluids.drag as drag
    q=ParticleSettlingRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("settling asset/clock differs from context")
    if not q.dilute_noninteracting_spheres_quiescent_newtonian_carrier_reviewed or not q.terminal_regime_and_external_absorbing_collector_reviewed:
        raise ValueError("dilute, terminal-speed, quiescent carrier and absorbing collector reviews required")
    required=set(q.geometry_and_carrier_evidence_ids)|{p.evidence_id for p in q.particles}
    evidence=[e.id for e in c.evidence]
    if len(evidence)!=len(set(evidence)) or not required<=set(evidence):
        raise ValueError("settling material/geometry/drag evidence is absent or ambiguous")
    if any(p.density_kg_m3<=q.carrier_density_kg_m3 for p in q.particles):
        raise ValueError("this collector model requires particles denser than the carrier")
    solids=[math.fsum(p.initial_concentration_kg_m3[i]/p.density_kg_m3 for p in q.particles)
            for i in range(len(q.height_edges_m)-1)]
    if max(solids)>q.maximum_reviewed_solids_volume_fraction:
        raise ValueError("initial concentration exceeds the reviewed dilute support")
    overlap_bound=math.fsum(max(p.initial_concentration_kg_m3)/p.density_kg_m3 for p in q.particles)
    if overlap_bound>q.maximum_reviewed_solids_volume_fraction:
        raise ValueError("possible overlap of translating particle classes exceeds the reviewed dilute support")
    implementation_pin=file_digest(Path(__file__))
    native_path=Path(inspect.getsourcefile(drag.v_terminal))
    native_pin=file_digest(native_path)
    native_paths={name:Path(inspect.getsourcefile(getattr(drag,name))) for name in
                  ("v_terminal","drag_sphere","drag_sphere_methods","secant","Reynolds")}
    native_sources={name:file_digest(path) for name,path in native_paths.items()}
    native_version=version("fluids")
    if drag.g!=9.80665:
        raise ValueError("original drag gravity differs from the declared standard-gravity boundary")
    if q.drag_method is not None and q.drag_method not in drag.drag_sphere_correlations:
        raise ValueError("settling method does not identify an actual original drag closure")
    times=[q.start_time_s+t for t in q.elapsed_sample_time_s]
    if any(not math.isfinite(t) for t in times) or any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError("settling physical sample clock is not representable")
    height=q.height_edges_m[-1]
    output=[]
    for particle in q.particles:
        speed=float(drag.v_terminal(D=particle.diameter_m,rhop=particle.density_kg_m3,
                                   rho=q.carrier_density_kg_m3,mu=q.carrier_viscosity_Pa_s,Method=q.drag_method))
        if not math.isfinite(speed) or speed<=0:
            raise ValueError("original drag closure did not return a positive settling velocity")
        reynolds=q.carrier_density_kg_m3*speed*particle.diameter_m/q.carrier_viscosity_Pa_s
        if not math.isfinite(reynolds) or reynolds<=0:
            raise ValueError("settling particle Reynolds number is not positively representable")
        if q.drag_method is None and reynolds>=1e6:
            raise ValueError("settling velocity lies outside the original automatic drag-correlation support")
        if q.drag_method is not None and q.drag_method not in drag.drag_sphere_methods(reynolds,check_ranges=True):
            raise ValueError("settling Reynolds number lies outside the selected original correlation support")
        drag_coefficient=float(drag.drag_sphere(reynolds,Method=q.drag_method))
        driving_force=(math.pi*particle.diameter_m**3/6)*(particle.density_kg_m3-q.carrier_density_kg_m3)*drag.g
        resisting_force=0.5*q.carrier_density_kg_m3*drag_coefficient*(math.pi*particle.diameter_m**2/4)*speed**2
        force_residual=driving_force-resisting_force
        if not all(math.isfinite(v) for v in (drag_coefficient,driving_force,resisting_force,force_residual)) or min(drag_coefficient,driving_force,resisting_force)<=0:
            raise ValueError("original terminal drag-force audit is not finite and positive")
        if abs(force_residual)>q.absolute_terminal_force_tolerance_N+q.relative_terminal_force_tolerance*max(driving_force,resisting_force):
            raise ValueError("original settling velocity exceeds its terminal force-residual tolerance")
        concentration=particle.initial_concentration_kg_m3
        cumulative=[0.0]
        for a,b,value in zip(q.height_edges_m,q.height_edges_m[1:],concentration):
            cumulative.append(math.fsum((cumulative[-1],(b-a)*value)))
        def integral(z):
            if z<=0:
                return 0.0
            if z>=height:
                return cumulative[-1]
            i=bisect.bisect_right(q.height_edges_m,z)-1
            return cumulative[i]+concentration[i]*(z-q.height_edges_m[i])
        initial=q.column_area_m2*cumulative[-1]
        profiles=[]
        for elapsed,time in zip(q.elapsed_sample_time_s,times):
            displacement=min(height,speed*elapsed)
            captured=q.column_area_m2*integral(displacement)
            values=[]; suspended=[]
            for a,b in zip(q.height_edges_m,q.height_edges_m[1:]):
                amount_per_area=integral(b+displacement)-integral(a+displacement)
                if amount_per_area<0:
                    raise ValueError("translated concentration integral is negative")
                values.append(amount_per_area/(b-a))
                suspended.append(q.column_area_m2*amount_per_area)
            remaining=math.fsum(suspended)
            residual=initial-captured-remaining
            if abs(residual)>q.absolute_mass_tolerance_kg+q.relative_mass_tolerance*initial:
                raise ValueError("settling suspended/collector mass balance exceeded tolerance")
            profiles.append(dict(time_s=time,concentration_kg_m3=values,suspended_mass_kg=remaining,
                                 captured_mass_kg=captured,mass_residual_kg=residual))
        output.append(dict(particle_id=particle.id,terminal_velocity_m_s=speed,
                           particle_Reynolds_number=reynolds,drag_coefficient=drag_coefficient,
                           effective_gravity_force_per_particle_N=driving_force,drag_force_per_particle_N=resisting_force,
                           terminal_force_residual_N=force_residual,
                           initial_mass_kg=initial,states=profiles))
    if file_digest(native_path)!=native_pin:
        raise ValueError("original drag implementation changed during settling calculation")
    if native_sources!={name:file_digest(path) for name,path in native_paths.items()} or version("fluids")!=native_version or file_digest(Path(__file__))!=implementation_pin or drag.g!=9.80665:
        raise ValueError("settling or original drag/core/root source/runtime changed during evaluation")
    body=dict(schema="pipeline_integrity.particle_settling.v1",request=q.model_dump(mode="json"),
              context=c.model_dump(mode="json"),status="computed_diagnostic",particle_classes=output,
              fluids_version=native_version,drag_source_sha256=native_pin,native_source_sha256=native_sources,
              implementation_sha256=implementation_pin,output_origin="simulation",
              method="exact_translation_of_piecewise_constant_initial_profiles",
              initial_dilute_volume_fraction_by_height_cell=solids,
              conservative_all_time_solids_volume_fraction_upper_bound=overlap_bound,
              gravitational_acceleration_m_s2=9.80665,
              bed_geometry_feedback=False,hindered_settling_or_turbulent_suspension=False,
              numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("settling receipt exceeds retained-result byte budget")
    return body


def verify_particle_settling_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("settling receipt changed")
    q=ParticleSettlingRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("settling caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("settling caller context differs")
    if evaluate_particle_settling(q,c)!=record:
        raise ValueError("original-drag settling replay differs")
    return True


def prepare_settling_deposit(record, template, context: RunContext, *, source_evidence_id: str,
                            mapping_evidence_id: str, fraction_reaching_deposit: float,
                            collector_to_deposit_mass_and_bulk_density_reviewed: bool):
    """Map recovered collector mass to reviewed deposit windows, without kinetics."""
    from .deposition_inventory import DepositInventoryRequest, DepositWindow
    verify_particle_settling_record(record)
    source=ParticleSettlingRequest.model_validate_json(json.dumps(record["request"]))
    base=DepositInventoryRequest.model_validate(template.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    sc=RunContext.model_validate_json(json.dumps(record["context"]))
    require_context_extension(sc,c)
    if not collector_to_deposit_mass_and_bulk_density_reviewed or type(fraction_reaching_deposit) not in (int,float) or not math.isfinite(fraction_reaching_deposit) or not 0<=fraction_reaching_deposit<=1:
        raise ValueError("collector destination, bulk deposit density and transfer fraction require review")
    if (base.asset_id,base.clock_id,base.start_time_s)!=(source.asset_id,source.clock_id,source.start_time_s):
        raise ValueError("collector and deposit must share the mapped asset, clock and initial time")
    evidence={e.id:e for e in c.evidence}
    if source_evidence_id not in evidence or evidence[source_evidence_id].sha256!=record["record_digest"]:
        raise ValueError("collector source receipt requires its exact context evidence pin")
    if evidence[source_evidence_id].origin!="simulation":
        raise ValueError("collector model receipt must retain its simulation origin")
    if mapping_evidence_id not in evidence or evidence[mapping_evidence_id].sha256 is None:
        raise ValueError("collector-to-deposit mapping requires source-pinned review evidence")
    if any(w.deposition_kg_s or w.removal_kg_s or w.cleaning_fraction_at_end for w in base.windows):
        raise ValueError("deposit template must not contain a competing deposition/removal/cleaning schedule")
    captured=[math.fsum(p["states"][i]["captured_mass_kg"] for p in record["particle_classes"])
              for i in range(len(source.elapsed_sample_time_s))]
    windows=[]
    for i,(a,b) in enumerate(zip(source.elapsed_sample_time_s,source.elapsed_sample_time_s[1:])):
        mass=(captured[i+1]-captured[i])*fraction_reaching_deposit
        if mass<0:
            raise ValueError("collector cumulative mass is not monotone")
        windows.append(DepositWindow(id=base.id+"/collector/"+str(i),duration_s=b-a,
                                     deposition_kg_s=mass/(b-a),removal_kg_s=0.0,evidence_id=mapping_evidence_id))
    payload=base.model_dump(mode="python"); payload["windows"]=tuple(windows)
    payload["evidence_ids"]=tuple(dict.fromkeys((*base.evidence_ids,source_evidence_id,mapping_evidence_id)))
    derived=DepositInventoryRequest.model_validate(payload)
    binding=dict(schema="pipeline_integrity.settling_deposit_binding.v1",source_receipt_digest=record["record_digest"],
                 template_digest=digest(base),derived_request_digest=digest(derived),context_digest=digest(c),
                 mapping_evidence_id=mapping_evidence_id,fraction_reaching_deposit=fraction_reaching_deposit,
                 interval_mean_mass_rates=True,within_window_rate_shape_inferred=False,
                 deposit_rate_representation="prescribed_piecewise_constant_interval_mean",
                 source_collector_curve_preserved_between_samples=False,
                 total_transferred_mass_kg=(captured[-1]-captured[0])*fraction_reaching_deposit)
    binding["record_digest"]=digest(binding)
    return derived,c,binding


def particle_settling_signal_frames(record, *, particle_id: str, stream_id: str):
    verify_particle_settling_record(record)
    q=ParticleSettlingRequest.model_validate_json(json.dumps(record["request"]))
    selected=[p for p in record["particle_classes"] if p["particle_id"]==particle_id]
    if len(selected)!=1:
        raise ValueError("settling signal particle class is absent")
    frames=[]
    for row in selected[0]["states"]:
        values={"concentration_kg_m3/"+str(i):v for i,v in enumerate(row["concentration_kg_m3"])}
        units=dict.fromkeys(values,"kg/m^3")
        values.update(suspended_mass_kg=row["suspended_mass_kg"],captured_mass_kg=row["captured_mass_kg"])
        units.update(suspended_mass_kg="kg",captured_mass_kg="kg")
        frames.append(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
            values=values,units=units,valid=dict.fromkeys(values,True),origin="simulation",
            operating_mode="dilute_terminal_particle_settling"))
    receipt=dict(schema="pipeline_integrity.particle_settling_frames.v1",source_record_digest=record["record_digest"],
                 particle_id=particle_id,stream_id=stream_id,height_edges_m=q.height_edges_m,
                 frames_digest=digest(frames),frame_count=len(frames))
    receipt["record_digest"]=digest(receipt)
    return tuple(frames),receipt
