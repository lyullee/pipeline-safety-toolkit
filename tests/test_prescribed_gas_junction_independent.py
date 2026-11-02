"""Independent ideal caloric/control-volume references for a supplied-flow node."""
import math

import pytest

from pipeline_integrity.prescribed_gas_junction import (
    JunctionBranch, JunctionFeed, JunctionMassFlux, JunctionMixtureState,
    PrescribedGasJunctionRequest, solve_prescribed_gas_junction,
)


def feed(t, y=None, shift=0.):
    y = y or {"A": 1.}
    cp = {"A": 1000., "B": 1700.}
    return JunctionFeed(enthalpy_J_kg=sum(v*cp[s]*t for s,v in y.items())+shift,
                        mass_fractions=y, enthalpy_reference_id="common-h", property_basis_id="mass-SI")


def request(*, injection=.2, species=("A",), incoming=None, qheat=0.):
    y = {s: 1/len(species) for s in species}
    return PrescribedGasJunctionRequest(id="J", asset_id="test", clock_id="steady", time_s=0.,
        species=species, branches=(
            JunctionBranch(id="high", closure_id="resistance", incoming=feed(330., y)),
            JunctionBranch(id="low", closure_id="resistance", incoming=feed(280., y))),
        external_mass_flow_into_node_kg_s=injection,
        external_incoming=(incoming or feed(390., y)) if injection>0 else None,
        external_delivery_pressure_Pa_abs=400000. if injection>0 else None,
        pressure_support_Pa_abs=(100000., 400000.), temperature_support_K=(200., 500.),
        initial_pressure_Pa_abs=250000., initial_temperature_K=335., initial_mass_fractions=y,
        heat_into_plenum_W=qheat, enthalpy_reference_id="common-h", property_basis_id="mass-SI",
        closure_revision_id="rev1", mass_scale_kg_s=1., energy_scale_W=1e6,
        single_phase_zero_velocity_plenum_and_delivered_branch_states_reviewed=True,
        branch_closures_composition_basis_and_selected_root_box_reviewed=True, evidence_ids=("analytic",))


class LinearClosure:
    pressures = {"high": 330000., "low": 170000.}
    c = {"high": 1e-5, "low": 1.7e-5}
    shift = 0.

    def flow(self, b, p, t, y):
        return JunctionMassFlux(branch_id=b.id, closure_id=b.closure_id, revision_id="rev1",
                                mass_flow_into_node_kg_s=self.c[b.id]*(self.pressures[b.id]-p))

    def state(self, p, t, y):
        cp = sum(y[s]*{"A":1000., "B":1700.}[s] for s in y)
        return JunctionMixtureState(pressure_Pa_abs=p, temperature_K=t, mass_fractions=y,
            enthalpy_J_kg=cp*t+self.shift, enthalpy_reference_id="common-h", property_basis_id="mass-SI",
            revision_id="rev1", phase="gas")


def linear_reference(q, c):
    # Kirchhoff's parallel-resistance equation, followed by independently
    # accumulated species inventories and caloric energy per exiting mass.
    p = (sum(c.c[b.id]*c.pressures[b.id] for b in q.branches)+q.external_mass_flow_into_node_kg_s)/sum(c.c.values())
    inflow = [(c.c[b.id]*(c.pressures[b.id]-p), b.incoming) for b in q.branches if c.pressures[b.id]>p]
    if q.external_mass_flow_into_node_kg_s>0:
        inflow.append((q.external_mass_flow_into_node_kg_s, q.external_incoming))
    mi = sum(m for m,_ in inflow)
    y = {s:sum(m*f.mass_fractions[s] for m,f in inflow)/mi for s in q.species}
    h = (sum(m*f.enthalpy_J_kg for m,f in inflow)+q.heat_into_plenum_W)/mi
    t = (h-c.shift)/sum(y[s]*{"A":1000.,"B":1700.}[s] for s in y)
    return p,t,y


@pytest.mark.parametrize("injection", [-.7, 0., .2, 1.8])
def test_signed_external_flow_analytic_resistor_and_caloric_balance(injection):
    q=request(injection=injection,qheat=1250.)
    c=LinearClosure(); p,t,y=linear_reference(q,c)
    r=solve_prescribed_gas_junction(q,closure=c)
    assert r["state"]["pressure_Pa_abs"] == pytest.approx(p,rel=2e-9)
    assert r["state"]["temperature_K"] == pytest.approx(t,rel=2e-9)
    assert abs(r["mass_residual_kg_s"])<1e-8 and abs(r["energy_residual_W"])<1e-5
    assert r["branch_mass_flow_into_node_kg_s"]["external"]==injection


