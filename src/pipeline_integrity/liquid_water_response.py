"""Single-phase water EOS response observations with explicit absolute states.

These derivatives and fixed-mass volume coordinates share the existing water
density owner. They are not a hydrotest acceptance or a fitted property table.
"""
from __future__ import annotations

import math
from typing import Literal

from .contracts import Contract, Nonnegative, Positive
from .closed_liquid_compatibility import CoolPropTestWaterProvider, TestLiquidState
from .thermo import CheckedProvider, CoolPropProvider, StateRequest


class LiquidWaterPT(Contract):
    pressure_Pa_abs: Positive
    temperature_K: Positive


class LiquidWaterResponseRequest(Contract):
    state: LiquidWaterPT
    reference_state: LiquidWaterPT | None = None
    fixed_mass_kg: Nonnegative | None = None


class LiquidWaterResponseResult(Contract):
    status: Literal['computed', 'out_of_scope']
    state: LiquidWaterPT
    reference_state: LiquidWaterPT | None = None
    property_basis_id: str = 'CoolProp::HEOS::Water'
    phase: str | None = None
    density_kg_m3: Positive | None = None
    specific_volume_m3_kg: Positive | None = None
    isobaric_volumetric_expansion_K_inv: float | None = None
    isothermal_compressibility_Pa_inv: Positive | None = None
    cp_J_kg_K: Positive | None = None
    cv_J_kg_K: Positive | None = None
    reference_density_kg_m3: Positive | None = None
    reference_specific_volume_m3_kg: Positive | None = None
    specific_volume_over_reference: Positive | None = None
    fixed_mass_kg: Nonnegative | None = None
    volume_m3: Nonnegative | None = None
    reference_volume_m3: Nonnegative | None = None
    issues: tuple[str, ...] = ()


def observe_liquid_water_response(request: LiquidWaterResponseRequest) -> LiquidWaterResponseResult:
    """Return signed beta, isothermal kappa and fixed-mass volume ratios.

    Each ratio uses its two declared PT endpoints. It is an isobaric thermal
    ratio only if those pressures match. No saturation, ice, vapor, mixture,
    acoustic-modulus substitution or spatial thermal-field closure is supplied.
    """
    from CoolProp.CoolProp import PropsSI

    q = LiquidWaterResponseRequest.model_validate(request.model_dump(mode='python'))
    common = dict(state=q.state, reference_state=q.reference_state, fixed_mass_kg=q.fixed_mass_kg)
    states = [q.state] + ([q.reference_state] if q.reference_state is not None else [])
    # Deliberately exclude supercooled liquid and the critical seam. This is
    # a liquid-only observation contract, not a melting-line implementation.
    if any(not 273.16 <= s.temperature_K < 647.096 or s.pressure_Pa_abs > 1e8 for s in states):
        return LiquidWaterResponseResult(status='out_of_scope',
            issues=('state outside non-supercooled subcritical liquid support up to 100 MPa',), **common)
    owner = CoolPropTestWaterProvider()
    try:
        resolved = []
        for s in states:
            value = owner.resolve_density(pressure_Pa_abs=s.pressure_Pa_abs,
                        water_temperature_K=s.temperature_K)
            # The hydrotest owner deliberately supports only CoolProp's literal
            # 'liquid' label. At subcritical T and P above Pc the same provider
            # labels compressed liquid 'supercritical_liquid'; keep this bounded
            # extension here, without changing the original hydrotest contract.
            if value.phase == 'supercritical_liquid':
                extended = CheckedProvider(CoolPropProvider(use_cache=False)).resolve(StateRequest(
                    fluid='Water', backend='HEOS', inputs={'P':s.pressure_Pa_abs,'T':s.temperature_K}))
                kappa_native = float(PropsSI('isothermal_compressibility','P',s.pressure_Pa_abs,
                                            'T',s.temperature_K,'HEOS::Water'))
                value = TestLiquidState(status='computed',pressure_Pa_abs=s.pressure_Pa_abs,
                    water_temperature_K=s.temperature_K,fluid_id='Water',property_basis_id=owner.property_basis_id,
                    density_kg_m3=extended.density_kg_m3,phase=extended.phase,
                    isothermal_compressibility_Pa_inv=kappa_native)
            resolved.append(value)
        if any(s.status != 'computed' or s.phase not in {'liquid','supercritical_liquid'} for s in resolved):
            return LiquidWaterResponseResult(status='out_of_scope', phase=resolved[0].phase,
                issues=('all endpoints must resolve as pure liquid water',), **common)
        point = q.state
        thermo = CheckedProvider(CoolPropProvider(use_cache=False)).resolve(StateRequest(
            fluid='Water', backend='HEOS', inputs={'P':point.pressure_Pa_abs, 'T':point.temperature_K}))
        if thermo.phase not in {'liquid','supercritical_liquid'} or thermo.cp_j_kg_k is None or thermo.cv_j_kg_k is None:
            return LiquidWaterResponseResult(status='out_of_scope',
                issues=('liquid caloric state unavailable',), **common)
        beta = float(PropsSI('isobaric_expansion_coefficient', 'P',point.pressure_Pa_abs,
                            'T',point.temperature_K, 'HEOS::Water'))
        rho = resolved[0].density_kg_m3
        kappa = resolved[0].isothermal_compressibility_Pa_inv
        if not math.isfinite(beta) or rho is None or kappa is None or not math.isfinite(kappa) or kappa <= 0:
            return LiquidWaterResponseResult(status='out_of_scope',
                issues=('finite signed expansion and stable isothermal response required',), **common)
        ref_rho = resolved[1].density_kg_m3 if q.reference_state is not None else None
        v = 1 / rho
        ref_v = 1 / ref_rho if ref_rho is not None else None
        return LiquidWaterResponseResult(status='computed', phase=thermo.phase,
            density_kg_m3=rho, specific_volume_m3_kg=v,
            isobaric_volumetric_expansion_K_inv=beta, isothermal_compressibility_Pa_inv=kappa,
            cp_J_kg_K=thermo.cp_j_kg_k, cv_J_kg_K=thermo.cv_j_kg_k,
            reference_density_kg_m3=ref_rho, reference_specific_volume_m3_kg=ref_v,
            specific_volume_over_reference=v/ref_v if ref_v is not None else None,
            volume_m3=q.fixed_mass_kg*v if q.fixed_mass_kg is not None else None,
            reference_volume_m3=q.fixed_mass_kg*ref_v if q.fixed_mass_kg is not None and ref_v is not None else None,
            **common)
    except (ValueError, OverflowError) as exc:
        return LiquidWaterResponseResult(status='out_of_scope',
            issues=('water property response unavailable: '+str(exc),), **common)
