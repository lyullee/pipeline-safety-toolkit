"""Explicit liquid-dew/ice-frost observations and ideal dry-carrier water ledgers."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field,model_validator

from .contracts import Contract,Name,Positive,Nonnegative
from .thermo import StateRequest,CheckedProvider,CoolPropProvider,ThermodynamicState
from .gas_caloric_mixture import (GasCaloricComponent,GasCaloricMixtureRequest,
    GasCaloricAmountComponent,GasCaloricAmountsRequest,GasCaloricMixtureResult,
    evaluate_gas_caloric_mixture,gas_caloric_mixture_from_amounts,R_UNIVERSAL_J_MOL_K)

WaterReferencePhase=Literal['liquid_water','ice_Ih']


class WaterSaturationPoint(Contract):
    reference_phase: WaterReferencePhase
    temperature_K: Positive
    equilibrium_vapor_pressure_Pa_abs: Positive
    provider_version: Name
    provider_revision: Name
    liquid_boundary_state: ThermodynamicState | None = None


def observe_water_saturation(temperature_K: float,reference_phase: WaterReferencePhase, *, property_provider=None) -> WaterSaturationPoint:
    """Reuse existing water EOS saturation or native IAPWS ice sublimation.

    Liquid-water support begins at the triple point and ends below the critical
    point. Ice uses CoolProp's existing IAPWS sublimation primitive on 50 K to
    the triple point. No supercooled-liquid fit, air enhancement/fugacity factor
    or copied pressure table is substituted for either explicitly named phase.
    """
    from CoolProp.CoolProp import PropsSI,get_global_param_string
    from CoolProp.HumidAirProp import HAProps_Aux
    from importlib.metadata import version
    t=float(temperature_K)
    if not math.isfinite(t) or t<=0:raise ValueError('positive finite Kelvin temperature required')
    triple=float(PropsSI('Ttriple','Water'));critical=float(PropsSI('Tcrit','Water'))
    if reference_phase=='liquid_water':
        if not triple<=t<critical:raise ValueError('liquid-water saturation requires triple <= T < critical; frost is a different phase')
        provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
        state=CheckedProvider(provider).resolve(StateRequest(fluid='Water',backend='HEOS',inputs={'T':t,'Q':0.},single_phase_required=False))
        if state.phase!='twophase' or not math.isclose(state.temperature_k,t,rel_tol=1e-11,abs_tol=1e-7):
            raise ValueError('provider must retain the prescribed pure-water saturation boundary and temperature')
        return WaterSaturationPoint(reference_phase=reference_phase,temperature_K=t,
            equilibrium_vapor_pressure_Pa_abs=state.pressure_pa_abs,provider_version=state.provider_version,
            provider_revision=state.provider_revision,liquid_boundary_state=state)
    if reference_phase!='ice_Ih':raise ValueError('explicit liquid_water or ice_Ih reference phase required')
    if not 50.<=t<=triple:raise ValueError('native ice-Ih sublimation pressure supports 50 K through the triple point')
    # The auxiliary psub_Ice output is PURE ice-vapor equilibrium pressure,
    # unlike HAPropsSI's enhanced saturation fraction in humid air.
    p,unit=HAProps_Aux('psub_Ice',t,101325.,0.)
    if unit!='Pa' or not math.isfinite(p) or p<=0:raise ValueError('native ice sublimation pressure is not positive finite Pa')
    return WaterSaturationPoint(reference_phase=reference_phase,temperature_K=t,
        equilibrium_vapor_pressure_Pa_abs=p,provider_version=version('CoolProp'),provider_revision=get_global_param_string('gitrevision'))


class WaterVaporObservationRequest(Contract):
    gas_temperature_K: Positive | None
    total_pressure_Pa_abs: Positive | None
    humidity_reference_phase: WaterReferencePhase
    condensation_reference_phase: WaterReferencePhase
    relative_humidity_fraction: float | None = Field(default=None,ge=0,le=1)
    water_partial_pressure_Pa_abs: Nonnegative | None = None
    water_mole_fraction: float | None = Field(default=None,ge=0,lt=1)
    condensation_point_temperature_K: Positive | None = None
    saturated_at_gas_temperature: bool = False
    infer_condensation_point: bool = True
    condensation_temperature_bracket_K: tuple[Positive,Positive] | None = None
    maximum_root_iterations: int = Field(default=100,ge=1,le=1000)
    ideal_partial_pressure_equilibrium_applicability_reviewed: bool = False
    ideal_mixture_pressure_support_Pa_abs: tuple[Positive,Positive] | None = None
    property_basis_id: Name
    dry_quantity: Nonnegative | None = None
    dry_quantity_basis: Literal['molar_amount_mol','mass_amount_kg','molar_flow_mol_s','mass_flow_kg_s'] = 'mass_flow_kg_s'
    dry_molar_mass_kg_mol: Positive | None = None
    quantity_clock_id: Name | None = None
    dry_carrier_excludes_water_and_molar_mass_basis_reviewed: bool = False

    @model_validator(mode='after')
    def selected_coordinate(self):
        selected=sum(v is not None for v in (self.relative_humidity_fraction,self.water_partial_pressure_Pa_abs,
            self.water_mole_fraction,self.condensation_point_temperature_K))+self.saturated_at_gas_temperature
        if selected>1:raise ValueError('select exactly one independent moisture coordinate; inconsistent redundant values are not reconciled')
        for values in (self.condensation_temperature_bracket_K,self.ideal_mixture_pressure_support_Pa_abs):
            if values is not None and values[0]>=values[1]:raise ValueError('ordered positive support bracket required')
        return self


class WaterVaporObservationResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','not_bracketed','not_converged']
    property_basis_id: Name
    gas_temperature_K: Positive | None = None
    total_pressure_Pa_abs: Positive | None = None
    humidity_reference: WaterSaturationPoint | None = None
    relative_humidity_fraction: Nonnegative | None = None
    water_partial_pressure_Pa_abs: Nonnegative | None = None
    water_vapor_mole_fraction: float | None = Field(default=None,ge=0,lt=1)
    ideal_water_vapor_density_kg_m3: Nonnegative | None = None
    condensation_reference_phase: WaterReferencePhase
    condensation_point_kind: Literal['liquid_dew_point','ice_frost_point']
    condensation_point_temperature_K: Positive | None = None
    condensation_point_status: Literal['not_requested','computed','dry_no_finite_point','needs_bracket','not_bracketed','not_converged'] = 'not_requested'
    condensation_pressure_residual_Pa: float | None = None
    dry_quantity_basis: str | None = None
    quantity_clock_id: Name | None = None
    mass_unit: Literal['kg','kg/s'] | None = None
    molar_unit: Literal['mol','mol/s'] | None = None
    dry_molar_quantity: Nonnegative | None = None
    water_molar_quantity: Nonnegative | None = None
    wet_molar_quantity: Nonnegative | None = None
    dry_mass_quantity: Nonnegative | None = None
    water_mass_quantity: Nonnegative | None = None
    wet_mass_quantity: Nonnegative | None = None
    wet_output_quantity_on_input_basis: Nonnegative | None = None
    mass_quantity_residual: float | None = None
    molar_quantity_residual: float | None = None
    wet_composition: GasCaloricMixtureResult | None = None
    issues: tuple[str,...] = ()
    nonideal_fugacity_or_condensed_inventory_inferred: Literal[False] = False
    field_dryness_or_corrosion_acceptance_inferred: Literal[False] = False


def observe_water_vapor(q: WaterVaporObservationRequest, *, property_provider=None) -> WaterVaporObservationResult:
    """Map one moisture coordinate to water partial pressure on explicit phases.

    The relation y_w=p_w/P is an explicitly reviewed ideal partial-pressure
    approximation, not a compressed humid-gas EOS or air enhancement model.
    RH's reference phase at gas temperature and the desired liquid-dew/ice-frost
    reference phase can differ. Zero water has no finite condensation point.

    Optional dry-carrier amounts/rates use the existing neutral component and
    absolute-amount mixture APIs. Only a water addition ledger is inferred: no
    evaporation energy/path, condensed phase, finite water supply or dryness
    acceptance follows. Clocks and kg versus kg/s remain distinct.
    """
    from CoolProp.CoolProp import PropsSI
    from scipy.optimize import brentq
    if not isinstance(q,WaterVaporObservationRequest):raise TypeError('WaterVaporObservationRequest required')
    q=WaterVaporObservationRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_basis_id=q.property_basis_id,gas_temperature_K=q.gas_temperature_K,
        total_pressure_Pa_abs=q.total_pressure_Pa_abs,condensation_reference_phase=q.condensation_reference_phase,
        condensation_point_kind='liquid_dew_point' if q.condensation_reference_phase=='liquid_water' else 'ice_frost_point')
    selected=any(v is not None for v in (q.relative_humidity_fraction,q.water_partial_pressure_Pa_abs,q.water_mole_fraction,q.condensation_point_temperature_K)) or q.saturated_at_gas_temperature
    if (None in (q.gas_temperature_K,q.total_pressure_Pa_abs) or not selected
        or not q.ideal_partial_pressure_equilibrium_applicability_reviewed or q.ideal_mixture_pressure_support_Pa_abs is None):
        return WaterVaporObservationResult(status='needs_data',**base,issues=('complete P/T, one moisture coordinate, ideal-mixture domain review and explicit pressure support required',))
    if not q.ideal_mixture_pressure_support_Pa_abs[0]<=q.total_pressure_Pa_abs<=q.ideal_mixture_pressure_support_Pa_abs[1]:
        return WaterVaporObservationResult(status='out_of_scope',**base,issues=('total absolute pressure outside reviewed ideal-mixture support; no automatic fugacity correction',))
    try:reference=observe_water_saturation(q.gas_temperature_K,q.humidity_reference_phase,property_provider=property_provider)
    except ValueError as error:return WaterVaporObservationResult(status='out_of_scope',**base,issues=(str(error),))
    ps=reference.equilibrium_vapor_pressure_Pa_abs
    if q.saturated_at_gas_temperature:pw=ps
    elif q.relative_humidity_fraction is not None:pw=q.relative_humidity_fraction*ps
    elif q.water_partial_pressure_Pa_abs is not None:pw=q.water_partial_pressure_Pa_abs
    elif q.water_mole_fraction is not None:pw=q.water_mole_fraction*q.total_pressure_Pa_abs
    else:
        try:pw=observe_water_saturation(q.condensation_point_temperature_K,q.condensation_reference_phase,property_provider=property_provider).equilibrium_vapor_pressure_Pa_abs
        except ValueError as error:return WaterVaporObservationResult(status='out_of_scope',**base,humidity_reference=reference,issues=(str(error),))
    if not math.isfinite(pw) or pw>=q.total_pressure_Pa_abs or pw>ps*(1+1e-12):
        return WaterVaporObservationResult(status='out_of_scope',**base,humidity_reference=reference,
            issues=('positive dry-carrier partial pressure and nonsupersaturated vapor state required; condensed or pure wet-steam inventory is not inferred',))
    y=pw/q.total_pressure_Pa_abs;rh=pw/ps
    mw=float(PropsSI('molar_mass','Water'))
    details=dict(humidity_reference=reference,relative_humidity_fraction=rh,water_partial_pressure_Pa_abs=pw,
        water_vapor_mole_fraction=y,ideal_water_vapor_density_kg_m3=pw*mw/(R_UNIVERSAL_J_MOL_K*q.gas_temperature_K))
    status='computed';issues=[]
    if q.infer_condensation_point:
        if q.condensation_point_temperature_K is not None:
            details.update(condensation_point_status='computed',condensation_point_temperature_K=q.condensation_point_temperature_K,condensation_pressure_residual_Pa=0.)
        elif pw==0:details['condensation_point_status']='dry_no_finite_point'
        elif q.condensation_temperature_bracket_K is None:
            status='needs_data';details['condensation_point_status']='needs_bracket';issues.append('explicit single-phase condensation-temperature bracket required')
        else:
            lo,hi=q.condensation_temperature_bracket_K
            try:
                low=observe_water_saturation(lo,q.condensation_reference_phase,property_provider=property_provider)
                high=observe_water_saturation(hi,q.condensation_reference_phase,property_provider=property_provider)
            except ValueError as error:return WaterVaporObservationResult(status='out_of_scope',**base,**details,issues=(str(error),))
            if not low.equilibrium_vapor_pressure_Pa_abs<high.equilibrium_vapor_pressure_Pa_abs:
                raise ValueError('selected saturation provider contradicts monotone phase bracket')
            if not low.equilibrium_vapor_pressure_Pa_abs<=pw<=high.equilibrium_vapor_pressure_Pa_abs:
                status='not_bracketed';details['condensation_point_status']='not_bracketed';issues.append('partial pressure lies outside supplied phase bracket; no extrapolated dew/frost point')
            else:
                def residual(t):
                    point=observe_water_saturation(t,q.condensation_reference_phase,property_provider=property_provider)
                    if (point.provider_version,point.provider_revision)!=(low.provider_version,low.provider_revision):raise ValueError('saturation provider revision changed during inverse')
                    return math.log(point.equilibrium_vapor_pressure_Pa_abs)-math.log(pw)
                try:t=brentq(residual,lo,hi,xtol=1e-9,rtol=1e-12,maxiter=q.maximum_root_iterations)
                except RuntimeError:
                    status='not_converged';details['condensation_point_status']='not_converged';issues.append('condensation-temperature inverse did not converge')
                else:
                    check=observe_water_saturation(t,q.condensation_reference_phase,property_provider=property_provider)
                    details.update(condensation_point_temperature_K=t,condensation_point_status='computed',condensation_pressure_residual_Pa=check.equilibrium_vapor_pressure_Pa_abs-pw)
    if q.dry_quantity is not None:
        if q.dry_molar_mass_kg_mol is None or q.quantity_clock_id is None or not q.dry_carrier_excludes_water_and_molar_mass_basis_reviewed:
            status='needs_data';issues.append('dry carrier molar mass, quantity clock and nonwater-basis review required for mass/mole ledger')
        else:
            molar=q.dry_quantity_basis.startswith('molar');flow=q.dry_quantity_basis.endswith('_s')
            nd=q.dry_quantity if molar else q.dry_quantity/q.dry_molar_mass_kg_mol
            nw=nd*y/(1-y)
            if not all(math.isfinite(v) for v in (nd,nw)):raise ValueError('wet gas molar inventory exceeds finite support')
            if nd>0:
                mixed=gas_caloric_mixture_from_amounts(GasCaloricAmountsRequest(mode='molar_mass_only',
                    quantity_basis='molar_flow_mol_s' if flow else 'molar_amount_mol',
                    components=(GasCaloricAmountComponent(id='dry_carrier',input_quantity=nd,molar_mass_kg_mol=q.dry_molar_mass_kg_mol),
                        GasCaloricAmountComponent(id='Water',input_quantity=nw,molar_mass_kg_mol=mw)),common_clock_id=q.quantity_clock_id,
                    property_basis_id=q.property_basis_id,temperature_K=q.gas_temperature_K,nonreacting_component_composition_ledger_reviewed=True))
                composition=mixed.mixture;nt=mixed.total_supplied_quantity
            else:
                composition=evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(mode='molar_mass_only',fraction_basis='mole',
                    components=(GasCaloricComponent(id='dry_carrier',fraction=1-y,molar_mass_kg_mol=q.dry_molar_mass_kg_mol),
                        GasCaloricComponent(id='Water',fraction=y,molar_mass_kg_mol=mw)),temperature_K=q.gas_temperature_K,
                    property_basis_id=q.property_basis_id,nonreacting_component_composition_ledger_reviewed=True));nt=0.
            md=nd*q.dry_molar_mass_kg_mol;mwq=nw*mw;mt=nt*composition.mixture_molar_mass_kg_mol
            if not all(math.isfinite(v) for v in (md,mwq,mt)):raise ValueError('wet gas mass inventory exceeds finite support')
            mass_res=math.fsum((mt,-md,-mwq));molar_res=math.fsum((nt,-nd,-nw))
            if abs(mass_res)>1e-12*max(mt,1e-300) or abs(molar_res)>1e-12*max(nt,1e-300):raise ValueError('water/dry carrier mass or molar budget failed')
            details.update(dry_quantity_basis=q.dry_quantity_basis,quantity_clock_id=q.quantity_clock_id,mass_unit='kg/s' if flow else 'kg',
                molar_unit='mol/s' if flow else 'mol',dry_molar_quantity=nd,water_molar_quantity=nw,wet_molar_quantity=nt,
                dry_mass_quantity=md,water_mass_quantity=mwq,wet_mass_quantity=mt,wet_output_quantity_on_input_basis=nt if molar else mt,
                mass_quantity_residual=mass_res,molar_quantity_residual=molar_res,wet_composition=composition)
    repeated=observe_water_saturation(q.gas_temperature_K,q.humidity_reference_phase,property_provider=property_provider)
    if repeated!=reference:raise ValueError('water saturation state/provider changed during observation')
    return WaterVaporObservationResult(status=status,**base,**details,issues=tuple(issues))


def moisture_gas_basis_state(observation: WaterVaporObservationResult, *, id: str,state_evidence_id: str,
    water_fraction_evidence_id: str,gas_phase_evidence_id: str,single_gas_state_reviewed: bool,
    compressibility_Z_common_R: float | None = None,compressibility_evidence_id: str | None = None):
    """Supply the explicit water fraction to the existing wet/dry volume owner.

    The existing gas_basis solver retains its own common-R Z, composition,
    clock and evidence checks; saturation observation does not impose a mixture
    phase or silently choose Z=1 for an unrelated compressed gas.
    """
    from .gas_basis import GasBasisState
    observation=WaterVaporObservationResult.model_validate(observation.model_dump(mode='python'))
    if observation.status!='computed' or observation.water_vapor_mole_fraction is None or not single_gas_state_reviewed:
        raise ValueError('computed moisture fraction and separately reviewed mixture single-gas state required')
    return GasBasisState(id=id,pressure_pa_abs=observation.total_pressure_Pa_abs,temperature_K=observation.gas_temperature_K,
        water_vapor_mole_fraction=observation.water_vapor_mole_fraction,volume_basis='dry_gas' if observation.water_vapor_mole_fraction==0 else 'wet_gas',
        state_evidence_id=state_evidence_id,water_fraction_evidence_id=water_fraction_evidence_id,gas_phase_evidence_id=gas_phase_evidence_id,
        compressibility_Z_common_R=compressibility_Z_common_R,compressibility_evidence_id=compressibility_evidence_id)
