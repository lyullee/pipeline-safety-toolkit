"""Closed single-guest hydrate reaction and deposition with real-gas fugacity.

Kinetic coefficients, equilibrium fugacity and accessible-area laws require
caller calibration. Temperature is imposed by an external bath; its actual
internal-energy exchange is recorded. No nucleation delay or multiphase slip
is inferred. This is a lumped reactive inventory, not a distributed pipe PDE.
"""
from __future__ import annotations

import bisect
import json
import math
from importlib.metadata import version
from pathlib import Path
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, require_context_extension, json_value
from .datasets import file_digest

R=8.31446261815324


class HydrateEquilibriumFugacity(Contract):
    temperature_K: tuple[Positive,...] = Field(min_length=2,max_length=500)
    guest_fugacity_Pa: tuple[Positive,...] = Field(min_length=2,max_length=500)
    evidence_id: Name

    @model_validator(mode="after")
    def support(self):
        if len(self.temperature_K)!=len(self.guest_fugacity_Pa) or any(b<=a for a,b in zip(self.temperature_K,self.temperature_K[1:])):
            raise ValueError("hydrate equilibrium fugacity requires aligned increasing temperature knots")
        return self


class HydrateKineticWindow(Contract):
    id: Name
    duration_s: Positive
    bath_temperature_K: Positive
    deposition_rate_constant_s_inv: Nonnegative
    erosion_rate_constant_s_inv: Nonnegative
    evidence_id: Name


class HydrateKineticsRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    guest_fluid: Name
    energy_reference_id: Name
    start_time_s: Nonnegative = 0.0
    total_volume_m3: Positive
    minimum_gas_volume_m3: Positive
    minimum_guest_gas_mol: Positive = 1e-15
    minimum_guest_gas_fraction_of_total: float = Field(default=1e-13,gt=0,le=1e-3)
    initial_temperature_K: Positive
    initial_guest_gas_mol: Positive
    initial_liquid_water_mol: Positive
    initial_suspended_hydrate_mol: Nonnegative = 0.0
    initial_deposited_hydrate_mol: Nonnegative = 0.0
    water_per_guest_mol: Positive
    water_molar_mass_kg_mol: Positive
    water_density_kg_m3: Positive
    hydrate_density_kg_m3: Positive
    water_molar_heat_capacity_J_mol_K: Positive
    hydrate_molar_heat_capacity_J_mol_K: Positive
    reference_temperature_K: Positive
    reference_guest_pressure_Pa_abs: Positive
    water_reference_internal_energy_J_mol: float
    formation_internal_energy_release_at_reference_J_mol: Positive
    formation_coefficient_at_reference_mol_m2_s_Pa: Nonnegative
    formation_activation_energy_J_mol: Nonnegative
    dissociation_coefficient_at_reference_mol_m2_s_Pa: Nonnegative
    dissociation_activation_energy_J_mol: Nonnegative
    initial_accessible_water_interface_area_m2: Nonnegative
    suspended_specific_reactive_area_m2_mol: Nonnegative
    deposited_specific_reactive_area_m2_mol: Nonnegative
    equilibrium: HydrateEquilibriumFugacity
    windows: tuple[HydrateKineticWindow,...] = Field(min_length=1,max_length=1000)
    maximum_step_s: Positive
    maximum_steps: int = Field(default=10000,ge=1,le=100000)
    maximum_property_calls: int = Field(default=1000000,ge=10,le=10000000)
    maximum_nonlinear_evaluations_per_step: int = Field(default=100,ge=5,le=1000)
    absolute_extent_residual_tolerance_mol: Positive = 1e-8
    relative_extent_residual_tolerance: float = Field(default=1e-8,ge=0,le=1e-3)
    absolute_energy_tolerance_J: Positive = 1e-5
    relative_energy_tolerance: float = Field(default=1e-9,ge=0,le=1e-3)
    pressure_support_Pa_abs: tuple[Positive,Positive]
    maximum_result_bytes: int = Field(default=64000000,ge=1000,le=1000000000)
    material_and_kinetic_evidence_ids: tuple[Name,...] = Field(min_length=1)
    pure_guest_common_reference_and_fixed_condensed_densities_reviewed: bool = False
    interfacial_fugacity_and_calibrated_effective_kinetics_reviewed: bool = False
    shrinking_water_domains_and_reactive_hydrate_area_reviewed: bool = False
    bath_and_first_order_deposition_erosion_reviewed: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if self.pressure_support_Pa_abs[0]>=self.pressure_support_Pa_abs[1]:
            raise ValueError("hydrate guest pressure support requires positive width")
        if len({w.id for w in self.windows})!=len(self.windows):
            raise ValueError("hydrate kinetic window IDs must be unique")
        if sum(math.ceil(w.duration_s/self.maximum_step_s) for w in self.windows)>self.maximum_steps:
            raise ValueError("hydrate kinetic step count exceeds its work budget")
        return self


