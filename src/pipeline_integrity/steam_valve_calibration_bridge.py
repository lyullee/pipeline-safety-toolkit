"""Cv observations through an explicit state-specific valve calibration.

No universal mapping from a water-rated flow coefficient to steam discharge
area exists in this adapter. The complete mapping is supplied and reviewed.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract,Name,Nonnegative,Positive
from .steam_nozzle import SteamNozzleRequest,SteamNozzleResult,evaluate_steam_nozzle


class SteamValveAreaState(Contract):
    valve_id: Name
    Cv_definition_id: Name
    calibration_id: Name
    revision: Name
    Cv: Nonnegative
    stagnation_pressure_Pa_abs: Positive
    stagnation_temperature_K: Positive
    back_pressure_Pa_abs: Positive
    effective_discharge_area_m2: Nonnegative


class SteamValveObservationRequest(Contract):
    mode: Literal['capacity_from_Cv','Cv_from_mass_flow']
    nozzle: SteamNozzleRequest
    valve_id: Name
    Cv_definition_id: Name
    calibration_id: Name | None
    state_specific_valve_Cv_area_calibration_and_nozzle_basis_reviewed: bool = False
    calibration_monotonic_bracket_reviewed: bool = False
    Cv: Nonnegative | None = None
    target_mass_flow_kg_s: Nonnegative | None = None
    Cv_bracket: tuple[Nonnegative,Nonnegative] | None = None

    @model_validator(mode='after')
    def coordinates(self):
        if self.nozzle.effective_flow_area_m2 is not None or self.nozzle.target_mass_flow_kg_s is not None:
            raise ValueError('The base nozzle state must not include an independent area or mass-flow coordinate.')
        if self.mode=='capacity_from_Cv' and (self.target_mass_flow_kg_s is not None or self.Cv_bracket is not None):
            raise ValueError('Forward Cv capacity has no inverse target or bracket.')
        if self.mode=='Cv_from_mass_flow' and self.Cv is not None:raise ValueError('Inverse Cv has no prescribed Cv.')
        if self.Cv_bracket is not None and self.Cv_bracket[0]>=self.Cv_bracket[1]:raise ValueError('Cv bracket must strictly increase.')
        return self


class SteamValveObservationResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','not_bracketed','not_identifiable','no_pressure_drive']
    valve_id: Name
    Cv_definition_id: Name
    calibration_id: Name | None
    calibration_revision: Name | None = None
    Cv: Nonnegative | None = None
    effective_discharge_area_m2: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    mass_flow_residual_kg_s: float | None = None
    nozzle: SteamNozzleResult | None = None
    issues: tuple[str,...] = ()
    universal_Cv_area_or_legacy_steam_fit_inferred: Literal[False] = False
    device_calibration_or_field_qualified: Literal[False] = False


def observe_calibrated_steam_valve(q:SteamValveObservationRequest,*,calibration_provider=None,property_provider=None)->SteamValveObservationResult:
    """Use a supplied fixed-state Cv→CdA curve with the existing steam EOS law.

