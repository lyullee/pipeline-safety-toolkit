"""Open thermodynamic provider with explicit state pairs and phase boundaries."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import version
from typing import Literal, Protocol

from pydantic import model_validator

from .contracts import Contract, Name, Positive, json_value


class StateRequest(Contract):
    fluid: Name
    backend: Literal["HEOS", "IF97", "PR", "SRK"] = "HEOS"
    inputs: dict[str, float]
    single_phase_required: bool = True

    @model_validator(mode="after")
    def independent_pair(self):
        allowed = {
            frozenset(pair)
            for pair in [("P", "T"), ("P", "Hmass"), ("P", "Smass"), ("T", "Dmass"), ("P", "Q"), ("T", "Q")]
        }
        if frozenset(self.inputs) not in allowed:
            raise ValueError("choose a supported pair of independent SI state variables")
        if any(self.inputs[k] <= 0 for k in self.inputs.keys() & {"P", "T", "Dmass"}):
            raise ValueError("absolute state variables must be positive")
        if "Q" in self.inputs and not 0 <= self.inputs["Q"] <= 1:
            raise ValueError("vapour mass fraction must lie in [0,1]")
        return self


class ThermodynamicState(Contract):
    fluid: Name
    backend: Name
    pressure_pa_abs: Positive
    temperature_k: Positive
    density_kg_m3: Positive
    enthalpy_j_kg: float
    entropy_j_kg_k: float
    phase: Name
    viscosity_pa_s: Positive | None
    cp_j_kg_k: Positive | None
    cv_j_kg_k: Positive | None
    conductivity_w_m_k: Positive | None
    speed_of_sound_m_s: Positive | None
    provider_version: Name
    provider_revision: Name


class PropertyProvider(Protocol):
    def resolve(self, request: StateRequest) -> ThermodynamicState: ...


def coolprop_reference_anchors(fluids=("Hydrogen", "ParaHydrogen")):
    """Read uncached H/S reference markers; package hashes cannot detect a reset.

    The declared 100 kPa/300 K HEOS gas point is a runtime identity marker,
    not a calibration reference or an assumption about an operating inlet.
    """
    if (
        not isinstance(fluids, tuple)
        or not fluids
        or any(not isinstance(fluid, str) or not fluid.strip() for fluid in fluids)
        or len(set(fluids)) != len(fluids)
    ):
        raise ValueError("distinct explicit HEOS fluid identifiers are required")
    for fluid in fluids:
        StateRequest(fluid=fluid, inputs={"P": 100000.0, "T": 300.0})
    from CoolProp.CoolProp import PropsSI

    return json_value(
        {
            fluid: {
                "pressure_Pa_abs": 100000.0,
                "temperature_K": 300.0,
                "specific_enthalpy_J_kg": float(
                    PropsSI("Hmass", "P", 100000.0, "T", 300.0, "HEOS::" + fluid)
                ),
                "specific_entropy_J_kg_K": float(
                    PropsSI("Smass", "P", 100000.0, "T", 300.0, "HEOS::" + fluid)
                ),
            }
            for fluid in fluids
        }
    )


class CheckedProvider:
    def __init__(self, provider):
        self.provider = provider

    def resolve(self, request: StateRequest) -> ThermodynamicState:
        state = self.provider.resolve(request)
        if not isinstance(state, ThermodynamicState):
            raise TypeError("property providers must return ThermodynamicState")
        if state.fluid != request.fluid or state.backend != request.backend:
            raise ValueError("property provider changed the fluid or backend identity")
        if state.phase not in {
            "gas",
            "liquid",
            "supercritical",
            "supercritical_gas",
            "supercritical_liquid",
            "twophase",
        }:
            raise ValueError("property provider did not resolve a supported phase")
        if request.single_phase_required and state.phase == "twophase":
            raise ValueError("the model requires a single-phase property result")
        return state


class CoolPropProvider:
    def __init__(self, *, use_cache=True):
        if not isinstance(use_cache, bool):
            raise TypeError("use_cache must be an explicit boolean")
        self.use_cache = use_cache

    def resolve(self, request: StateRequest) -> ThermodynamicState:
        if not isinstance(request, StateRequest):
            raise TypeError("StateRequest is required")
        calculate = self._cached if self.use_cache else self._cached.__wrapped__
        return calculate(request.model_dump_json())

    @staticmethod
    @lru_cache(maxsize=4096)
    def _cached(serialized: str):
        from CoolProp.CoolProp import PhaseSI, PropsSI, get_global_param_string

        request = StateRequest.model_validate_json(serialized)
        fluid = request.backend + "::" + request.fluid
        (a, va), (b, vb) = sorted(request.inputs.items())
        phase = PhaseSI(a, va, b, vb, fluid)
        if phase in {"unknown", "not_imposed"}:
            raise ValueError("the property backend did not resolve a physical phase")
        if request.single_phase_required and phase == "twophase":
            raise ValueError("the requested model requires a single-phase thermodynamic state")

        def required(key):
            return float(PropsSI(key, a, va, b, vb, fluid))

        def optional(key):
            try:
                value = required(key)
                return value if value > 0 else None
            except ValueError:
                return None

        return ThermodynamicState(
            fluid=request.fluid,
            backend=request.backend,
            pressure_pa_abs=required("P"),
            temperature_k=required("T"),
            density_kg_m3=required("Dmass"),
            enthalpy_j_kg=required("Hmass"),
            entropy_j_kg_k=required("Smass"),
            phase=phase,
            viscosity_pa_s=optional("viscosity"),
            cp_j_kg_k=optional("Cpmass"),
            cv_j_kg_k=optional("Cvmass"),
            conductivity_w_m_k=optional("conductivity"),
            speed_of_sound_m_s=optional("speed_of_sound"),
            provider_version=version("CoolProp"),
            provider_revision=get_global_param_string("gitrevision"),
        )
