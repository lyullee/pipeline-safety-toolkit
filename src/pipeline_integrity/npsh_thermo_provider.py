"""Pure-fluid suction properties through the existing thermodynamic provider.

Bulk rho/mu use the actual declared reference P,T pair. Vapor pressure uses
the same fluid's T,Q=0 saturation coordinate. The saturation call is not a
replacement for the bulk-liquid state or a mixture bubble-point calibration.
"""
from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Name, Positive
from .npsh_temperature_search import SuctionTemperatureProperties
from .thermo import CheckedProvider, CoolPropProvider, PropertyProvider, StateRequest


class PureFluidSuctionProviderConfig(Contract):
    fluid: Name
    backend: Literal['HEOS','IF97'] = 'HEOS'
    reference_pressure_pa_abs: Positive
    provider_basis_id: Name

    @model_validator(mode='after')
    def pure_fluid_identifier(self):
        if any(token in self.fluid for token in ('&','[',']','::')):
            raise ValueError('provide one pure-fluid identifier; composition/bubble-point providers require their separate reviewed contract')
        return self


class PureFluidSuctionPropertyProvider:
    """Callable adapter; CoolProp is the default, without a global cache change.

    The selected existing provider resolves phase and transport properties.
    Its material/phase validity and calibration are not independently qualified
    by this adapter. A non-liquid bulk state is retained as unsupported data,
    never overwritten with a forced phase or guessed vapor pressure.
    """
    def __init__(self, config: PureFluidSuctionProviderConfig, provider: PropertyProvider | None = None):
        self.config=PureFluidSuctionProviderConfig.model_validate(config.model_dump(mode='python'))
        self.provider=CheckedProvider(CoolPropProvider(use_cache=False) if provider is None else provider)

    def __call__(self, temperature_K: float) -> SuctionTemperatureProperties:
        c=self.config
        bulk=self.provider.resolve(StateRequest(fluid=c.fluid,backend=c.backend,
            inputs={'P':c.reference_pressure_pa_abs,'T':temperature_K},single_phase_required=True))
        mapped_phase={'liquid':'liquid','gas':'gas','twophase':'two_phase','supercritical':'supercritical',
            'supercritical_gas':'supercritical','supercritical_liquid':'supercritical'}[bulk.phase]
        base=dict(temperature_K=temperature_K,property_reference_pressure_pa_abs=c.reference_pressure_pa_abs,
            density_kg_m3=bulk.density_kg_m3,dynamic_viscosity_pa_s=bulk.viscosity_pa_s,
            phase=mapped_phase,provider_basis_id=c.provider_basis_id)
        if bulk.phase!='liquid':
            return SuctionTemperatureProperties(**base)
        saturation=self.provider.resolve(StateRequest(fluid=c.fluid,backend=c.backend,
            inputs={'T':temperature_K,'Q':0.},single_phase_required=False))
        return SuctionTemperatureProperties(**base,vapor_pressure_pa_abs=saturation.pressure_pa_abs)
