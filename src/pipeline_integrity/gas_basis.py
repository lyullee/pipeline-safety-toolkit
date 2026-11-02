"""Explicit wet/dry reference-volume conversion by conservation of dry moles.

The shared dry-species composition is unchanged. Source and target water vapor
mole fractions are supplied directly; relative humidity, saturation, entrained
liquid and a humidification/dehumidification energy path are not inferred.
Volumes and volume rates retain separate mol and mol/s inventories.
"""
from __future__ import annotations

import json
import math
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Name, Positive, Nonnegative, RunContext, digest, json_value
from .datasets import file_digest
from .gas_meter import GasComponent
from .thermo import StateRequest, CheckedProvider, CoolPropProvider

SHA=r"^[a-f0-9]{64}$"
# SI defining constants: molar gas constant = Avogadro constant * Boltzmann constant.
R_SI_J_MOL_K=6.02214076e23*1.380649e-23
REVIEWS=(
    "same_dry_species_and_mole_fractions",
    "dry_species_identification_excludes_water_aliases",
    "only_water_vapor_added_or_removed",
    "source_and_target_single_gas_states",
    "explicit_water_mole_fractions_without_RH_or_saturation_inference",
    "common_R_compressibility_and_quantity_dimension",
    "no_chemical_reaction_or_entrained_liquid",
)


class GasBasisState(Contract):
    id: Name
    pressure_pa_abs: Positive
    temperature_K: Positive
    water_vapor_mole_fraction: float = Field(ge=0,lt=1)
    volume_basis: Literal["dry_gas","wet_gas"]
    declared_phase: Literal["single_gas"] = "single_gas"
    compressibility_Z_common_R: Positive | None = None
    state_evidence_id: Name
    water_fraction_evidence_id: Name
    gas_phase_evidence_id: Name
    compressibility_evidence_id: Name | None = None

    @model_validator(mode="after")
    def explicit_water(self):
        if self.volume_basis=="dry_gas" and self.water_vapor_mole_fraction!=0:
            raise ValueError("dry-gas reference declarations require exactly zero water vapor mole fraction")
        return self


class GasBasisCoolPropPin(Contract):
    version: Name
    revision: Name
    extension_sha256: str = Field(pattern=SHA)
    property_adapter_sha256: str = Field(pattern=SHA)


class GasBasisRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    quantity_kind: Literal["volume","volume_flow"]
    source_value: Nonnegative
    dry_composition_id: Name
    dry_components: tuple[GasComponent, ...] = Field(min_length=1,max_length=64)
    composition_evidence_id: Name
    source: GasBasisState
    target: GasBasisState
    Z_mode: Literal["supplied","coolprop"]
    coolprop_pin: GasBasisCoolPropPin | None = None
    reviews: dict[str,bool] = Field(default_factory=lambda:dict.fromkeys(REVIEWS,False))
    review_evidence_ids: dict[str,Name] = Field(default_factory=dict)
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    composition_sum_tolerance: Positive = Field(default=1e-12,le=1e-9)
    relative_property_crosscheck_tolerance: Positive = Field(default=1e-8,le=1e-4)
    relative_molar_balance_tolerance: Positive = Field(default=1e-9,le=1e-3)
    absolute_molar_balance_tolerance: Nonnegative = 0.0

    @model_validator(mode="after")
    def declared_basis(self):
        if len({c.fluid.casefold() for c in self.dry_components})!=len(self.dry_components):
            raise ValueError("dry-gas species must have unique declared identities")
        water_aliases={"water","h2o","steam","watervapor","water_vapor","heavywater","d2o","deuteriumoxide"}
        if any(c.fluid.casefold().replace(" ","") in water_aliases for c in self.dry_components):
            raise ValueError("water species cannot appear inside the dry-gas component fractions")
        total=math.fsum(c.mole_fraction for c in self.dry_components)
        if not math.isclose(total,1.0,rel_tol=0,abs_tol=self.composition_sum_tolerance):
            raise ValueError("dry-species mole fractions must sum to one without normalization")
        if len(set(self.evidence_ids))!=len(self.evidence_ids):
            raise ValueError("gas reference selected evidence IDs must be unique")
        if set(self.reviews)!=set(REVIEWS) or set(self.review_evidence_ids)-set(REVIEWS):
            raise ValueError("gas-basis applicability review fields differ from the declared boundary")
        if self.Z_mode=="supplied":
            if self.coolprop_pin is not None:
                raise ValueError("supplied compressibility mode cannot claim an unexecuted CoolProp pin")
            if any(s.compressibility_Z_common_R is None or s.compressibility_evidence_id is None for s in (self.source,self.target)):
                raise ValueError("supplied mode requires both positive common-R Z values and their evidence")
        else:
            if self.coolprop_pin is None:
                raise ValueError("CoolProp mode requires its explicitly reviewed backend/source pin")
            if any(s.compressibility_Z_common_R is not None or s.compressibility_evidence_id is not None for s in (self.source,self.target)):
                raise ValueError("native CoolProp mode cannot silently substitute supplied Z values")
        return self