def evaluate_hydrate_kinetics(request: HydrateKineticsRequest, context: RunContext):
    import numpy as np
    import scipy
    import CoolProp.CoolProp as CP
    from scipy.optimize import least_squares
    q=HydrateKineticsRequest.model_validate(request.model_dump(mode="python"))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):
        raise ValueError("hydrate kinetic asset/clock differs from context")
    if not all((q.pure_guest_common_reference_and_fixed_condensed_densities_reviewed,
                q.interfacial_fugacity_and_calibrated_effective_kinetics_reviewed,
                q.shrinking_water_domains_and_reactive_hydrate_area_reviewed,
                q.bath_and_first_order_deposition_erosion_reviewed)):
        raise ValueError("hydrate thermodynamic, kinetic, geometry and bath reviews are required")
    needed=set(q.material_and_kinetic_evidence_ids)|{q.equilibrium.evidence_id}|{w.evidence_id for w in q.windows}
    evidence={e.id:e for e in c.evidence}
    if len(evidence)!=len(c.evidence) or any(k not in evidence or evidence[k].sha256 is None for k in needed):
        raise ValueError("hydrate reaction and deposition laws require source-pinned evidence")
    pins={name:file_digest(Path(__file__).with_name(name+".py")) for name in ("hydrate_kinetics","contracts","datasets")}
    input_pin=digest(dict(request=q,context=c))
    property_source_pin=file_digest(Path(CP.__file__))
    property_identity=dict(version=version("CoolProp"),revision=CP.get_global_param_string("gitrevision"),
                           extension_sha256=property_source_pin)
    reserved_bytes=len(json.dumps(dict(request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),
        source_sha256=pins,CoolProp_identity=property_identity),allow_nan=False).encode("utf-8"))+8192
    if reserved_bytes>=q.maximum_result_bytes:
        raise ValueError("hydrate request/context/source metadata exceed retained-result budget")
    gas=CP.AbstractState("HEOS",q.guest_fluid)
    if len(gas.fluid_names())!=1 or CP.get_fluid_param_string(q.guest_fluid,"pure").strip().lower()!="true":
        raise ValueError("hydrate kinetic model requires one pure guest fluid")
    gas.update(CP.PT_INPUTS,q.reference_guest_pressure_Pa_abs,q.reference_temperature_K)
    if gas.phase() not in (CP.iphase_gas,CP.iphase_supercritical_gas,CP.iphase_supercritical):
        raise ValueError("hydrate guest reference must use the same reviewed gas-like branch")
    guest_reference_u=float(gas.umolar())
    guest_molar_mass=float(gas.molar_mass())
    hydrate_molar_mass=guest_molar_mass+q.water_per_guest_mol*q.water_molar_mass_kg_mol
    if not math.isfinite(guest_reference_u) or not math.isfinite(hydrate_molar_mass) or not math.isfinite(guest_molar_mass) or min(hydrate_molar_mass,guest_molar_mass)<=0:
        raise ValueError("hydrate guest reference and molecular mass must be finite")
    initial_total=q.initial_suspended_hydrate_mol+q.initial_deposited_hydrate_mol
    guest_total=q.initial_guest_gas_mol+initial_total
    water_total=q.initial_liquid_water_mol+q.water_per_guest_mol*initial_total
    stoich_upper=min(guest_total,water_total/q.water_per_guest_mol)
    zero_hydrate_gas_volume=q.total_volume_m3-water_total*q.water_molar_mass_kg_mol/q.water_density_kg_m3
    volume_slope=q.water_per_guest_mol*q.water_molar_mass_kg_mol/q.water_density_kg_m3-hydrate_molar_mass/q.hydrate_density_kg_m3
    lower=0.0; upper=stoich_upper
    if volume_slope>0:
        lower=max(lower,(q.minimum_gas_volume_m3-zero_hydrate_gas_volume)/volume_slope)
    elif volume_slope<0:
        upper=min(upper,(q.minimum_gas_volume_m3-zero_hydrate_gas_volume)/volume_slope)
    elif zero_hydrate_gas_volume<q.minimum_gas_volume_m3:
        raise ValueError("reactor has insufficient gas volume throughout its stoichiometric support")
    # Zero gas density is outside the real-gas fugacity API; no fabricated
    # zero-density thermodynamic state is used to complete a reaction step.
    upper=min(upper,guest_total-max(q.minimum_guest_gas_mol,guest_total*q.minimum_guest_gas_fraction_of_total))
    if not 0<=lower<=initial_total<upper:
        raise ValueError("initial hydrate state has no positive physical reaction support")
    property_calls=1  # The actual reference PT update is part of the native work budget.
    def equilibrium(T):
        knots=q.equilibrium.temperature_K
        if not knots[0]<=T<=knots[-1]:
            raise ValueError("bath temperature is outside equilibrium fugacity support")
        i=min(len(knots)-2,max(0,bisect.bisect_right(knots,T)-1))
        weight=(T-knots[i])/(knots[i+1]-knots[i])
        return math.exp((1-weight)*math.log(q.equilibrium.guest_fugacity_Pa[i])+weight*math.log(q.equilibrium.guest_fugacity_Pa[i+1]))
    def thermo(total,T):
        nonlocal property_calls
        property_calls+=1
        if property_calls>q.maximum_property_calls:
            raise ValueError("hydrate property-call budget exhausted")
        ng=guest_total-total; nw=water_total-q.water_per_guest_mol*total
        V=zero_hydrate_gas_volume+volume_slope*total
        if ng<=0 or nw<0 or V<q.minimum_gas_volume_m3:
            raise ValueError("hydrate reaction trial left positive gas/water/volume support")
        gas.update(CP.DmolarT_INPUTS,ng/V,T)
        if gas.phase() not in (CP.iphase_gas,CP.iphase_supercritical_gas,CP.iphase_supercritical):
            raise ValueError("guest fluid is outside the reviewed gas-like phase support")
        pressure=float(gas.p()); fugacity_coefficient=float(gas.fugacity_coefficient(0)); fugacity=pressure*fugacity_coefficient
        gas_u=float(gas.umolar())
        if not all(math.isfinite(v) for v in (pressure,fugacity,gas_u)) or not q.pressure_support_Pa_abs[0]<=pressure<=q.pressure_support_Pa_abs[1] or fugacity<=0:
            raise ValueError("hydrate guest property trial is outside finite reviewed pressure/fugacity support")
        native_density=float(gas.rhomolar())
        if not math.isfinite(native_density) or native_density<=0 or not math.isclose(native_density,ng/V,rel_tol=1e-11,abs_tol=0.0) or not math.isclose(float(gas.T()),T,rel_tol=1e-12,abs_tol=0.0):
            raise ValueError("native guest update changed the exact molar-density/temperature inputs")
        water_u=q.water_reference_internal_energy_J_mol+q.water_molar_heat_capacity_J_mol_K*(T-q.reference_temperature_K)
        hydrate_u=(guest_reference_u+q.water_per_guest_mol*q.water_reference_internal_energy_J_mol
                   -q.formation_internal_energy_release_at_reference_J_mol
                   +q.hydrate_molar_heat_capacity_J_mol_K*(T-q.reference_temperature_K))
        U=math.fsum((ng*gas_u,nw*water_u,total*hydrate_u))
        guest_residual=math.fsum((ng,total,-guest_total))
        water_residual=math.fsum((nw,q.water_per_guest_mol*total,-water_total))
        mass_residual=math.fsum((ng*guest_molar_mass,nw*q.water_molar_mass_kg_mol,total*hydrate_molar_mass,
            -guest_total*guest_molar_mass,-water_total*q.water_molar_mass_kg_mol))
        extent_tolerance=q.absolute_extent_residual_tolerance_mol+q.relative_extent_residual_tolerance*max(guest_total,total)
        if abs(guest_residual)>extent_tolerance or abs(water_residual)>q.water_per_guest_mol*extent_tolerance:
            raise ValueError("hydrate guest/water component inventories do not conserve their actual molar totals")
        mass_tolerance=(guest_molar_mass+q.water_per_guest_mol*q.water_molar_mass_kg_mol)*extent_tolerance
        if not math.isfinite(U) or not math.isfinite(mass_residual) or abs(mass_residual)>mass_tolerance:
            raise ValueError("hydrate reactor internal energy or physical mass balance is unresolved")
        return dict(guest_gas_mol=ng,liquid_water_mol=nw,gas_volume_m3=V,pressure_pa_abs=pressure,
                    guest_fugacity_Pa=fugacity,guest_fugacity_coefficient=fugacity_coefficient,
                    guest_molar_density_mol_m3=native_density,internal_energy_J=U,temperature_K=T,
                    guest_inventory_residual_mol=guest_residual,water_inventory_residual_mol=water_residual,
                    reactor_mass_residual_kg=mass_residual)
    total=initial_total; deposited=q.initial_deposited_hydrate_mol
    time=q.start_time_s; T=q.initial_temperature_K
    initial_state=thermo(total,T); initial_energy=initial_state["internal_energy_J"]
    heat_sensible=heat_reaction=0.0; rows=[]; ledgers=[]; failure=None
    def state_row(window_id,values,at_time,hydrate,deposited_phase):
        return dict(window_id=window_id,time_s=at_time,total_hydrate_mol=hydrate,
            suspended_hydrate_mol=hydrate-deposited_phase,deposited_hydrate_mol=deposited_phase,
            deposited_mass_kg=deposited_phase*hydrate_molar_mass,**values)
    rows.append(state_row(q.windows[0].id,initial_state,time,total,deposited))
    retained_bytes=reserved_bytes+len(json.dumps(rows,allow_nan=False).encode("utf-8"))
    for window in q.windows:
        if failure:
            break
        try:
            temperature=window.bath_temperature_K; feq=equilibrium(temperature)
            kf=q.formation_coefficient_at_reference_mol_m2_s_Pa*math.exp(-q.formation_activation_energy_J_mol/R*(1/temperature-1/q.reference_temperature_K))
            kd=q.dissociation_coefficient_at_reference_mol_m2_s_Pa*math.exp(-q.dissociation_activation_energy_J_mol/R*(1/temperature-1/q.reference_temperature_K))
            steps=math.ceil(window.duration_s/q.maximum_step_s)
            window_start=time
            for step in range(steps):
                next_time=window_start+window.duration_s*(step+1)/steps
                dt=next_time-time
                if not math.isfinite(next_time) or dt<=0:
                    raise ValueError("hydrate kinetic clock cannot resolve its actual boundary window")
                old_total,old_deposited=total,deposited
                old=thermo(total,T); pre=thermo(total,temperature)
                scale=max(1.0,old_total,stoich_upper)
                def rates(h,d):
                    state=thermo(h,temperature)
                    drive=state["guest_fugacity_Pa"]-feq
                    area=q.initial_accessible_water_interface_area_m2*(state["liquid_water_mol"]/q.initial_liquid_water_mol)**(2/3)
                    formation=kf*area*max(drive,0.0)
                    ds=kd*q.suspended_specific_reactive_area_m2_mol*(h-d)*max(-drive,0.0)
                    dd=kd*q.deposited_specific_reactive_area_m2_mol*d*max(-drive,0.0)
                    deposition=window.deposition_rate_constant_s_inv*(h-d)
                    erosion=window.erosion_rate_constant_s_inv*d
                    fluxes=(formation,ds,dd,deposition,erosion)
                    if any(not math.isfinite(value) or value<0 for value in fluxes):
                        raise ValueError("hydrate reaction/deposition fluxes left finite nonnegative support")
                    return state,fluxes
                nonlinear_evaluations=0
                def residual(x):
                    nonlocal nonlinear_evaluations
                    nonlinear_evaluations+=1
                    if nonlinear_evaluations>q.maximum_nonlinear_evaluations_per_step:
                        raise ValueError("hydrate nonlinear residual/finite-difference evaluation budget exhausted")
                    h=lower+x[0]*(upper-lower); d=h*x[1]
                    _,r=rates(h,d)
                    return np.array((h-old_total-dt*(r[0]-r[1]-r[2]),
                                     d-old_deposited-dt*(r[3]-r[4]-r[2])))/scale
                current=[(total-lower)/(upper-lower),0.0 if total==0 else deposited/total]
                _,initial_rates=rates(total,deposited)
                if all(value==0 for value in initial_rates):
                    solved=np.array(current); successful=True
                else:
                    predicted=min(upper,max(lower,total+dt*(initial_rates[0]-initial_rates[1]-initial_rates[2])))
                    predicted_deposit=min(predicted,max(0.0,deposited+dt*(initial_rates[3]-initial_rates[4]-initial_rates[2])))
                    guess=[(predicted-lower)/(upper-lower),0.0 if predicted==0 else predicted_deposit/predicted]
                    solution=least_squares(residual,guess,bounds=([0.0,0.0],[1.0,1.0]),
                        max_nfev=q.maximum_nonlinear_evaluations_per_step,ftol=1e-12,xtol=1e-12,gtol=1e-12)
                    solved=solution.x; successful=bool(solution.success)
                h=lower+float(solved[0])*(upper-lower); d=h*float(solved[1])
                if not lower<=h<=upper or not 0<=d<=h:
                    raise ValueError("hydrate implicit result left its actual phase-inventory bounds")
                state,r=rates(h,d)
                # Qualify the actual final fluxes already evaluated above;
                # calling the optimizer callback again would spend another
                # finite-difference budget slot and repeat the same EOS state.
                residual_mol=np.array((h-old_total-dt*(r[0]-r[1]-r[2]),
                                       d-old_deposited-dt*(r[3]-r[4]-r[2])))
                tolerance=q.absolute_extent_residual_tolerance_mol+q.relative_extent_residual_tolerance*max(abs(h-old_total),dt*sum(abs(v) for v in r))
                if not successful or max(abs(v) for v in residual_mol)>tolerance:
                    raise ValueError("hydrate reaction/deposition implicit molar balance did not converge")
                if time+dt<=time:
                    raise ValueError("hydrate kinetic clock cannot resolve the accepted timestep")
                sensible=pre["internal_energy_J"]-old["internal_energy_J"]
                reaction=state["internal_energy_J"]-pre["internal_energy_J"]
                next_heat_sensible=heat_sensible+sensible; next_heat_reaction=heat_reaction+reaction
                energy_residual=state["internal_energy_J"]-initial_energy-next_heat_sensible-next_heat_reaction
                energy_tolerance=q.absolute_energy_tolerance_J+q.relative_energy_tolerance*max(
                    abs(state["internal_energy_J"]-initial_energy),abs(next_heat_sensible),abs(next_heat_reaction))
                if not math.isfinite(energy_residual) or abs(energy_residual)>energy_tolerance:
                    raise ValueError("hydrate reactor/bath energy ledger exceeds caller tolerance")
                row=state_row(window.id,state,next_time,h,d)
                ledger=dict(window_id=window.id,time_s=next_time,duration_s=dt,
                    formed_mol=dt*r[0],dissociated_suspended_mol=dt*r[1],dissociated_deposit_mol=dt*r[2],
                    deposited_transfer_mol=dt*r[3],eroded_transfer_mol=dt*r[4],
                    extent_residual_mol=residual_mol.tolist(),equilibrium_fugacity_Pa=feq,
                    sensible_bath_heat_into_reactor_J=sensible,reaction_bath_heat_into_reactor_J=reaction,
                    energy_residual_J=energy_residual,nonlinear_residual_evaluations=nonlinear_evaluations)
                required_bytes=len(json.dumps((row,ledger),allow_nan=False).encode("utf-8"))
                if retained_bytes+required_bytes>q.maximum_result_bytes:
                    raise ValueError("hydrate retained state/ledger byte budget exhausted before committing a step")
                heat_sensible,heat_reaction=next_heat_sensible,next_heat_reaction
                total,deposited=h,d; T=temperature; time=next_time
                rows.append(row); ledgers.append(ledger); retained_bytes+=required_bytes
        except (ValueError,RuntimeError,ArithmeticError) as error:
            failure=dict(window_id=window.id,error_type=type(error).__name__,reason=str(error)[:1000],last_accepted_time_s=time)
    if pins!={name:file_digest(Path(__file__).with_name(name+".py")) for name in pins} or digest(dict(request=q,context=c))!=input_pin or file_digest(Path(CP.__file__))!=property_source_pin:
        raise ValueError("hydrate kinetic source or input/context changed during evaluation")
    body=dict(schema="pipeline_integrity.hydrate_kinetics.v1",request=q.model_dump(mode="json"),context=c.model_dump(mode="json"),
        status="withheld" if failure else "computed_diagnostic",failure=failure,states=rows,ledgers=ledgers,
        property_calls=property_calls,source_sha256=pins,scipy_version=scipy.__version__,CoolProp_identity=property_identity,
        hydrate_molar_mass_kg_mol=hydrate_molar_mass,guest_reference_internal_energy_J_mol=guest_reference_u,
        energy_reference_id=q.energy_reference_id,guest_total_mol=guest_total,water_total_mol=water_total,
        guest_reference_state=dict(backend="HEOS",fluid=gas.fluid_names()[0],temperature_K=q.reference_temperature_K,
            pressure_pa_abs=q.reference_guest_pressure_Pa_abs,internal_energy_J_mol=guest_reference_u),
        hydrate_molar_basis="one_guest_formula_unit_with_declared_water_stoichiometry",
        condensed_caloric_basis="constant_density_internal_energy_slopes_in_J_per_mol_K",
        output_origin="simulation",bounded_predictor_is_initial_guess_only=True,
        total_bath_heat_into_reactor_J=heat_sensible+heat_reaction,
        equilibrium_interpolation="log_fugacity_linear_in_temperature_without_extrapolation",
        total_volume_fixed=True,temperature_is_external_bath_boundary=True,
        bath_heat_is_required_thermostat_exchange_not_predicted_cooling_capacity=True,
        formation_law="calibrated_accessible_water_area_times_positive_fugacity_affinity",
        dissociation_law="calibrated_reactive_hydrate_area_times_negative_fugacity_affinity",
        source_formation_reference_doi="10.1016/0009-2509(87)87015-X",
        nucleation_delay_or_spatial_transport_solved=False,numerical_validation_completed=False)
    body["record_digest"]=digest(body)
    if len(json.dumps(body,allow_nan=False).encode("utf-8"))>q.maximum_result_bytes:
        raise ValueError("hydrate kinetic receipt exceeds its retained-byte budget")
    return body


