"""Conditional single-phase steam nozzle capacity using the actual water EOS."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import Field, model_validator

from .contracts import Contract,Name,Nonnegative,Positive
from .gas_thermal_observation import checked_gas_thermal_state,GasThermalStateOutOfScope
from .thermo import CheckedProvider,CoolPropProvider,StateRequest,ThermodynamicState


class SteamNozzleRequest(Contract):
    fluid: Literal['Water'] = 'Water'
    backend: Literal['HEOS','IF97'] = 'HEOS'
    stagnation_pressure_Pa_abs: Positive | None
    stagnation_temperature_K: Positive | None = None
    inlet_definition: Literal['total_PT','dry_saturated_total_P'] = 'total_PT'
    back_pressure_Pa_abs: Positive | None
    pressure_support_lower_Pa_abs: Positive | None
    effective_flow_area_m2: Nonnegative | None = None
    target_mass_flow_kg_s: Nonnegative | None = None
    property_basis_id: Name
    effective_area_basis_id: Name | None = None
    adiabatic_no_work_one_dimensional_total_inlet_and_single_phase_isentrope_reviewed: bool = False
    single_sonic_maximum_on_supported_path_reviewed: bool = False
    effective_area_is_reviewed_CdA_not_inferred_from_Cv: bool = False
    pressure_scan_intervals: int = Field(default=96,ge=16,le=4096)
    maximum_root_iterations: int = Field(default=100,ge=1,le=1000)

    @model_validator(mode='after')
    def pressures(self):
        p=self.stagnation_pressure_Pa_abs
        if p is not None:
            if self.back_pressure_Pa_abs is not None and self.back_pressure_Pa_abs>p:
                raise ValueError('passive forward nozzle requires back pressure no greater than total inlet pressure')
            if self.pressure_support_lower_Pa_abs is not None and self.pressure_support_lower_Pa_abs>=p:
                raise ValueError('positive lower pressure support must be below total inlet pressure')
        if self.inlet_definition=='dry_saturated_total_P' and self.stagnation_temperature_K is not None:
            raise ValueError('dry-saturated pressure and independent total temperature are alternative inlet definitions')
        return self


class SteamNozzleResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','insufficient_pressure_support','not_converged','no_pressure_drive']
    property_basis_id: Name
    regime: Literal['zero_drive','unchoked','choked'] | None = None
    inlet_state: ThermodynamicState | None = None
    throat_state: ThermodynamicState | None = None
    ideal_mass_flux_kg_m2_s: Nonnegative | None = None
    throat_velocity_m_s: Nonnegative | None = None
    throat_mach: Nonnegative | None = None
    mass_flow_kg_s: Nonnegative | None = None
    required_effective_flow_area_m2: Nonnegative | None = None
    specific_energy_residual_J_kg: float | None = None
    entropy_residual_J_kg_K: float | None = None
    issues: tuple[str,...] = ()
    conventional_Cv_inferred: Literal[False] = False
    wet_steam_or_device_calibration_qualified: Literal[False] = False


def evaluate_steam_nozzle(q: SteamNozzleRequest, *, property_provider=None) -> SteamNozzleResult:
    """Resolve a converging nozzle's throat mass flux and conditional effective area.

    Stagnation enthalpy is converted to kinetic energy on the same entropy
    surface: u²=2(h0-h), G=rho*u. Its sonic stationary point uses the EOS sound
    speed, not Cp/Cv substituted into a constant-gamma gas formula. A reviewed
    single maximum and single-phase path are required; the pressure scan is a
    support check, not a general proof of phase topology or global uniqueness.

    Effective area is explicitly Cd*A with a supplied calibration basis. The
    nozzle law supplies no universal conversion to a water-rated valve Cv and
    does not resolve a valve vena contracta, recovery or actual discharge Cd.
    A choked result covers the inlet-to-throat path only, not downstream jet
    phase changes, shocks or condensate. Dry-saturated boundary states and any
    wet path are conservatively withheld from this single-phase EOS adapter.
    """
    from scipy.optimize import brentq
    if not isinstance(q,SteamNozzleRequest):raise TypeError('SteamNozzleRequest required')
    q=SteamNozzleRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_basis_id=q.property_basis_id)
    required=(q.stagnation_pressure_Pa_abs,q.back_pressure_Pa_abs,q.pressure_support_lower_Pa_abs)
    if (None in required or (q.inlet_definition=='total_PT' and q.stagnation_temperature_K is None)
        or not q.adiabatic_no_work_one_dimensional_total_inlet_and_single_phase_isentrope_reviewed
        or not q.single_sonic_maximum_on_supported_path_reviewed):
        return SteamNozzleResult(status='needs_data',**base,issues=('complete total inlet/back-pressure/support state and reviewed one-dimensional single-phase sonic path required',))
    if q.effective_flow_area_m2 is not None and (not q.effective_area_is_reviewed_CdA_not_inferred_from_Cv or q.effective_area_basis_id is None):
        return SteamNozzleResult(status='needs_data',**base,issues=('supplied effective CdA needs its calibration/basis review; Cv is not an area',))
    provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
    identity=None
    def resolve(inputs):
        request=StateRequest(fluid='Water',backend=q.backend,inputs=inputs,single_phase_required=False)
        try:
            if 'T' in inputs:
                state=checked_gas_thermal_state(request,property_provider=provider,expected_provider_identity=identity)
            else:
                state=CheckedProvider(provider).resolve(request)
        except GasThermalStateOutOfScope:raise
        except ValueError as error:
            raise GasThermalStateOutOfScope('water EOS could not resolve the requested supported single-phase state: '+str(error)) from error
        if state.phase not in ('gas','supercritical_gas','supercritical'):
            raise GasThermalStateOutOfScope('single vapor/gaslike EOS path required; saturation boundary, wet steam and liquid branches are withheld')
        if not math.isclose(state.pressure_pa_abs,inputs['P'],rel_tol=1e-10,abs_tol=1e-5):
            raise ValueError('provider changed prescribed steam pressure')
        if 'Smass' in inputs and not math.isclose(state.entropy_j_kg_k,inputs['Smass'],rel_tol=1e-9,abs_tol=1e-6):
            raise ValueError('provider changed prescribed steam entropy')
        if identity is not None and identity!=(state.provider_version,state.provider_revision):
            raise ValueError('water property reference revision changed on one nozzle path')
        if state.speed_of_sound_m_s is None:
            raise GasThermalStateOutOfScope('single-phase EOS sound speed required; no constant-gamma substitute')
        return state
    p0=q.stagnation_pressure_Pa_abs
    inlet_inputs={'P':p0,'T':q.stagnation_temperature_K} if q.inlet_definition=='total_PT' else {'P':p0,'Q':1.}
    try:
        inlet=resolve(inlet_inputs);identity=(inlet.provider_version,inlet.provider_revision)
        h0,s0=inlet.enthalpy_j_kg,inlet.entropy_j_kg_k
        if q.back_pressure_Pa_abs==p0:
            if q.target_mass_flow_kg_s not in (None,0):
                return SteamNozzleResult(status='no_pressure_drive',**base,inlet_state=inlet,regime='zero_drive',ideal_mass_flux_kg_m2_s=0.,issues=('positive prescribed mass flow has no finite effective area at zero pressure drive',))
            return SteamNozzleResult(status='computed',**base,regime='zero_drive',inlet_state=inlet,throat_state=inlet,
                ideal_mass_flux_kg_m2_s=0.,throat_velocity_m_s=0.,throat_mach=0.,mass_flow_kg_s=0. if q.effective_flow_area_m2 is not None else None,
                required_effective_flow_area_m2=0. if q.target_mass_flow_kg_s==0 else None,specific_energy_residual_J_kg=0.,entropy_residual_J_kg_K=0.)
        def at_pressure(p):
            state=resolve({'P':p,'Smass':s0})
            drop=h0-state.enthalpy_j_kg
            if drop<=0:raise GasThermalStateOutOfScope('positive enthalpy-to-kinetic release is unresolved on requested pressure path')
            speed=math.sqrt(2*drop)
            return state,speed,speed/state.speed_of_sound_m_s
        floor=max(q.back_pressure_Pa_abs,q.pressure_support_lower_Pa_abs)
        previous=p0;sonic=None;state=speed=mach=None
        # Descend from the reservoir and stop at the first sonic crossing. No
        # two-phase downstream expansion is needed to assert a dry throat.
        for i in range(1,q.pressure_scan_intervals+1):
            p=p0*math.exp(math.log(floor/p0)*i/q.pressure_scan_intervals)
            state,speed,mach=at_pressure(p)
            if mach>=1:
                def residual(value):
                    if value==p0:return -1.
                    return at_pressure(value)[2]-1
                try:sonic=brentq(residual,p,previous,xtol=1e-7,rtol=1e-12,maxiter=q.maximum_root_iterations)
                except RuntimeError:
                    return SteamNozzleResult(status='not_converged',**base,inlet_state=inlet,issues=('EOS sonic-pressure root did not converge',))
                state,speed,mach=at_pressure(sonic);break
            previous=p
        if sonic is None and q.back_pressure_Pa_abs<q.pressure_support_lower_Pa_abs:
            return SteamNozzleResult(status='insufficient_pressure_support',**base,inlet_state=inlet,issues=('no sonic point bracketed above declared lower EOS pressure support; cannot extrapolate to back pressure',))
        # Re-resolve total state to expose same-revision enthalpy/entropy drift.
        repeated=resolve(inlet_inputs)
        if not all(math.isclose(getattr(inlet,k),getattr(repeated,k),rel_tol=1e-10,abs_tol=1e-6) for k in ('enthalpy_j_kg','entropy_j_kg_k','density_kg_m3')):
            raise ValueError('water property total-state/reference coordinates drifted during nozzle evaluation')
        flux=state.density_kg_m3*speed
        if not math.isfinite(flux) or flux<=0:raise ValueError('steam nozzle mass flux exceeds positive finite support')
        mass=flux*q.effective_flow_area_m2 if q.effective_flow_area_m2 is not None else None
        area=q.target_mass_flow_kg_s/flux if q.target_mass_flow_kg_s is not None else None
        if any(v is not None and not math.isfinite(v) for v in (mass,area)):raise ValueError('capacity/area exceeds finite support')
        return SteamNozzleResult(status='computed',**base,regime='choked' if sonic is not None else 'unchoked',
            inlet_state=inlet,throat_state=state,ideal_mass_flux_kg_m2_s=flux,throat_velocity_m_s=speed,throat_mach=mach,
            mass_flow_kg_s=mass,required_effective_flow_area_m2=area,
            specific_energy_residual_J_kg=math.fsum((state.enthalpy_j_kg,.5*speed**2,-h0)),entropy_residual_J_kg_K=state.entropy_j_kg_k-s0)
    except GasThermalStateOutOfScope as error:
        return SteamNozzleResult(status='out_of_scope',**base,issues=(str(error),))