def gas_basis_backend_identity():
    """Discover the optional native backend pin without resolving a gas state."""
    import CoolProp.CoolProp as cp
    return GasBasisCoolPropPin(version=version("CoolProp"),revision=cp.get_global_param_string("gitrevision"),
        extension_sha256=file_digest(Path(cp.__file__)),
        property_adapter_sha256=file_digest(Path(__file__).with_name("thermo.py")))


def _evidence(q,c):
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("gas reference conversion asset/clock differs from context")
    available={e.id:e for e in c.evidence}
    if len(available)!=len(c.evidence):
        raise ValueError("gas reference context evidence IDs must be unique")
    required={q.composition_evidence_id,*q.review_evidence_ids.values()}
    for state in (q.source,q.target):
        required|={state.state_evidence_id,state.water_fraction_evidence_id,state.gas_phase_evidence_id}
        if state.compressibility_evidence_id is not None:
            required.add(state.compressibility_evidence_id)
    if not required<=set(q.evidence_ids) or not set(q.evidence_ids)<=set(available):
        raise ValueError("gas composition, state, humidity, phase and review evidence must be selected from context")
    if any(available[identity].sha256 is None for identity in required):
        raise ValueError("gas-basis source declarations and reviews require source-hash-pinned evidence")
    return ["source-hash-pinned applicability review required: "+name for name in REVIEWS
            if not q.reviews[name] or name not in q.review_evidence_ids]


def _composition(q,state):
    water=state.water_vapor_mole_fraction
    values=[dict(fluid=c.fluid,mole_fraction=c.mole_fraction*(1.0-water)) for c in q.dry_components]
    if water>0:
        values.append(dict(fluid="Water",mole_fraction=water))
    if any(v["mole_fraction"]==0 for v in values):
        raise ValueError("nonzero dry-species fraction underflowed while constructing the explicit wet composition")
    return values


def _fluid(composition):
    if len(composition)==1:
        return composition[0]["fluid"]
    return "&".join(f"{c['fluid']}[{c['mole_fraction']:.17g}]" for c in composition)