def verify_hydrate_kinetics_record(record, *, request=None, context=None):
    if digest({k:v for k,v in record.items() if k!="record_digest"})!=record.get("record_digest"):
        raise ValueError("hydrate kinetic receipt changed")
    q=HydrateKineticsRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate_json(json.dumps(record["context"]))
    if request is not None and digest(request)!=digest(q):
        raise ValueError("hydrate kinetic caller request differs")
    if context is not None and digest(context)!=digest(c):
        raise ValueError("hydrate kinetic caller context differs")
    if record.get("status")=="withheld":
        raise ValueError("withheld hydrate kinetics require diagnosis before replay qualification")
    if evaluate_hydrate_kinetics(q,c)!=record:
        raise ValueError("hydrate reaction/property/deposition replay differs")
    return True


def bind_hydrate_deposit_inventory(record, template, context: RunContext, *, source_evidence_id: str,
                                  mapping_evidence_id: str, cylindrical_reactor_and_deposit_geometry_reviewed: bool):
    """Project the actual deposited-phase ledger onto the existing bore inventory."""
    from .deposition_inventory import DepositInventoryRequest, DepositWindow, evaluate_deposit_inventory
    source_input=record
    source_input_pin=digest(record)
    template_input_pin=digest(template)
    context_input_pin=digest(context)
    record=json_value(record)
    verify_hydrate_kinetics_record(record)
    q=HydrateKineticsRequest.model_validate_json(json.dumps(record["request"]))
    c=RunContext.model_validate(context.model_dump(mode="python"))
    require_context_extension(RunContext.model_validate_json(json.dumps(record["context"])),c)
    base=DepositInventoryRequest.model_validate(template.model_dump(mode="python"))
    evidence={e.id:e for e in c.evidence}
    if not cylindrical_reactor_and_deposit_geometry_reviewed:
        raise ValueError("hydrate reactor-to-bore geometry and deposit density require review")
    if source_evidence_id not in evidence or evidence[source_evidence_id].sha256!=record["record_digest"] or mapping_evidence_id not in evidence or evidence[mapping_evidence_id].sha256 is None:
        raise ValueError("hydrate deposit mapping requires exact source receipt and review evidence pins")
    if (base.asset_id,base.clock_id,base.start_time_s)!=(q.asset_id,q.clock_id,q.start_time_s):
        raise ValueError("hydrate reactor and bore inventories differ in asset or clock")
    volume=math.pi*base.clean_inner_diameter_m**2*base.length_m/4
    if not math.isclose(volume,q.total_volume_m3,rel_tol=1e-10,abs_tol=1e-12) or base.deposit_density_kg_m3!=q.hydrate_density_kg_m3:
        raise ValueError("hydrate reactor volume and deposited solid density must match the mapped pipe bore")
    if any(w.deposition_kg_s or w.removal_kg_s or w.cleaning_fraction_at_end for w in base.windows):
        raise ValueError("hydrate bore template cannot contain a competing transfer schedule")
    if not record["ledgers"]:
        raise ValueError("hydrate bore projection requires a complete accepted kinetic trajectory")
    if len(record["ledgers"])>10000:
        raise ValueError("kinetic endpoints exceed the original bore-inventory window budget; map a reviewed subwindow")
    mass_per_mol=record["hydrate_molar_mass_kg_mol"]
    windows=tuple(DepositWindow(id=base.id+"/kinetic/"+str(i),duration_s=row["duration_s"],
        deposition_kg_s=row["deposited_transfer_mol"]*mass_per_mol/row["duration_s"],
        removal_kg_s=(row["eroded_transfer_mol"]+row["dissociated_deposit_mol"])*mass_per_mol/row["duration_s"],
        evidence_id=mapping_evidence_id) for i,row in enumerate(record["ledgers"]))
    payload=base.model_dump(mode="python"); payload["windows"]=windows
    payload["evidence_ids"]=tuple(dict.fromkeys((*base.evidence_ids,source_evidence_id,mapping_evidence_id)))
    derived=DepositInventoryRequest.model_validate(payload)
    projection=evaluate_deposit_inventory(derived,c)
    projected=projection["states"]
    if len(projected)!=len(record["states"]):
        raise ValueError("zero-cleaning bore projection must retain exactly one row per kinetic endpoint")
    cumulative_residual_allowance=0.0
    maximum_mass_difference=0.0
    for i,(row,endpoint) in enumerate(zip(record["states"],projected,strict=True)):
        expected_phase="initial" if i==0 else "before_cleaning"
        if endpoint["phase"]!=expected_phase or not math.isclose(endpoint["time_s"],row["time_s"],rel_tol=0,abs_tol=1e-9):
            raise ValueError("hydrate and original bore projection endpoints differ in clock or phase")
        if i:
            cumulative_residual_allowance+=abs(record["ledgers"][i-1]["extent_residual_mol"][1])*mass_per_mol
        tolerance=(mass_per_mol*q.absolute_extent_residual_tolerance_mol+cumulative_residual_allowance
                   +q.relative_extent_residual_tolerance*max(1.0,row["deposited_mass_kg"]))
        difference=abs(endpoint["deposit_mass_kg"]-row["deposited_mass_kg"])
        maximum_mass_difference=max(maximum_mass_difference,difference)
        if difference>tolerance:
            raise ValueError("kinetic deposited mass and original bore inventory do not reconcile")
    receipt=dict(schema="pipeline_integrity.hydrate_deposit_binding.v1",source_receipt_digest=record["record_digest"],
                 template_digest=digest(base),derived_request_digest=digest(derived),context_digest=digest(c),
                 projection=projection,interval_mean_transfer_rates=True,within_step_rate_shape_inferred=False,
                 maximum_endpoint_mass_difference_kg=maximum_mass_difference,
                 cumulative_actual_deposition_equation_residual_allowance_kg=cumulative_residual_allowance,
                 projection_mass_adjustment_applied=False)
    if digest(template)!=template_input_pin or digest(context)!=context_input_pin or digest(source_input)!=source_input_pin or digest(record)!=source_input_pin:
        raise ValueError("hydrate deposit mapping source/template/context changed during projection")
    receipt["record_digest"]=digest(receipt)
    return derived,c,receipt


