"""Homogeneous frozen-phase liquid/gas acoustics with elastic-wall storage.

Gas and liquid share pressure and velocity; neither phase conversion nor slip
is predicted. Gas density and its local polytropic exponent are supplied at the
declared common state. The existing single-fluid wall model owns wave speed.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from .contracts import Contract,Name,Nonnegative,Positive
from .models.hydraulics import WaveSpeed,wave_speed


class MixtureWaveRequest(Contract):
    common_state_basis_id: Name
    gas_volume_fraction: float = Field(ge=0,le=1)
    liquid_density_kg_m3: Positive
    liquid_bulk_modulus_Pa: Positive
    gas_pressure_Pa_abs: Positive | None = None
    gas_density_kg_m3: Positive | None = None
    gas_polytropic_exponent: Positive | None = None
    pipe_elastic_modulus_Pa: Positive
    inner_diameter_m: Positive
    wall_thickness_m: Positive
    wall_restraint_coefficient: Nonnegative
    homogeneous_frozen_phase_and_common_state_reviewed: bool = False


class MixtureWaveResult(Contract):
    status: Literal["ok","needs_data","out_of_scope"]
    mixture_density_kg_m3: Positive | None = None
    mixture_bulk_modulus_Pa: Positive | None = None
    fluid_compliance_per_Pa: Positive | None = None
    wall_compliance_per_Pa: Nonnegative | None = None
    wave_speed_m_s: Positive | None = None
    rigid_wall_sound_speed_m_s: Positive | None = None
    reason: str
    phase_change_or_slip_predicted: bool = False
    material_calibration_claim: bool = False


def evaluate_mixture_wave_speed(request:MixtureWaveRequest)->MixtureWaveResult:
    q=MixtureWaveRequest.model_validate(request.model_dump(mode="python"))
    if not q.homogeneous_frozen_phase_and_common_state_reviewed:
        raise ValueError("homogeneous frozen-phase and common pressure/state assumptions require review")
    alpha=q.gas_volume_fraction
    if alpha>0 and any(v is None for v in (q.gas_pressure_Pa_abs,q.gas_density_kg_m3,q.gas_polytropic_exponent)):
        return MixtureWaveResult(status="needs_data",reason="A nonzero gas fraction requires actual absolute pressure, gas density and a reviewed local polytropic exponent.")
    try:
        gas_compliance=0.0 if alpha==0 else alpha/q.gas_pressure_Pa_abs/q.gas_polytropic_exponent
        compliance=math.fsum(((1-alpha)/q.liquid_bulk_modulus_Pa,gas_compliance))
        density=math.fsum(((1-alpha)*q.liquid_density_kg_m3,0.0 if alpha==0 else alpha*q.gas_density_kg_m3))
        bulk=1/compliance
        if not all(math.isfinite(v) and v>0 for v in (compliance,density,bulk)):
            raise ArithmeticError("mixture property arithmetic is not finite positive")
        owner=WaveSpeed(density_kg_m3=density,fluid_bulk_modulus_pa=bulk,
            pipe_elastic_modulus_pa=q.pipe_elastic_modulus_Pa,inner_diameter_m=q.inner_diameter_m,
            wall_thickness_m=q.wall_thickness_m,restraint_coefficient=q.wall_restraint_coefficient)
        values=wave_speed(owner).values
        wall=(q.wall_restraint_coefficient*q.inner_diameter_m/q.wall_thickness_m)/q.pipe_elastic_modulus_Pa
        return MixtureWaveResult(status="ok",mixture_density_kg_m3=density,
            mixture_bulk_modulus_Pa=bulk,fluid_compliance_per_Pa=compliance,wall_compliance_per_Pa=wall,
            wave_speed_m_s=values["wave_speed_m_s"],rigid_wall_sound_speed_m_s=values["rigid_wall_sound_speed_m_s"],
            reason="Common-pressure volume compliance and constituent mass density passed to the existing elastic-wall wave-speed owner; local small-amplitude frozen-phase response only.")
    except (ArithmeticError,ValueError) as error:
        return MixtureWaveResult(status="out_of_scope",reason=str(error))