def _native_state(q,state,role,cp,provider,calls):
    composition=_composition(q,state)
    fluid=_fluid(composition)
    request=StateRequest(fluid=fluid,backend="HEOS",inputs={"P":state.pressure_pa_abs,"T":state.temperature_K},
                         single_phase_required=False)
    resolved=provider.resolve(request)
    entry=dict(role=role,request=request.model_dump(mode="json"),state=resolved.model_dump(mode="json"),
               declared_composition=composition)
    calls.append(entry)
    if resolved.phase not in {"gas","supercritical_gas"}:
        raise ValueError(role+" state is not a resolved single gas phase; no gas phase is imposed")
    if (not math.isclose(resolved.pressure_pa_abs,state.pressure_pa_abs,rel_tol=q.relative_property_crosscheck_tolerance,abs_tol=0)
        or not math.isclose(resolved.temperature_k,state.temperature_K,rel_tol=q.relative_property_crosscheck_tolerance,abs_tol=0)):
        raise ValueError("native gas state changed the declared source/reference absolute state")
    if resolved.provider_version!=q.coolprop_pin.version or resolved.provider_revision!=q.coolprop_pin.revision:
        raise ValueError("thermodynamic provider identity differs from the reviewed native source")
    native_fluid="HEOS::"+fluid
    native={key:float(cp.PropsSI(output,"P",state.pressure_pa_abs,"T",state.temperature_K,native_fluid))
            for key,output in (("Z","Z"),("molar_density_mol_m3","Dmolar"),
                               ("gas_constant_J_mol_K","gas_constant"),("molar_mass_kg_mol","molar_mass"))}
    entry["native_properties"]=native
    if any(not math.isfinite(v) or v<=0 for v in native.values()):
        raise ValueError("native gas compressibility, molar density or gas constant is not positive and finite")
    native_z_check=state.pressure_pa_abs/(native["molar_density_mol_m3"]*native["gas_constant_J_mol_K"]*state.temperature_K)
    mass_density_check=native["molar_density_mol_m3"]*native["molar_mass_kg_mol"]
    common_z=native["Z"]*(native["gas_constant_J_mol_K"]/R_SI_J_MOL_K)
    common_molar_density=state.pressure_pa_abs/(common_z*R_SI_J_MOL_K*state.temperature_K)
    if not math.isfinite(common_z) or common_z<=0 or not math.isfinite(common_molar_density) or common_molar_density<=0:
        raise ValueError("common-R gas-basis representation exceeds finite arithmetic")
    if not all(math.isclose(a,b,rel_tol=q.relative_property_crosscheck_tolerance,abs_tol=0) for a,b in (
        (native["Z"],native_z_check),(resolved.density_kg_m3,mass_density_check),
        (common_molar_density,native["molar_density_mol_m3"]))):
        raise ValueError("native Z/R/molar-density/mass-density cross-check is inconsistent")
    entry.update(common_R_Z=common_z,common_R_molar_density_mol_m3=common_molar_density,
                 native_Z_crosscheck=native_z_check,native_mass_density_crosscheck_kg_m3=mass_density_check,
                 native_to_common_R_ratio=native["gas_constant_J_mol_K"]/R_SI_J_MOL_K)
    return common_z


def _seal(body):
    body=json_value(body)
    if body.get("implementation_sha256") is not None and body["implementation_sha256"]!=file_digest(Path(__file__)):
        raise ValueError("gas-basis implementation changed during evaluation")
    if body.get("thermo_adapter_sha256") is not None and body["thermo_adapter_sha256"]!=file_digest(Path(__file__).with_name("thermo.py")):
        raise ValueError("gas-basis property adapter changed during evaluation")
    if body.get("backend") is not None and gas_basis_backend_identity().model_dump(mode="json")!=body["backend"]:
        raise ValueError("gas-basis native backend changed during evaluation")
    if body.get("gas_meter_source_sha256") is not None and body["gas_meter_source_sha256"]!=file_digest(Path(__file__).with_name("gas_meter.py")):
        raise ValueError("original gas-meter source changed during conversion binding")
    if body.get("meter_source_coolprop_pin") is not None and gas_basis_backend_identity().model_dump(mode="json")!=body["meter_source_coolprop_pin"]:
        raise ValueError("original gas-meter thermodynamic backend changed during conversion binding")
    body["record_digest"]=digest(body)
    return body