def hydrate_kinetics_signal_frames(record, *, stream_id: str):
    """Expose complete accepted reactive states without promoting them to measurements."""
    source_input=record
    source_input_pin=digest(record)
    record=json_value(record)
    verify_hydrate_kinetics_record(record)
    if record.get("status")!="computed_diagnostic":
        raise ValueError("hydrate process frames require a complete kinetic calculation")
    q=HydrateKineticsRequest.model_validate_json(json.dumps(record["request"]))
    units={"pressure_pa_abs":"Pa","temperature_K":"K","internal_energy_J":"J",
           "gas_volume_m3":"m^3","guest_gas_mol":"mol","liquid_water_mol":"mol",
           "total_hydrate_mol":"mol","suspended_hydrate_mol":"mol",
           "deposited_hydrate_mol":"mol","deposited_mass_kg":"kg"}
    frames=tuple(SignalFrame(stream_id=stream_id,clock_id=q.clock_id,time_s=row["time_s"],
        values={key:row[key] for key in units},units=units,valid=dict.fromkeys(units,True),
        origin="simulation",operating_mode=row["window_id"]) for row in record["states"])
    receipt=dict(schema="pipeline_integrity.hydrate_kinetics_frames.v1",source_record_digest=record["record_digest"],
        stream_id=stream_id,asset_id=q.asset_id,clock_id=q.clock_id,frames_digest=digest(frames),frame_count=len(frames),
        pressure_reference="absolute",energy_reference_id=q.energy_reference_id,
        hydrate_molar_basis=record["hydrate_molar_basis"],origin="simulation",
        calibrated_or_measured_sensor_values_inferred=False)
    receipt=json_value(receipt)
    if digest(source_input)!=source_input_pin or digest(record)!=source_input_pin:
        raise ValueError("hydrate process source changed during frame conversion")
    receipt["record_digest"]=digest(receipt)
    return frames,receipt