@pytest.mark.parametrize("z", [.1,.4,.9])
def test_two_species_mass_inventory_and_caloric_energy(z):
    q=request(species=("A","B"),incoming=feed(380.,{"A":z,"B":1-z}),qheat=-1000.)
    c=LinearClosure();p,t,y=linear_reference(q,c)
    r=solve_prescribed_gas_junction(q,closure=c)
    assert r["state"]["mass_fractions"]==pytest.approx(y,abs=1e-9)
    assert r["state"]["temperature_K"]==pytest.approx(t,rel=1e-9)
    assert max(map(abs,r["species_residual_kg_s"].values()))<1e-8


class ThermalResistance(LinearClosure):
    c={"high":2e-8,"low":3e-8}

    def flow(self,b,p,t,y):
        reservoir_t={"high":330.,"low":280.}[b.id]
        up_t=reservoir_t if self.pressures[b.id]>=p else t
        m=self.c[b.id]*(self.pressures[b.id]**2-p*p)/up_t
        return JunctionMassFlux(branch_id=b.id,closure_id=b.closure_id,revision_id="rev1",mass_flow_into_node_kg_s=m)


@pytest.mark.parametrize("heat",[-4000.,0.,7500.])
def test_coupled_pressure_temperature_against_eliminated_thermal_resistance(heat):
    q=request(qheat=heat);c=ThermalResistance();a,b=c.c["high"],c.c["low"]
    # Eliminating T from adiabatic caloric energy yields a separate scalar
    # polynomial in P^2; no nonlinear node residual is used by this oracle.
    p2=(a*c.pressures["high"]**2+b*c.pressures["low"]**2+.2*390.+heat/1000.)/(a+b)
    p=math.sqrt(p2)
    t=b*(p2-c.pressures["low"]**2)/(a*(c.pressures["high"]**2-p2)/330.+.2)
    r=solve_prescribed_gas_junction(q,closure=c)
    assert r["state"]["pressure_Pa_abs"]==pytest.approx(p,rel=1e-9)
    assert r["state"]["temperature_K"]==pytest.approx(t,rel=1e-9)


def test_enthalpy_reference_shift_leaves_pressure_temperature_unchanged():
    q=request(species=("A","B"));c=LinearClosure()
    base=solve_prescribed_gas_junction(q,closure=c)
    shift=-2500000.;c.shift=shift
    shifted=q.model_copy(update={"branches":tuple(b.model_copy(update={"incoming":b.incoming.model_copy(update={"enthalpy_J_kg":b.incoming.enthalpy_J_kg+shift})}) for b in q.branches),
        "external_incoming":q.external_incoming.model_copy(update={"enthalpy_J_kg":q.external_incoming.enthalpy_J_kg+shift})})
    r=solve_prescribed_gas_junction(shifted,closure=c)
    assert r["state"]["temperature_K"]==pytest.approx(base["state"]["temperature_K"],abs=1e-7)
    assert r["state"]["pressure_Pa_abs"]==pytest.approx(base["state"]["pressure_Pa_abs"],abs=1e-5)


@pytest.mark.parametrize("tolerance",[1e-8,1e-10,1e-12])
def test_solver_resolution_and_branch_reversal_event(tolerance):
    q=request(injection=1.8).model_copy(update={"solver_tolerance":tolerance})
    c=LinearClosure();c.pressures={"high":260000.,"low":170000.}
    r=solve_prescribed_gas_junction(q,closure=c);p,t,_=linear_reference(q,c)
    assert r["state"]["pressure_Pa_abs"]==pytest.approx(p,rel=1e-8)
    assert r["state"]["temperature_K"]==pytest.approx(t,rel=1e-8)
    assert all(r["branch_mass_flow_into_node_kg_s"][s]<0 for s in ("high","low"))


def test_zero_flow_branch_does_not_impose_incoming_temperature():
    q=request(injection=1.7)
    c=LinearClosure();c.pressures={"high":270000.,"low":170000.}
    # P=270kPa: source2 boundary is withdrawn, first branch exactly stagnant.
    r=solve_prescribed_gas_junction(q,closure=c)
    assert r["state"]["temperature_K"]==pytest.approx(390.,abs=1e-6)
    assert abs(r["branch_mass_flow_into_node_kg_s"]["high"])<1e-8