def evaluate_gas_basis(request: GasBasisRequest, context: RunContext):
    q=GasBasisRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    issues=_evidence(q,c)
    body=dict(schema="pipeline_integrity.gas_basis.v1",request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),
        status="needs_data",issues=issues,values=None,molar_ledger=None,property_calls=[],backend=None,
        common_molar_gas_constant_J_mol_K=R_SI_J_MOL_K,
        compressibility_definition="P/(molar_density * common_SI_R * T)",
        dry_composition_digest=digest(q.dry_components),composition_automatically_normalized=False,
        declared_dry_mole_fraction_sum=math.fsum(component.mole_fraction for component in q.dry_components),
        source_composition=_composition(q,q.source),target_composition=_composition(q,q.target),
        water_fraction_source="explicit_vapor_mole_fraction",relative_humidity_inferred=False,
        native_gas_phase_imposed=False,
        saturation_inferred=False,condensation_path_or_energy_computed=False,
        chemical_conversion_inferred=False,amount_inferred_from_a_rate=False,
        custody_transfer_certified=False,EOS_accuracy_improvement_claimed=False,
        implementation_sha256=file_digest(Path(__file__)),
        thermo_adapter_sha256=file_digest(Path(__file__).with_name("thermo.py")),numerical_validation_completed=False)
    if issues:
        return _seal(body)
    if q.Z_mode=="coolprop":
        try:
            import CoolProp.CoolProp as cp
            pin=gas_basis_backend_identity()
            body["backend"]=pin.model_dump(mode="json")
            if pin!=q.coolprop_pin:
                raise ValueError("native gas-basis backend differs from the explicitly reviewed source pin")
            canonical=[cp.get_fluid_param_string(component.fluid,"name") for component in q.dry_components]
            if len(set(canonical))!=len(canonical) or any(name in {"Water","HeavyWater"} for name in canonical):
                raise ValueError("dry components resolve to duplicate fluids or a native water alias")
            body["canonical_dry_components"]=canonical
            provider=CheckedProvider(CoolPropProvider(use_cache=False))
            zs=_native_state(q,q.source,"source",cp,provider,body["property_calls"])
            zt=_native_state(q,q.target,"target",cp,provider,body["property_calls"])
            if gas_basis_backend_identity()!=pin:
                raise ValueError("native gas-basis backend changed during property evaluation")
        except ImportError as error:
            body.update(status="needs_data",issues=["CoolProp backend is unavailable: "+type(error).__name__])
            return _seal(body)
        except (ValueError,RuntimeError,ArithmeticError) as error:
            body.update(status="out_of_scope",issues=[type(error).__name__+": "+str(error)])
            return _seal(body)
        body["gas_phase_check"]="native_single_gas_phase_at_both_explicit_compositions"
        body["canonical_species_resolution_performed"]=True
        body["native_Z_reexpressed_in_common_R_basis"]=True
    else:
        zs=q.source.compressibility_Z_common_R
        zt=q.target.compressibility_Z_common_R
        body["gas_phase_check"]="caller_reviewed_single_gas_declarations_not_EOS_verified"
        body["canonical_species_resolution_performed"]=False
        body["native_Z_reexpressed_in_common_R_basis"]=False
    try:
        ys=q.source.water_vapor_mole_fraction
        yt=q.target.water_vapor_mole_fraction
        log_factor=(math.log(q.source.pressure_pa_abs)-math.log(q.target.pressure_pa_abs)
                    +math.log(q.target.temperature_K)-math.log(q.source.temperature_K)
                    +math.log(zt)-math.log(zs)+math.log1p(-ys)-math.log1p(-yt))
        factor=math.exp(log_factor)
        source_density=math.exp(math.log(q.source.pressure_pa_abs)-math.log(zs)-math.log(R_SI_J_MOL_K)-math.log(q.source.temperature_K))
        target_density=math.exp(math.log(q.target.pressure_pa_abs)-math.log(zt)-math.log(R_SI_J_MOL_K)-math.log(q.target.temperature_K))
        target_value=q.source_value*factor
        ns=q.source_value*source_density
        nd=ns*(1.0-ys)
        nt=nd/(1.0-yt)
        source_water=ns*ys
        target_water=nt*yt
        water_transfer=target_water-source_water
        species=[dict(fluid=component.fluid,source_dry_quantity=nd*component.mole_fraction,
                      target_dry_quantity=nd*component.mole_fraction) for component in q.dry_components]
        if not all(math.isfinite(v) and v>=0 for v in (factor,source_density,target_density,target_value,ns,nd,nt,source_water,target_water)) or factor==0 or source_density==0 or target_density==0:
            raise ValueError("gas-basis factor or molar inventory is outside positive finite arithmetic")
        if q.source_value>0 and any(v==0 for v in (target_value,ns,nd,nt)):
            raise ValueError("nonzero gas volume/flow underflow is not exact zero inventory")
        if nd>0 and any(row["source_dry_quantity"]==0 for row in species):
            raise ValueError("nonzero dry-species inventory underflow is not species removal")
        if (ns>0 and ys>0 and source_water==0) or (nt>0 and yt>0 and target_water==0):
            raise ValueError("nonzero water-vapor inventory underflow is not a dry-gas declaration")
        if nd>0 and ys!=yt and water_transfer==0:
            raise ValueError("the declared humidity change is not representable as a molar transfer")
        residue_dry=math.fsum(row["source_dry_quantity"] for row in species)-nd
        residue_total=nt-ns-water_transfer
        residue_target=target_value*target_density-nt
        tolerance=q.absolute_molar_balance_tolerance+q.relative_molar_balance_tolerance*max(ns,nt,nd)
        if not all(math.isfinite(v) for v in (residue_dry,residue_total,residue_target,tolerance)) or any(abs(v)>tolerance for v in (residue_dry,residue_total,residue_target)):
            raise ValueError("dry-species, total-mole or target-volume ledger does not close within the declared tolerance")
    except (ValueError,ArithmeticError) as error:
        body.update(status="out_of_scope",issues=[type(error).__name__+": "+str(error)])
        return _seal(body)
    molar_unit="mol" if q.quantity_kind=="volume" else "mol/s"
    volume_unit="m^3" if q.quantity_kind=="volume" else "m^3/s"
    body.update(status="computed",issues=[],values=dict(quantity_kind=q.quantity_kind,source_value=q.source_value,
        target_value=target_value,unit=volume_unit,target_over_source_volume_factor=factor,
        source_common_R_Z=zs,target_common_R_Z=zt,
        source_molar_density_mol_m3=source_density,target_molar_density_mol_m3=target_density),
        molar_ledger=dict(unit=molar_unit,molar_quantity_kind="molar_amount" if q.quantity_kind=="volume" else "molar_flow",
            source_total_molar_quantity=ns,target_total_molar_quantity=nt,
            conserved_dry_molar_quantity=nd,source_water_vapor_quantity=source_water,target_water_vapor_quantity=target_water,
            net_water_to_gas_quantity=water_transfer,net_water_addition_quantity=max(water_transfer,0.0),
            net_water_removal_quantity=max(-water_transfer,0.0),dry_species=species,
            dry_component_sum_residual=residue_dry,total_molar_quantity_residual=residue_total,
            target_volume_molar_quantity_residual=residue_target,absolute_balance_tolerance=tolerance),
        reference_basis_conversion_only=True,water_transfer_is_net_bookkeeping_not_a_process_path=True)
    if file_digest(Path(__file__))!=body["implementation_sha256"] or file_digest(Path(__file__).with_name("thermo.py"))!=body["thermo_adapter_sha256"]:
        raise ValueError("gas-basis source changed during evaluation")
    return _seal(body)


