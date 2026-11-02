"""Composition-aware gas state observations and fixed-mass endpoint coordinates.

No thermal trajectory, phase split or material calibration is inferred. The ideal
mode needs supplied species molar masses; HEOS mode uses existing CoolProp state
and composition adapters without changing their implementations.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive
from .gas_caloric_mixture import (GasCaloricComponent,GasCaloricMixtureRequest,
    evaluate_gas_caloric_mixture,R_UNIVERSAL_J_MOL_K)
from .thermo import CoolPropProvider,StateRequest


class GasStateObservationRequest(Contract):
    model: Literal['ideal','HEOS']
    coordinate: Literal['pressure_temperature','mass_pressure_temperature','mass_volume_temperature']
    components: tuple[GasCaloricComponent,...] = Field(min_length=1)
    fraction_basis: Literal['mole','mass']
    temperature_K: Positive
    pressure_Pa_abs: Positive | None = None
    mass_kg: Positive | None = None
    volume_m3: Positive | None = None
    inverse_pressure_bracket_Pa_abs: tuple[Positive,Positive] | None = None
    single_phase_isotherm_bracket_reviewed: bool = False
    composition_and_gas_property_domain_reviewed: bool = False
    property_basis_id: Name

    @model_validator(mode='after')
    def coordinates(self):
        # Reuse the existing composition contract before any EOS/provider call.
        # Missing constituent molar masses remain explicit unknown values.
        GasCaloricMixtureRequest(mode='molar_mass_only',fraction_basis=self.fraction_basis,
            components=self.components,property_basis_id=self.property_basis_id,
            temperature_K=self.temperature_K)
        required={'pressure_temperature':(True,False,False),'mass_pressure_temperature':(True,True,False),
            'mass_volume_temperature':(False,True,True)}[self.coordinate]
        if tuple(x is not None for x in (self.pressure_Pa_abs,self.mass_kg,self.volume_m3))!=required:
            raise ValueError('supply exactly the declared pressure/mass/volume endpoint coordinates')
        if self.inverse_pressure_bracket_Pa_abs is not None:
            if self.model!='HEOS' or self.coordinate!='mass_volume_temperature':raise ValueError('pressure bracket belongs to HEOS closed-volume inversion only')
            if self.inverse_pressure_bracket_Pa_abs[0]>=self.inverse_pressure_bracket_Pa_abs[1]:raise ValueError('pressure bracket must strictly increase')
        for c in self.components:
            if any(x in c.id for x in ('&','[',']','::','|')):raise ValueError('component identifiers cannot contain mixture or phase syntax')
        return self


class GasStateObservationResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    property_basis_id: Name
    pressure_Pa_abs: Positive | None = None
    temperature_K: Positive | None = None
    density_kg_m3: Positive | None = None
    compressibility_factor: Positive | None = None
    mixture_molar_mass_kg_mol: Positive | None = None
    molar_gas_constant_J_mol_K: Positive | None = None
    specific_gas_constant_J_kg_K: Positive | None = None
    isothermal_compressibility_1_Pa: Positive | None = None
    mass_kg: Positive | None = None
    volume_m3: Positive | None = None
    phase: Name | None = None
    mole_fractions: dict[str,float] | None = None
    mass_fractions: dict[str,float] | None = None
    provider_identity: dict | None = None
    state_mass_residual_kg: float | None = None
    issues: tuple[str,...] = ()
    thermal_path_or_phase_split_inferred: Literal[False] = False


def observe_gas_state(q:GasStateObservationRequest) -> GasStateObservationResult:
    """Resolve a supplied-temperature state; inverse coordinates preserve mass.

    Positive absolute pressure, kelvin and kg/mol are mandatory. Mixture inverse
    flashes use reviewed bracketed PT calls; no unreviewed binary interaction
    estimation, phase imposition or cubic-root guess is installed globally.
    """
    if not isinstance(q,GasStateObservationRequest):raise TypeError('GasStateObservationRequest required')
    q=GasStateObservationRequest.model_validate(q.model_dump(mode='python'))
    common=dict(property_basis_id=q.property_basis_id)
    if not q.composition_and_gas_property_domain_reviewed:
        return GasStateObservationResult(status='needs_data',issues=('composition and gas-property domain review required',),**common)
    components=q.components
    if q.model=='HEOS':
        from CoolProp.CoolProp import PropsSI
        prepared=[]
        for c in components:
            if c.fraction==0:prepared.append(c);continue
            mass=float(PropsSI('molar_mass','HEOS::'+c.id))
            if c.molar_mass_kg_mol is not None and not math.isclose(c.molar_mass_kg_mol,mass,rel_tol=1e-8):
                raise ValueError('supplied molar mass disagrees with the selected HEOS species basis')
            prepared.append(c.model_copy(update={'molar_mass_kg_mol':mass}))
        components=tuple(prepared)
    ledger=evaluate_gas_caloric_mixture(GasCaloricMixtureRequest(mode='molar_mass_only',fraction_basis=q.fraction_basis,
        components=components,property_basis_id=q.property_basis_id,temperature_K=q.temperature_K,
        nonreacting_component_composition_ledger_reviewed=True))
    if ledger.status!='computed':return GasStateObservationResult(status=ledger.status,issues=ledger.issues,**common)
    M=ledger.mixture_molar_mass_kg_mol
    base=dict(mixture_molar_mass_kg_mol=M,mole_fractions=ledger.mole_fractions,mass_fractions=ledger.mass_fractions)
    target_rho=None if q.volume_m3 is None else q.mass_kg/q.volume_m3
    if q.model=='ideal':
        gas_constant=R_UNIVERSAL_J_MOL_K
        pressure=q.pressure_Pa_abs if target_rho is None else target_rho*gas_constant/M*q.temperature_K
        rho=pressure*M/(gas_constant*q.temperature_K);z=1.;phase='ideal_gas_assumed';compressibility=1/pressure
        identity=dict(provider='exact ideal density law',molar_constant_basis='SI k_B*N_A',caloric_properties_inferred=False)
    else:
        active=[(key,value) for key,value in ledger.mole_fractions.items() if value>0]
        fluid=active[0][0] if len(active)==1 else '&'.join(f'{key}[{value:.17g}]' for key,value in active)
        gas_constant=float(PropsSI('gas_constant','HEOS::'+fluid))
        provider=CoolPropProvider(use_cache=False)
        def resolve(inputs):
            state=provider.resolve(StateRequest(fluid=fluid,backend='HEOS',inputs=inputs))
            if state.phase not in ('gas','supercritical_gas','supercritical'):
                raise ValueError('state is outside the selected gas/generic-supercritical phase domain')
            kappa=float(PropsSI('isothermal_compressibility','P',state.pressure_pa_abs,'T',state.temperature_k,'HEOS::'+fluid))
            if not math.isfinite(kappa) or kappa<=0:raise ValueError('state lacks positive finite isothermal compressibility')
            return state
        if target_rho is None:state=resolve({'P':q.pressure_Pa_abs,'T':q.temperature_K})
        elif len(active)==1:state=resolve({'Dmass':target_rho,'T':q.temperature_K})
        else:
            if q.inverse_pressure_bracket_Pa_abs is None or not q.single_phase_isotherm_bracket_reviewed:
                return GasStateObservationResult(status='needs_data',issues=('mixture inverse needs a reviewed single-phase positive pressure bracket',),**common,**base)
            from scipy.optimize import brentq
            low,high=map(math.log,q.inverse_pressure_bracket_Pa_abs)
            def residual(logp):return math.log(resolve({'P':math.exp(logp),'T':q.temperature_K}).density_kg_m3/target_rho)
            points=[low+(high-low)*i/8 for i in range(9)];values=[residual(x) for x in points]
            if not all(y>x for x,y in zip(values,values[1:])):raise ValueError('reviewed inverse bracket is not density-monotone on sampled states')
            if not values[0]<=0<=values[-1]:raise ValueError('target density is outside the supplied pressure bracket')
            root=brentq(residual,low,high,xtol=2e-13,rtol=1e-14)
            state=resolve({'P':math.exp(root),'T':q.temperature_K})
        pressure=state.pressure_pa_abs;rho=state.density_kg_m3;phase=state.phase
        compressibility=float(PropsSI('isothermal_compressibility','P',pressure,'T',q.temperature_K,'HEOS::'+fluid))
        z=pressure*M/(rho*gas_constant*q.temperature_K)
        identity=dict(provider='existing CoolPropProvider',backend='HEOS',fluid=fluid,version=state.provider_version,
            revision=state.provider_revision,phase_imposed=False,binary_interaction_estimation_applied=False,
            molar_constant_basis='selected HEOS model gas_constant',EOS_experimental_accuracy_qualified=False)
    volume=q.volume_m3
    if q.coordinate=='mass_pressure_temperature':volume=q.mass_kg/rho
    residual_mass=None if q.mass_kg is None else q.mass_kg-rho*volume
    if not all(math.isfinite(x) and x>0 for x in (pressure,rho,z,M,gas_constant)):
        raise ValueError('state observation exceeds finite positive support')
    if residual_mass is not None and abs(residual_mass)>2e-9*max(q.mass_kg,1e-300):raise ValueError('closed-state mass inversion residual unresolved')
    return GasStateObservationResult(status='computed',pressure_Pa_abs=pressure,temperature_K=q.temperature_K,density_kg_m3=rho,
        compressibility_factor=z,molar_gas_constant_J_mol_K=gas_constant,specific_gas_constant_J_kg_K=gas_constant/M,
        mass_kg=q.mass_kg,volume_m3=volume,phase=phase,provider_identity=identity,state_mass_residual_kg=residual_mass,
        isothermal_compressibility_1_Pa=compressibility,**common,**base)
