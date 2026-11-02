"""Steady, completely mixed gas plenum with a signed external mass boundary.

Branch transport and the common mixture enthalpy are supplied closures. The
adapter closes their pressure/temperature/composition feedback, not a finite
velocity junction momentum model. Incoming enthalpies refer to the plenum face.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Positive


class JunctionFeed(Contract):
    enthalpy_J_kg: float
    mass_fractions: dict[str, float]
    enthalpy_reference_id: Name
    property_basis_id: Name

    @model_validator(mode="after")
    def composition(self):
        if (not self.mass_fractions or any(v < 0 for v in self.mass_fractions.values())
                or not math.isclose(math.fsum(self.mass_fractions.values()), 1., abs_tol=1e-12, rel_tol=0)):
            raise ValueError("feed mass fractions must be nonnegative and sum to one")
        return self


class JunctionBranch(Contract):
    id: Name
    closure_id: Name
    incoming: JunctionFeed


class JunctionMassFlux(Contract):
    """Positive flow enters the node; negative flow carries its mixed state out."""
    branch_id: Name
    closure_id: Name
    revision_id: Name
    mass_flow_into_node_kg_s: float


class JunctionMixtureState(Contract):
    pressure_Pa_abs: Positive
    temperature_K: Positive
    mass_fractions: dict[str, float]
    enthalpy_J_kg: float
    enthalpy_reference_id: Name
    property_basis_id: Name
    revision_id: Name
    phase: Literal["gas", "supercritical_gas"]


class PrescribedGasJunctionRequest(Contract):
    id: Name
    asset_id: Name
    clock_id: Name
    time_s: float
    species: tuple[Name, ...] = Field(min_length=1, max_length=16)
    branches: tuple[JunctionBranch, ...] = Field(min_length=1, max_length=32)
    external_mass_flow_into_node_kg_s: float
    external_incoming: JunctionFeed | None = None
    external_delivery_pressure_Pa_abs: Positive | None = None
    heat_into_plenum_W: float = 0.
    pressure_support_Pa_abs: tuple[Positive, Positive]
    temperature_support_K: tuple[Positive, Positive]
    initial_pressure_Pa_abs: Positive
    initial_temperature_K: Positive
    initial_mass_fractions: dict[str, float]
    enthalpy_reference_id: Name
    property_basis_id: Name
    closure_revision_id: Name
    mass_scale_kg_s: Positive
    energy_scale_W: Positive
    mass_tolerance_kg_s: Positive = 1e-8
    energy_tolerance_W: Positive = 1e-5
    solver_tolerance: Positive = Field(default=1e-11, le=1e-5)
    maximum_evaluations: int = Field(default=300, ge=1, le=10000)
    single_phase_zero_velocity_plenum_and_delivered_branch_states_reviewed: bool = False
    branch_closures_composition_basis_and_selected_root_box_reviewed: bool = False
    evidence_ids: tuple[Name, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def contract(self):
        if len(set(self.species)) != len(self.species) or len({b.id for b in self.branches}) != len(self.branches):
            raise ValueError("species and branch identities must be unique")
        if any(b.id == "external" for b in self.branches):
            raise ValueError("external is reserved for the signed side boundary")
        feeds = [b.incoming for b in self.branches]
        if self.external_mass_flow_into_node_kg_s > 0:
            if self.external_incoming is None or self.external_delivery_pressure_Pa_abs is None:
                raise ValueError("positive injection needs a delivered state and absolute pressure")
            feeds.append(self.external_incoming)
        elif self.external_incoming is not None or self.external_delivery_pressure_Pa_abs is not None:
            raise ValueError("zero/withdrawal boundary follows the node; no incoming state is identified")
        for f in feeds:
            if set(f.mass_fractions) != set(self.species):
                raise ValueError("every feed must identify every species, including zero fractions")
            if (f.enthalpy_reference_id, f.property_basis_id) != (self.enthalpy_reference_id, self.property_basis_id):
                raise ValueError("feed enthalpy/composition bases differ")
        JunctionFeed(enthalpy_J_kg=0., mass_fractions=self.initial_mass_fractions,
                     enthalpy_reference_id=self.enthalpy_reference_id, property_basis_id=self.property_basis_id)
        if set(self.initial_mass_fractions) != set(self.species):
            raise ValueError("initial composition identities differ")
        for bounds, initial in ((self.pressure_support_Pa_abs, self.initial_pressure_Pa_abs),
                                (self.temperature_support_K, self.initial_temperature_K)):
            if not bounds[0] < initial < bounds[1]:
                raise ValueError("initial state must be strictly inside ordered support")
        return self


def solve_prescribed_gas_junction(request: PrescribedGasJunctionRequest, *, closure):
    """Solve a selected reviewed root box using ``closure.flow`` and ``closure.state``.

    Flow callbacks receive branch, absolute P, T and mass fractions. State callbacks
    receive P, T and fractions. All callbacks must retain the declared revision.
    Negative external flow withdraws mixed gas without imposing a second enthalpy.
    """
    import numpy as np
    from scipy.optimize import least_squares

    q = PrescribedGasJunctionRequest.model_validate(request.model_dump(mode="python"))
    if not (q.single_phase_zero_velocity_plenum_and_delivered_branch_states_reviewed
            and q.branch_closures_composition_basis_and_selected_root_box_reviewed):
        raise ValueError("plenum, branch closures, basis and selected root box require explicit review")
    ids = q.species
    p0, p1 = q.pressure_support_Pa_abs
    t0, t1 = q.temperature_support_K

    def evaluate(x):
        p, t = p0 + x[0]*(p1-p0), t0 + x[1]*(t1-t0)
        total = math.fsum(x[2:])
        if total <= 0:
            raise ValueError("zero trial composition has no mixture state")
        y = {s: float(v/total) for s, v in zip(ids, x[2:])}
        state = closure.state(p, t, y)
        if not isinstance(state, JunctionMixtureState):
            raise TypeError("closure.state must return JunctionMixtureState")
        if (state.revision_id != q.closure_revision_id or state.enthalpy_reference_id != q.enthalpy_reference_id
                or state.property_basis_id != q.property_basis_id or state.mass_fractions != y
                or not math.isclose(state.pressure_Pa_abs, p, rel_tol=1e-12)
                or not math.isclose(state.temperature_K, t, rel_tol=1e-12)):
            raise ValueError("mixture closure changed requested state, basis or revision")
        flows = []
        for b in q.branches:
            f = closure.flow(b, p, t, y)
            if not isinstance(f, JunctionMassFlux):
                raise TypeError("closure.flow must return JunctionMassFlux")
            if (f.branch_id, f.closure_id, f.revision_id) != (b.id, b.closure_id, q.closure_revision_id):
                raise ValueError("branch closure identity or revision changed")
            flows.append((f.mass_flow_into_node_kg_s, b.incoming, b.id))
        flows.append((q.external_mass_flow_into_node_kg_s, q.external_incoming, "external"))
        incoming = [(m, feed) for m, feed, _ in flows if m > 0]
        outgoing = -math.fsum(m for m, _, _ in flows if m < 0)
        mass = math.fsum(m for m, _, _ in flows)
        energy = math.fsum(m*f.enthalpy_J_kg for m, f in incoming) + q.heat_into_plenum_W - outgoing*state.enthalpy_J_kg
        species = {s: math.fsum(m*f.mass_fractions[s] for m, f in incoming)-outgoing*y[s] for s in ids}
        residual = [mass/q.mass_scale_kg_s, energy/q.energy_scale_W,
                    *(species[s]/q.mass_scale_kg_s for s in ids[:-1]), total-1.]
        return residual, state, flows, mass, energy, species, outgoing

    x0 = [(q.initial_pressure_Pa_abs-p0)/(p1-p0), (q.initial_temperature_K-t0)/(t1-t0),
          *(q.initial_mass_fractions[s] for s in ids)]
    # A dimensionless optimizer tolerance alone does not guarantee the caller's
    # dimensional budgets when a residual is deliberately scaled by a large flow.
    termination = min(q.solver_tolerance, .01*q.mass_tolerance_kg_s/q.mass_scale_kg_s,
                      .01*q.energy_tolerance_W/q.energy_scale_W)
    if termination <= 10*np.finfo(float).eps:
        raise ValueError("requested budgets are below supported double-precision residual scaling")
    fit = least_squares(lambda x: evaluate(x)[0], np.asarray(x0), bounds=(0., 1.),
                        xtol=termination, ftol=termination, gtol=termination,
                        max_nfev=q.maximum_evaluations)
    _, state, flows, mass, energy, species, outgoing = evaluate(fit.x)
    if not fit.success or outgoing <= q.mass_tolerance_kg_s:
        raise ValueError("no identified nonzero-throughflow steady root was resolved")
    if (abs(mass) > q.mass_tolerance_kg_s or abs(energy) > q.energy_tolerance_W
            or any(abs(v) > q.mass_tolerance_kg_s for v in species.values())):
        raise ValueError("selected root did not close mass, species and enthalpy budgets")
    if min(fit.x[:2]) < 1e-9 or max(fit.x[:2]) > 1.-1e-9:
        raise ValueError("root lies at an unresolved pressure/temperature support boundary")
    if (q.external_mass_flow_into_node_kg_s > 0
            and q.external_delivery_pressure_Pa_abs < state.pressure_Pa_abs):
        raise ValueError("external delivery pressure cannot sustain this passive prescribed injection")
    return dict(status="computed", id=q.id, asset_id=q.asset_id, clock_id=q.clock_id, time_s=q.time_s,
                state=state.model_dump(mode="json"), branch_mass_flow_into_node_kg_s={i:m for m, _, i in flows},
                mass_residual_kg_s=mass, energy_residual_W=energy, species_residual_kg_s=species,
                solver_evaluations=fit.nfev, evidence_ids=q.evidence_ids,
                branch_incoming_state_meaning="delivered static enthalpy at zero-velocity plenum face",
                field_or_standard_qualified=False, global_root_uniqueness_proven=False,
                finite_velocity_momentum_or_full_network_solved=False)