def verify_gas_basis_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("gas-basis receipt changed")
    q=GasBasisRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("gas-basis caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("gas-basis caller context differs")
    if evaluate_gas_basis(q,c)!=record:
        raise ValueError("gas-basis composition, native state or molar ledger replay differs")
    return True


class MeterGasBasisRequest(Contract):
    id: Name
    meter_record: dict
    expected_meter_receipt_digest: str = Field(pattern=SHA)
    expected_meter_basis_digest: str = Field(pattern=SHA)
    meter_source_evidence_id: Name
    mapping_evidence_id: Name
    actual_upstream_volume_flow_mapping_reviewed: bool = False
    conversion: GasBasisRequest

    @field_validator("meter_record")
    @classmethod
    def meter_budget(cls,value):
        value=json_value(value)
        if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode("utf-8"))>32000000:
            raise ValueError("gas-meter source receipt exceeds the basis-conversion input budget")
        return value

    @model_validator(mode="after")
    def quantity_kind(self):
        if self.conversion.quantity_kind!="volume_flow":
            raise ValueError("a meter volume rate cannot be converted to an amount without an observed time integral")
        if self.meter_record.get("record_digest")!=self.expected_meter_receipt_digest:
            raise ValueError("gas-meter source receipt differs from its explicit input pin")
        audits=self.meter_record.get("audits")
        if not isinstance(audits,dict) or audits.get("basis_digest")!=self.expected_meter_basis_digest:
            raise ValueError("gas-meter source basis differs from its explicit input pin")
        return self