Provider ``area(Cv, stagnation_pressure_Pa_abs=..., stagnation_temperature_K=...,
back_pressure_Pa_abs=...)`` returns SteamValveAreaState. Its valve, state,
revision and calibration identities must remain fixed. The inverse uses one
caller-reviewed strictly increasing Cv bracket; a finite grid support check
does not prove global monotonicity. No vendor curve or pressure recovery model
is guessed. Wet/saturated inlet and other unsupported nozzle states remain
withheld by the same thermodynamic owner.
"""
    from scipy.optimize import brentq
    q=SteamValveObservationRequest.model_validate(q.model_dump(mode='python'))
    base=dict(valve_id=q.valve_id,Cv_definition_id=q.Cv_definition_id,calibration_id=q.calibration_id)
    if calibration_provider is None or q.calibration_id is None or not q.state_specific_valve_Cv_area_calibration_and_nozzle_basis_reviewed:
        return SteamValveObservationResult(status='needs_data',**base,issues=('State-specific reviewed valve Cv/effective-area calibration required; Cv is not assumed to be a geometric area.',))
    required=q.Cv if q.mode=='capacity_from_Cv' else q.target_mass_flow_kg_s
    if required is None or (q.mode=='Cv_from_mass_flow' and (q.Cv_bracket is None or not q.calibration_monotonic_bracket_reviewed)):
        return SteamValveObservationResult(status='needs_data',**base,issues=('Selected Cv/flow coordinate and reviewed inverse bracket required.',))
    response=evaluate_steam_nozzle(q.nozzle,property_provider=property_provider)
    if response.status!='computed':return SteamValveObservationResult(status='out_of_scope' if response.status!='needs_data' else 'needs_data',**base,nozzle=response,issues=response.issues)
    flux=response.ideal_mass_flux_kg_m2_s
    if flux==0:
        if q.mode=='capacity_from_Cv':return SteamValveObservationResult(status='computed',**base,Cv=q.Cv,mass_flow_kg_s=0.,nozzle=response)
        return SteamValveObservationResult(status='no_pressure_drive' if q.target_mass_flow_kg_s else 'not_identifiable',**base,nozzle=response,issues=('No unique finite Cv is inferred at zero pressure drive.',))
    identity=None
    def area(cv):
        nonlocal identity
        s=calibration_provider.area(cv,stagnation_pressure_Pa_abs=q.nozzle.stagnation_pressure_Pa_abs,
            stagnation_temperature_K=q.nozzle.stagnation_temperature_K,back_pressure_Pa_abs=q.nozzle.back_pressure_Pa_abs)
        if not isinstance(s,SteamValveAreaState):raise ValueError('Calibration provider must return a typed area/state observation.')
        s=SteamValveAreaState.model_validate(s.model_dump(mode='python'))
        if (s.valve_id,s.Cv_definition_id,s.calibration_id)!=(q.valve_id,q.Cv_definition_id,q.calibration_id):raise ValueError('Valve, Cv definition or calibration identity mismatch.')
        if (s.Cv,s.stagnation_pressure_Pa_abs,s.stagnation_temperature_K,s.back_pressure_Pa_abs)!=(cv,q.nozzle.stagnation_pressure_Pa_abs,q.nozzle.stagnation_temperature_K,q.nozzle.back_pressure_Pa_abs):
            raise ValueError('Calibration changed the requested Cv or total/back state.')
        if identity is not None and identity!=s.revision:raise ValueError('Valve calibration revision changed during observation.')
        identity=s.revision
        return s.effective_discharge_area_m2
    if q.mode=='capacity_from_Cv':cv=q.Cv;effective_area=area(cv)
    else:
        lo,hi=q.Cv_bracket
        values=[area(lo+(hi-lo)*i/16) for i in range(17)]
        if any(a>=b for a,b in zip(values,values[1:])):
            return SteamValveObservationResult(status='out_of_scope',**base,nozzle=response,issues=('Calibration support is not strictly increasing; no arbitrary root selected.',))
        target=q.target_mass_flow_kg_s/flux
        if target<values[0] or target>values[-1]:return SteamValveObservationResult(status='not_bracketed',**base,nozzle=response,issues=('Target flow lies outside supplied Cv calibration bracket.',))
        cv=brentq(lambda value:area(value)-target,lo,hi,xtol=1e-11,rtol=1e-12)
        effective_area=area(cv)
    flow=effective_area*flux
    if not math.isfinite(flow):raise ValueError('Capacity exceeds finite numerical support.')
    residual=None if q.target_mass_flow_kg_s is None else flow-q.target_mass_flow_kg_s
    return SteamValveObservationResult(status='computed',**base,calibration_revision=identity,Cv=cv,effective_discharge_area_m2=effective_area,mass_flow_kg_s=flow,mass_flow_residual_kg_s=residual,nozzle=response)
