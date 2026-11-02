"""Clearance-limited ideal-valve intake using EOS entropy and enthalpy states."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from scipy.optimize import brentq
from .contracts import Contract,Name,Positive,Nonnegative
from .thermo import CheckedProvider,CoolPropProvider,StateRequest,ThermodynamicState
from .gas_thermal_observation import checked_gas_thermal_state,GasThermalStateOutOfScope
from .volume_weighting import normalized_weighted_sum


class GasClearanceIntakeRequest(Contract):
    fluid: Name
    backend: Literal['HEOS','IF97','PR','SRK'] = 'HEOS'
    swept_volume_m3: Positive
    clearance_volume_m3: Nonnegative
    suction_pressure_Pa_abs: Positive | None
    suction_temperature_K: Positive | None
    residual_discharge_pressure_Pa_abs: Positive | None
    residual_discharge_temperature_K: Positive | None
    property_basis_id: Name
    chamber_geometry_basis_id: Name
    mode: Literal['per_cycle','rate'] = 'per_cycle'
    intake_cycles_per_second: Nonnegative | None = None
    cycle_clock_id: Name | None = None
    repeatable_given_states_per_cycle_reviewed: bool = False
    same_composition_single_phase_ideal_valves_adiabatic_reexpansion_isobaric_intake_reviewed: bool = False
    continuous_increasing_filled_volume_with_admitted_mass_reviewed: bool = False
    absolute_fresh_mass_tolerance_kg: Positive = 1e-12
    relative_fresh_mass_tolerance: float = Field(default=1e-11,ge=8.881784197001252e-16,lt=1e-3)
    maximum_bracket_expansions: int = Field(default=32,ge=0,le=1000)
    initial_fresh_mass_upper_kg: Positive | None = None
    maximum_solver_iterations: int = Field(default=100,ge=1,le=10000)
    absolute_volume_tolerance_m3: Positive = 1e-10
    relative_volume_tolerance: Nonnegative = Field(default=1e-9,lt=1e-3)
    negative_entropy_generation_tolerance_J_K: Nonnegative = 1e-8

    @model_validator(mode='after')
    def compression(self):
        if self.clearance_volume_m3>0 and None not in (self.suction_pressure_Pa_abs,self.residual_discharge_pressure_Pa_abs):
            if self.residual_discharge_pressure_Pa_abs<self.suction_pressure_Pa_abs:
                raise ValueError('residual discharge pressure must not be below suction pressure')
        return self


class GasClearanceIntakeResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','no_bracket','not_converged']
    property_basis_id: Name
    chamber_geometry_basis_id: Name
    cycle_clock_id: Name | None = None
    regime: Literal['no_clearance','fresh_intake','no_suction_admission'] | None = None
    residual_mass_kg: Nonnegative | None = None
    reexpansion_volume_at_suction_pressure_m3: Nonnegative | None = None
    fresh_intake_mass_per_cycle_kg: Nonnegative | None = None
    fresh_intake_suction_volume_per_cycle_m3: Nonnegative | None = None
    ideal_clearance_volumetric_efficiency: Nonnegative | None = None
    ideal_fresh_mass_flow_kg_s: Nonnegative | None = None
    ideal_suction_volume_flow_m3_s: Nonnegative | None = None
    reexpansion_work_by_residual_gas_J: float | None = None
    isobaric_intake_work_by_gas_J: Nonnegative | None = None
    intake_volume_budget_residual_m3: float | None = None
    intake_energy_budget_residual_J: float | None = None
    intake_entropy_generation_J_K: float | None = None
    reexpansion_entropy_residual_J_kg_K: float | None = None
    supply_state: ThermodynamicState | None = None
    residual_discharge_state: ThermodynamicState | None = None
    reexpansion_state_at_suction_pressure: ThermodynamicState | None = None
    full_stroke_state_after_intake: ThermodynamicState | None = None
    issues: tuple[str,...] = ()
    empirical_filling_or_leak_loss_inferred: Literal[False] = False


def evaluate_gas_clearance_intake(q: GasClearanceIntakeRequest, *, property_provider=None) -> GasClearanceIntakeResult:
    """Preserve residual mass, expand it isentropically, then close a P-fixed intake.

    Known chamber volumes are geometry observations. During ideal intake the
    enthalpy of admitted gas adds to residual enthalpy; EOS density fixes the
    final occupied volume. This retains piston boundary work and thermal mixing,
    rather than setting the final gas temperature equal to the supply temperature.
    No empirical filling deduction, gas slippage or valve-rating table is selected.
    This is one intake observation; the subsequent compression/discharge and
    periodic temperature consistency are not solved. Rate conversion additionally
    requires reviewed repeatability of the supplied states and a common clock.
    """
    if not isinstance(q,GasClearanceIntakeRequest):raise TypeError('GasClearanceIntakeRequest required')
    q=GasClearanceIntakeRequest.model_validate(q.model_dump(mode='python'))
    base=dict(property_basis_id=q.property_basis_id,chamber_geometry_basis_id=q.chamber_geometry_basis_id)
    if (None in (q.suction_pressure_Pa_abs,q.suction_temperature_K)
        or not q.same_composition_single_phase_ideal_valves_adiabatic_reexpansion_isobaric_intake_reviewed
        or (q.clearance_volume_m3>0 and (None in (q.residual_discharge_pressure_Pa_abs,q.residual_discharge_temperature_K)
            or not q.continuous_increasing_filled_volume_with_admitted_mass_reviewed))):
        return GasClearanceIntakeResult(status='needs_data',**base,issues=('complete reviewed supply/residual states and ideal-valve reexpansion/intake closure required',))
    provider=property_provider if property_provider is not None else CoolPropProvider(use_cache=False)
    identity=[]
    def resolve(inputs):
        request=StateRequest(fluid=q.fluid,backend=q.backend,inputs=inputs,single_phase_required=False)
        if 'Smass' not in inputs:
            s=checked_gas_thermal_state(request,property_provider=provider,
                expected_provider_identity=identity[0] if identity else None)
        else:
            s=CheckedProvider(provider).resolve(request)
            if s.phase not in ('gas','supercritical_gas','supercritical'):
                raise GasThermalStateOutOfScope('reexpansion must remain on the reviewed single gas/supercritical branch')
            if not math.isclose(s.pressure_pa_abs,inputs['P'],rel_tol=1e-10,abs_tol=1e-5):raise ValueError('provider changed reexpansion pressure')
            if not math.isclose(s.entropy_j_kg_k,inputs['Smass'],rel_tol=1e-10,abs_tol=1e-7):raise ValueError('provider changed prescribed reexpansion entropy')
            if identity and identity[0]!=(s.provider_version,s.provider_revision):raise ValueError('common property reference revision changed')
        identity.append((s.provider_version,s.provider_revision));return s
    try:
        supply=resolve({'P':q.suction_pressure_Pa_abs,'T':q.suction_temperature_K})
        nominal_mass=supply.density_kg_m3*q.swept_volume_m3
        vmax=math.fsum((q.swept_volume_m3,q.clearance_volume_m3))
        if not math.isfinite(nominal_mass) or nominal_mass<=0 or not math.isfinite(vmax):raise ValueError('chamber inventory exceeds finite support')
        common=dict(supply_state=supply)
        if q.clearance_volume_m3==0:
            fresh=nominal_mass;volume=q.swept_volume_m3
            details=dict(regime='no_clearance',residual_mass_kg=0.,reexpansion_volume_at_suction_pressure_m3=0.,
                reexpansion_work_by_residual_gas_J=0.,isobaric_intake_work_by_gas_J=q.suction_pressure_Pa_abs*volume,
                intake_volume_budget_residual_m3=0.,intake_energy_budget_residual_J=0.,intake_entropy_generation_J_K=0.,
                full_stroke_state_after_intake=supply)
        else:
            residual=resolve({'P':q.residual_discharge_pressure_Pa_abs,'T':q.residual_discharge_temperature_K})
            expanded=resolve({'P':q.suction_pressure_Pa_abs,'Smass':residual.entropy_j_kg_k})
            mass=residual.density_kg_m3*q.clearance_volume_m3;opening=mass/expanded.density_kg_m3
            if not all(math.isfinite(v) and v>0 for v in (mass,opening)):raise ValueError('residual gas inventory exceeds finite support')
            common.update(residual_discharge_state=residual,reexpansion_state_at_suction_pressure=expanded,
                residual_mass_kg=mass,reexpansion_volume_at_suction_pressure_m3=opening,
                reexpansion_entropy_residual_J_kg_K=expanded.entropy_j_kg_k-residual.entropy_j_kg_k)
            if opening>=vmax:
                fresh=volume=0.;details=dict(regime='no_suction_admission')
            else:
                def filled(admitted):
                    if admitted==0:return expanded,opening
                    total=mass+admitted
                    h=normalized_weighted_sum({'residual':expanded.enthalpy_j_kg,'fresh':supply.enthalpy_j_kg},
                        {'residual':mass/total,'fresh':admitted/total})
                    state=resolve({'P':q.suction_pressure_Pa_abs,'Hmass':h})
                    return state,total/state.density_kg_m3
                upper=q.initial_fresh_mass_upper_kg if q.initial_fresh_mass_upper_kg is not None else max(nominal_mass,mass)
                for _ in range(q.maximum_bracket_expansions+1):
                    _,end_volume=filled(upper)
                    if end_volume>=vmax:break
                    upper*=2
                    if not math.isfinite(upper):break
                else:return GasClearanceIntakeResult(status='no_bracket',**base,**common,issues=('fresh-mass search did not bracket full-stroke volume',))
                if not math.isfinite(upper):return GasClearanceIntakeResult(status='no_bracket',**base,**common,issues=('fresh-mass bracket exceeds finite support',))
                try:
                    fresh=brentq(lambda value:filled(value)[1]-vmax,0.,upper,
                        xtol=q.absolute_fresh_mass_tolerance_kg,rtol=q.relative_fresh_mass_tolerance,maxiter=q.maximum_solver_iterations)
                except RuntimeError:
                    return GasClearanceIntakeResult(status='not_converged',**base,**common,issues=('fresh-intake mass solver did not converge',))
                end,end_volume=filled(fresh);volume=fresh/supply.density_kg_m3;total=mass+fresh
                if abs(end_volume-vmax)>q.absolute_volume_tolerance_m3+q.relative_volume_tolerance*vmax:
                    return GasClearanceIntakeResult(status='not_converged',**base,**common,
                        intake_volume_budget_residual_m3=end_volume-vmax,issues=('full-stroke volume budget was not met',))
                work=q.suction_pressure_Pa_abs*(vmax-opening)
                u_end=end.enthalpy_j_kg-q.suction_pressure_Pa_abs/end.density_kg_m3
                u_open=expanded.enthalpy_j_kg-q.suction_pressure_Pa_abs/expanded.density_kg_m3
                details=dict(regime='fresh_intake',full_stroke_state_after_intake=end,
                    isobaric_intake_work_by_gas_J=work,intake_volume_budget_residual_m3=end_volume-vmax,
                    intake_energy_budget_residual_J=math.fsum((total*u_end,-mass*u_open,-fresh*supply.enthalpy_j_kg,work)),
                    intake_entropy_generation_J_K=math.fsum((total*end.entropy_j_kg_k,-mass*expanded.entropy_j_kg_k,-fresh*supply.entropy_j_kg_k)),
                    reexpansion_work_by_residual_gas_J=mass*((residual.enthalpy_j_kg-residual.pressure_pa_abs/residual.density_kg_m3)
                        -(expanded.enthalpy_j_kg-expanded.pressure_pa_abs/expanded.density_kg_m3)))
                if details['intake_entropy_generation_J_K'] < -q.negative_entropy_generation_tolerance_J_K:
                    return GasClearanceIntakeResult(status='out_of_scope',**base,**common,**details,
                        issues=('supplied property closure violates the adiabatic intake entropy budget',))
    except GasThermalStateOutOfScope as exc:
        return GasClearanceIntakeResult(status='out_of_scope',**base,issues=(str(exc),))
    values=dict(fresh_intake_mass_per_cycle_kg=fresh,fresh_intake_suction_volume_per_cycle_m3=volume,
        ideal_clearance_volumetric_efficiency=fresh/nominal_mass)
    rate_ready=(q.intake_cycles_per_second is not None and q.cycle_clock_id is not None
        and q.repeatable_given_states_per_cycle_reviewed)
    if rate_ready:
        values.update(ideal_fresh_mass_flow_kg_s=fresh*q.intake_cycles_per_second,
            ideal_suction_volume_flow_m3_s=volume*q.intake_cycles_per_second)
    status='needs_data' if q.mode=='rate' and not rate_ready else 'computed'
    return GasClearanceIntakeResult(status=status,**base,**common,**details,**values,
        cycle_clock_id=q.cycle_clock_id,
        issues=('repeatable cycle states, cycle clock and rate required; per-event observations retained',) if status=='needs_data' else ())