def evaluate_meter_gas_basis(request: MeterGasBasisRequest, context: RunContext):
    """Bind only the original meter's actual upstream nonnegative volume flow.

    The meter's own same-composition reference result remains unchanged. This
    separate conversion uses the actual upstream state and explicit wet/dry
    declarations, and retains the signed meter result rather than taking an
    implicit absolute value for reverse-flow data.
    """
    from .gas_meter import GasMeterRequest, verify_gas_meter_record, _fluid as meter_fluid
    q=MeterGasBasisRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    source=q.meter_record
    original=GasMeterRequest.model_validate_json(json.dumps(source["request"]))
    source_context=RunContext.model_validate_json(json.dumps(source["context"]))
    if (source_context.asset_id,source_context.clock_id,source_context.scenario_id,source_context.origin)!=(
        c.asset_id,c.clock_id,c.scenario_id,c.origin):
        raise ValueError("gas-meter to basis conversion must preserve the original asset, clock, scenario and origin")
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence):
        raise ValueError("gas-meter binding context evidence IDs must be unique")
    for old in source_context.evidence:
        if old.id not in evidence or old.model_dump(mode="json")!=evidence[old.id].model_dump(mode="json"):
            raise ValueError("gas-meter source evidence must remain unchanged in the downstream context")
    if any(key not in c.metadata or c.metadata[key]!=value for key,value in source_context.metadata.items()):
        raise ValueError("gas-meter source metadata must remain unchanged in the downstream context")
    for identity in (q.meter_source_evidence_id,q.mapping_evidence_id):
        if identity not in q.conversion.evidence_ids or identity not in evidence or evidence[identity].sha256 is None:
            raise ValueError("gas-meter source and actual-flow mapping evidence must be selected and source-hash pinned")
    if evidence[q.meter_source_evidence_id].sha256!=q.expected_meter_receipt_digest:
        raise ValueError("gas-meter source evidence differs from the expected meter receipt digest")
    body=dict(schema="pipeline_integrity.meter_gas_basis.v1",request=q.model_dump(mode="json"),
        context=c.model_dump(mode="json"),status="needs_data",issues=[],conversion_result=None,
        source_meter_digest=q.expected_meter_receipt_digest,source_meter_basis_digest=q.expected_meter_basis_digest,
        source_meter_context_digest=digest(source_context),source_meter_reference_result_modified=False,
        amount_inferred_from_a_rate=False,reverse_flow_magnitude_automatically_taken=False,
        implementation_sha256=file_digest(Path(__file__)),
        gas_meter_source_sha256=file_digest(Path(__file__).with_name("gas_meter.py")),numerical_validation_completed=False)
    if not q.actual_upstream_volume_flow_mapping_reviewed:
        body["issues"]=["actual upstream volume-flow mapping requires explicit review"]
        return _seal(body)
    replay=verify_gas_meter_record(source)
    if not isinstance(replay,dict) or replay.get("valid") is not True or source.get("status")!="computed":
        body["issues"]=["a replay-verified computed original gas-meter receipt is required"]
        return _seal(body)
    conversion=q.conversion
    if (conversion.asset_id,conversion.clock_id,conversion.event_id)!=(original.asset_id,original.clock_id,original.event_id):
        raise ValueError("gas-basis conversion must identify the source meter asset, clock and event")
    source_time=original.time_s+original.clock_offset_s
    if conversion.time_s!=source_time:
        raise ValueError("gas-basis conversion must retain the meter's explicit common-clock sample time")
    if conversion.source.id!=original.upstream_port_id:
        raise ValueError("gas-basis source must explicitly identify the original actual upstream meter port")
    if (conversion.source.pressure_pa_abs,conversion.source.temperature_K)!=(original.upstream_pressure_pa_abs,original.upstream_temperature_K):
        raise ValueError("gas-basis source P/T must exactly match the original actual upstream state")
    expected_volume_basis="dry_gas" if original.volume_basis=="same_composition_dry" else "wet_gas"
    if conversion.source.volume_basis!=expected_volume_basis:
        raise ValueError("gas-basis moisture label differs from the actual upstream meter basis")
    actual=source["values"]["signed_actual_volume_flow_m3_s"]
    if actual<0:
        raise ValueError("reverse signed meter flow needs an explicitly directed independent conversion; no implicit absolute value")
    if conversion.source_value!=actual:
        raise ValueError("gas-basis source rate must equal the original actual volume rate, not its same-composition reference rate")
    try:
        import CoolProp.CoolProp as cp
        native_pin=gas_basis_backend_identity()
        original_backend=source["backend"]
        if (original_backend["CoolProp_version"],original_backend["CoolProp_revision"],
            original_backend["CoolProp_extension_sha256"],original_backend["property_adapter_sha256"])!=(
            native_pin.version,native_pin.revision,native_pin.extension_sha256,native_pin.property_adapter_sha256):
            raise ValueError("gas-meter source backend differs from the current source-state mapping backend")
        body["meter_source_coolprop_pin"]=native_pin.model_dump(mode="json")
        canonical_meter=source["audits"]["canonical_backend_components"]
        if len(canonical_meter)!=len(original.components):
            raise ValueError("original meter component audit does not align with its declared composition")
        canonical_dry=[cp.get_fluid_param_string(component.fluid,"name") for component in conversion.dry_components]
        if len(set(canonical_dry))!=len(canonical_dry) or any(name in {"Water","HeavyWater"} for name in canonical_dry):
            raise ValueError("basis dry species resolve to duplicate names or water aliases")
        source_composition=dict(zip(canonical_meter,(component.mole_fraction for component in original.components)))
        if len(source_composition)!=len(original.components):
            raise ValueError("original meter's canonical gas species are not unique")
        ys=conversion.source.water_vapor_mole_fraction
        actual_water=source_composition.get("Water",0.0)
        if actual_water!=ys:
            raise ValueError("gas-basis source water fraction differs from the original actual gas composition")
        dry_source={name:fraction for name,fraction in source_composition.items() if name!="Water"}
        if set(dry_source)!=set(canonical_dry):
            raise ValueError("gas-basis dry species differ from the original upstream meter species")
        for component,name in zip(conversion.dry_components,canonical_dry):
            if not math.isclose(dry_source[name],component.mole_fraction*(1.0-ys),rel_tol=0,abs_tol=conversion.composition_sum_tolerance):
                raise ValueError("gas-basis dry composition fractions differ from the original actual meter mixture")
        fluid="HEOS::"+meter_fluid(original)
        native_R=float(cp.PropsSI("gas_constant","P",original.upstream_pressure_pa_abs,"T",original.upstream_temperature_K,fluid))
        native_molar_density=float(cp.PropsSI("Dmolar","P",original.upstream_pressure_pa_abs,"T",original.upstream_temperature_K,fluid))
        common_meter_Z=source["values"]["compressibility_Z"]*native_R/R_SI_J_MOL_K
        if not all(math.isfinite(v) and v>0 for v in (native_R,native_molar_density,common_meter_Z)):
            raise ValueError("meter source native gas constant/molar density/common-R Z is invalid")
    except ImportError:
        body["issues"]=["original meter thermodynamic backend is unavailable; source basis is not substituted"]
        return _seal(body)
    result=evaluate_gas_basis(conversion,c)
    body.update(conversion_result=result,status=result["status"],issues=list(result["issues"]),
                canonical_source_components=source_composition,canonical_dry_components=canonical_dry,
                source_meter_native_R_J_mol_K=native_R,source_meter_native_molar_density_mol_m3=native_molar_density,
                source_meter_common_R_Z=common_meter_Z,source_signed_actual_volume_flow_m3_s=actual,
                source_gas_phase_checked_by_original_meter=True,
                source_state_pressure_pa_abs=original.upstream_pressure_pa_abs,
                source_state_temperature_K=original.upstream_temperature_K,
                source_state_water_vapor_mole_fraction=ys)
    if result["status"]=="computed":
        if not math.isclose(result["values"]["source_common_R_Z"],common_meter_Z,
                            rel_tol=conversion.relative_property_crosscheck_tolerance,abs_tol=0):
            raise ValueError("gas-basis source compressibility differs from the original meter's actual thermodynamic state")
        native_molar_flow=actual*native_molar_density
        model_molar_flow=result["molar_ledger"]["source_total_molar_quantity"]
        if not math.isclose(native_molar_flow,model_molar_flow,rel_tol=conversion.relative_property_crosscheck_tolerance,
                            abs_tol=conversion.absolute_molar_balance_tolerance):
            raise ValueError("meter actual molar flow and dry-basis source ledger disagree")
        body["source_molar_flow_crosscheck_residual_mol_s"]=model_molar_flow-native_molar_flow
    if file_digest(Path(__file__).with_name("gas_meter.py"))!=body["gas_meter_source_sha256"]:
        raise ValueError("original gas-meter source changed during wet/dry conversion binding")
    return _seal(body)


def verify_meter_gas_basis_record(record, *, request=None, context=None):
    if not isinstance(record,dict) or digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("meter gas-basis receipt changed")
    q=MeterGasBasisRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and request.model_dump(mode="json")!=q.model_dump(mode="json"):
        raise ValueError("meter gas-basis caller request differs")
    if context is not None and context.model_dump(mode="json")!=c.model_dump(mode="json"):
        raise ValueError("meter gas-basis caller context differs")
    if evaluate_meter_gas_basis(q,c)!=record:
        raise ValueError("meter source, actual gas basis or wet/dry conversion replay differs")
    return True