@pytest.mark.parametrize("change",[
    {"single_phase_zero_velocity_plenum_and_delivered_branch_states_reviewed":False},
    {"external_delivery_pressure_Pa_abs":150000.},
    {"closure_revision_id":"unknown"},
    {"maximum_evaluations":1},
    {"pressure_support_Pa_abs":(100000.,200000.),"initial_pressure_Pa_abs":150000.},
])
def test_missing_review_pressure_revision_resolution_and_support_fail_closed(change):
    with pytest.raises(ValueError):solve_prescribed_gas_junction(request().model_copy(update=change),closure=LinearClosure())


def test_missing_injection_state_and_basis_mismatch_rejected():
    with pytest.raises(ValueError):
        PrescribedGasJunctionRequest.model_validate(request().model_copy(update={"external_incoming":None}).model_dump())
    with pytest.raises(ValueError):
        PrescribedGasJunctionRequest.model_validate(request().model_copy(update={"property_basis_id":"wrong"}).model_dump())


def test_branch_identity_cannot_overwrite_external_boundary_receipt():
    q=request()
    bad=(q.branches[0].model_copy(update={"id":"external"}),q.branches[1])
    with pytest.raises(ValueError):
        PrescribedGasJunctionRequest.model_validate(q.model_copy(update={"branches":bad}).model_dump())


def test_zero_throughflow_has_no_identified_steady_temperature():
    q=request(injection=0.)
    c=LinearClosure();c.pressures={"high":250000.,"low":250000.}
    with pytest.raises(ValueError):solve_prescribed_gas_junction(q,closure=c)


def test_unknown_or_nonphysical_property_result_is_rejected():
    class Bad(LinearClosure):
        def state(self,p,t,y):return {"pressure":p}
    with pytest.raises(TypeError):solve_prescribed_gas_junction(request(),closure=Bad())


@pytest.mark.parametrize("injection_T",[310.,420.])
def test_native_nitrogen_against_independent_cp_volume_derivative_energy_integrals(injection_T):
    from scipy.integrate import quad
    from scipy.optimize import brentq
    from CoolProp.CoolProp import PropsSI
    from pipeline_integrity.thermo import CheckedProvider,CoolPropProvider,StateRequest
    provider=CheckedProvider(CoolPropProvider(use_cache=False))
    def native(p,t):
        return provider.resolve(StateRequest(fluid="Nitrogen",inputs={"P":p,"T":t}))
    def nf(p,t):
        return feed(t).model_copy(update={"enthalpy_J_kg":native(p,t).enthalpy_j_kg})
    q=request(incoming=nf(400000.,injection_T),qheat=300.)
    q=q.model_copy(update={"branches":tuple(b.model_copy(update={"incoming":nf(LinearClosure.pressures[b.id],330. if b.id=="high" else 280.)}) for b in q.branches)})
    class Native(LinearClosure):
        def state(self,p,t,y):
            s=native(p,t)
            return JunctionMixtureState(pressure_Pa_abs=p,temperature_K=t,mass_fractions=y,
                enthalpy_J_kg=s.enthalpy_j_kg,enthalpy_reference_id="common-h",property_basis_id="mass-SI",
                revision_id="rev1",phase=s.phase)
    c=Native();p=(sum(c.c[k]*c.pressures[k] for k in c.c)+.2)/sum(c.c.values())
    mi=c.c["high"]*(c.pressures["high"]-p)
    # dh=cp*dT+[v-T*(dv/dT)_P]*dP from the fundamental Gibbs identity.
    # No native enthalpy values are used by the reference integration.
    def delta_h(p,t):
        hp=quad(lambda pp:(1-300.*PropsSI("isobaric_expansion_coefficient","P",pp,"T",300.,"Nitrogen"))/PropsSI("Dmass","P",pp,"T",300.,"Nitrogen"),100000.,p,epsabs=1e-7)[0]
        ht=quad(lambda tt:PropsSI("Cpmass","P",p,"T",tt,"Nitrogen"),300.,t,epsabs=1e-7)[0]
        return hp+ht
    target=(mi*delta_h(c.pressures["high"],330.)+.2*delta_h(400000.,injection_T)+300.)/(mi+.2)
    tref=brentq(lambda t:delta_h(p,t)-target,250.,480.,xtol=1e-9)
    r=solve_prescribed_gas_junction(q,closure=c)
    assert r["state"]["pressure_Pa_abs"]==pytest.approx(p,rel=1e-9)
    assert r["state"]["temperature_K"]==pytest.approx(tref,abs=3e-7)
