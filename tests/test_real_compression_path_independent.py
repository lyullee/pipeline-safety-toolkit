"""Independent pressure-temperature paths and first-law stage ledgers."""
import math

import pytest
from CoolProp.CoolProp import PropsSI
from scipy.integrate import solve_ivp

from pipeline_integrity.compressor_train import IntercoolerBoundary
from pipeline_integrity.real_compression_path import (
    DifferentialCompressionStage, RealCompressionPathRequest, evaluate_real_compression_path,
)
from pipeline_integrity.thermo import ThermodynamicState


class IdealCaloricProvider:
    cp = 1000.
    gas_constant = 287.

    def __init__(self, enthalpy_origin=0., phase='gas'):
        self.origin, self.phase = enthalpy_origin, phase

    def resolve(self, request):
        p = request.inputs['P']
        t = request.inputs.get('T')
        if t is None:
            t = (request.inputs['Hmass']-self.origin)/self.cp
        return ThermodynamicState(
            fluid=request.fluid, backend=request.backend, pressure_pa_abs=p, temperature_k=t,
            density_kg_m3=p/(self.gas_constant*t), enthalpy_j_kg=self.cp*t+self.origin,
            entropy_j_kg_k=self.cp*math.log(t/300.)-self.gas_constant*math.log(p/1e5),
            phase=self.phase, viscosity_pa_s=None, cp_j_kg_k=self.cp,
            cv_j_kg_k=self.cp-self.gas_constant, conductivity_w_m_k=None, speed_of_sound_m_s=None,
            provider_version='manufactured-caloric-1', provider_revision='independent-ideal-state',
        )


def cooler(mode='none', **kwargs):
    return IntercoolerBoundary(mode=mode, pressure_drop_pa=0., evidence_id='cooler-boundary', **kwargs)


def stage(p=4e5, eta=.8, **kwargs):
    kwargs.setdefault('intercooler', cooler())
    return DifferentialCompressionStage(id='stage-1', outlet_pressure_pa_abs=p,
                                       differential_polytropic_efficiency=eta, evidence_id='stage-data', **kwargs)


def request(**kwargs):
    data = dict(fluid='Methane', inlet_pressure_pa_abs=1e5, inlet_temperature_k=300., mass_flow_kg_s=.7,
                stages=(stage(),), single_phase_steady_adiabatic_stage_path_reviewed=True,
                efficiency_and_cooler_boundaries_reviewed=True, evidence_id='process-data')
    data.update(kwargs)
    return RealCompressionPathRequest(**data)


@pytest.mark.parametrize('ratio', [1.1, 4., 20.])
@pytest.mark.parametrize('eta', [.6, .8, 1.])
def test_independent_ideal_temperature_entropy_and_pressure_work(ratio, eta):
    provider = IdealCaloricProvider()
    q = request(stages=(stage(p=1e5*ratio, eta=eta),))
    r = evaluate_real_compression_path(q, property_provider=provider)
    assert r.status == 'computed'
    final = r.values['stages'][0]['compressed']
    # Constant-cp first law and the ideal entropy differential give this power
    # law directly in T(P), independently of the production enthalpy ODE.
    temperature = 300.*ratio**(provider.gas_constant/(provider.cp*eta))
    assert final['temperature_k'] == pytest.approx(temperature, rel=2e-10)
    assert r.values['specific_fluid_work_j_kg'] == pytest.approx(provider.cp*(temperature-300.), rel=2e-9)
    expected_entropy = provider.gas_constant*(1/eta-1)*math.log(ratio)
    assert final['entropy_j_kg_k'] == pytest.approx(expected_entropy, abs=2e-7)
    assert abs(r.values['first_law_residual_j_kg']) < 1e-7


@pytest.mark.parametrize('origin', [-1e8, 1e8])
def test_enthalpy_origin_shift_preserves_work_and_cooling(origin):
    q = request(stages=(stage(intercooler=cooler('outlet_temperature', outlet_total_temperature_K=310.)),))
    base = evaluate_real_compression_path(q, property_provider=IdealCaloricProvider()).values
    shifted = evaluate_real_compression_path(q, property_provider=IdealCaloricProvider(origin)).values
    for key in ('specific_fluid_work_j_kg', 'specific_heat_rejected_j_kg', 'specific_enthalpy_change_j_kg'):
        assert shifted[key] == pytest.approx(base[key], rel=2e-11, abs=1e-7)


@pytest.mark.parametrize('boundary', ['outlet_temperature', 'heat_removal'])
def test_multistage_cooling_independent_caloric_energy_ledger(boundary):
    provider = IdealCaloricProvider()
    after1 = 300.*2**(287/(1000*.8))
    options = {'outlet_total_temperature_K': 300.} if boundary == 'outlet_temperature' else {
        'heat_removal_W': .7*1000*(after1-300.)}
    c = cooler(boundary, **options)
    first = stage(p=2e5, intercooler=c, mechanical_efficiency=.9, drive_efficiency=.95)
    second = stage(p=4e5, eta=.9, mechanical_efficiency=.85, drive_efficiency=.96).model_copy(update={'id': 'stage-2'})
    r = evaluate_real_compression_path(request(stages=(first, second)), property_provider=provider).values
    after2 = 300.*2**(287/(1000*.9))
    expected_work = 1000*(after1-300.)+1000*(after2-300.)
    expected_heat = 1000*(after1-300.)
    assert r['specific_fluid_work_j_kg'] == pytest.approx(expected_work, rel=3e-10)
    assert r['specific_heat_rejected_j_kg'] == pytest.approx(expected_heat, rel=3e-10)
    assert r['specific_enthalpy_change_j_kg'] == pytest.approx(1000*(after2-300.), rel=3e-10)
    assert r['shaft_power_w'] == pytest.approx(.7*1000*((after1-300.)/.9+(after2-300.)/.85), rel=3e-10)
    assert r['drive_power_w'] == pytest.approx(.7*1000*((after1-300.)/(.9*.95)+(after2-300.)/(.85*.96)), rel=3e-10)
    assert abs(r['first_law_residual_j_kg']) < 1e-7


@pytest.mark.parametrize('fluid,p1,p2,t1,eta', [
    ('Methane', 2e6, 8e6, 300., .8), ('Hydrogen', 1e5, 2e6, 300., .75),
    ('Methane', 2e5, 1e6, 320., 1.),
])
def test_native_eos_independent_temperature_pressure_ode_and_refinement(fluid, p1, p2, t1, eta):
    ref = 'HEOS::'+fluid
    # The oracle evolves T in linear pressure, using a native isothermal
    # enthalpy derivative. Production evolves enthalpy in logarithmic pressure.
    def derivative(p, y):
        t = float(y[0])
        rho = PropsSI('Dmass', 'P', p, 'T', t, ref)
        cp = PropsSI('Cpmass', 'P', p, 'T', t, ref)
        hp = PropsSI('d(Hmass)/d(P)|T', 'P', p, 'T', t, ref)
        return [(1/(rho*eta)-hp)/cp]
    independent = solve_ivp(derivative, (p1, p2), [t1], rtol=1e-11, atol=1e-9, max_step=(p2-p1)/64)
    assert independent.success
    expected_t = float(independent.y[0, -1])
    h1 = PropsSI('Hmass', 'P', p1, 'T', t1, ref)
    expected_work = PropsSI('Hmass', 'P', p2, 'T', expected_t, ref)-h1
    for intervals in (4, 8, 16):
        q = request(fluid=fluid, inlet_pressure_pa_abs=p1, inlet_temperature_k=t1,
                    stages=(stage(p=p2, eta=eta),), minimum_log_pressure_intervals=intervals,
                    relative_tolerance=1e-9)
        r = evaluate_real_compression_path(q).values
        assert r['stages'][0]['compressed']['temperature_k'] == pytest.approx(expected_t, rel=2e-8)
        assert r['specific_fluid_work_j_kg'] == pytest.approx(expected_work, rel=1e-7)
        assert abs(r['first_law_residual_j_kg']) < 1e-6


def test_missing_data_review_and_optional_drive_are_not_assumed():
    for updates in ({'mass_flow_kg_s': None}, {'single_phase_steady_adiabatic_stage_path_reviewed': None},
                    {'stages': (stage(eta=None),)}, {'stages': (stage(intercooler=None),)}):
        assert evaluate_real_compression_path(request(**updates)).status == 'needs_data'
    result = evaluate_real_compression_path(request(), property_provider=IdealCaloricProvider()).values
    assert result['shaft_power_w'] is None and result['drive_power_w'] is None


def test_zero_flow_keeps_specific_path_but_has_no_power():
    result = evaluate_real_compression_path(request(mass_flow_kg_s=0.), property_provider=IdealCaloricProvider()).values
    assert result['specific_fluid_work_j_kg'] > 0
    assert result['fluid_power_w'] == result['heat_rejection_w'] == 0
    with pytest.raises(ValueError, match='positive mass flow'):
        evaluate_real_compression_path(request(mass_flow_kg_s=0., stages=(stage(intercooler=cooler('heat_removal', heat_removal_W=0.)),)),
                                       property_provider=IdealCaloricProvider())


def test_gas_phase_and_explicit_pressure_direction():
    with pytest.raises(ValueError, match='gas phase'):
        evaluate_real_compression_path(request(), property_provider=IdealCaloricProvider(phase='liquid'))
    with pytest.raises(ValueError, match='increased absolute pressure'):
        evaluate_real_compression_path(request(stages=(stage(p=1e5),)), property_provider=IdealCaloricProvider())


@pytest.mark.parametrize('kind', ['capacity', 'temperature', 'approach'])
def test_supplied_limits_withhold_infeasible_conditions(kind):
    args = {}
    if kind == 'capacity':
        c = cooler('outlet_temperature', outlet_total_temperature_K=300., available_heat_rejection_W=1.)
    elif kind == 'approach':
        c = cooler('outlet_temperature', outlet_total_temperature_K=300., sink_temperature_K=300., minimum_approach_K=10.)
    else:
        c = cooler(); args['maximum_outlet_temperature_k'] = 301.
    r = evaluate_real_compression_path(request(stages=(stage(intercooler=c, **args),)), property_provider=IdealCaloricProvider())
    assert r.status == 'out_of_scope'


def test_cooler_pressure_loss_and_no_heating_boundary():
    c = cooler('outlet_temperature', outlet_total_temperature_K=310.).model_copy(update={'pressure_drop_pa': 1e4})
    result = evaluate_real_compression_path(request(stages=(stage(intercooler=c),)), property_provider=IdealCaloricProvider()).values
    assert result['stages'][0]['outlet']['pressure_pa_abs'] == 3.9e5
    with pytest.raises(ValueError, match='adds heat'):
        evaluate_real_compression_path(request(stages=(stage(intercooler=cooler('outlet_temperature', outlet_total_temperature_K=1000.)),)),
                                       property_provider=IdealCaloricProvider())


@pytest.mark.parametrize('mode', ['outlet_temperature', 'heat_removal'])
def test_native_cooled_two_stage_path_uses_actual_intermediate_pressure(mode):
    fluid, p1, p2, pc, p3 = 'HEOS::Methane', 2e6, 4e6, 3.9e6, 8e6

    def reference_temperature(pa, pb, ta, eta):
        def rhs(p, values):
            t = float(values[0])
            rho = PropsSI('Dmass', 'P', p, 'T', t, fluid)
            hp = PropsSI('d(Hmass)/d(P)|T', 'P', p, 'T', t, fluid)
            cp = PropsSI('Cpmass', 'P', p, 'T', t, fluid)
            return [(1/(eta*rho)-hp)/cp]
        solution = solve_ivp(rhs, (pa, pb), [ta], rtol=1e-11, atol=1e-9, max_step=(pb-pa)/40)
        assert solution.success
        return float(solution.y[0, -1])

    tc1 = reference_temperature(p1, p2, 300., .8)
    h1 = PropsSI('Hmass', 'P', p1, 'T', 300., fluid)
    hc1 = PropsSI('Hmass', 'P', p2, 'T', tc1, fluid)
    hcool = PropsSI('Hmass', 'P', pc, 'T', 310., fluid)
    tc2 = reference_temperature(pc, p3, 310., .85)
    hc2 = PropsSI('Hmass', 'P', p3, 'T', tc2, fluid)
    options = {'outlet_total_temperature_K': 310.} if mode == 'outlet_temperature' else {
        'heat_removal_W': .7*(hc1-hcool)}
    boundary = cooler(mode, **options).model_copy(update={'pressure_drop_pa': p2-pc})
    first = stage(p=p2, eta=.8, intercooler=boundary)
    second = stage(p=p3, eta=.85).model_copy(update={'id': 'stage-2'})
    result = evaluate_real_compression_path(request(inlet_pressure_pa_abs=p1, stages=(first, second))).values
    assert result['stages'][1]['inlet']['pressure_pa_abs'] == pc
    assert result['stages'][1]['inlet']['temperature_k'] == pytest.approx(310., abs=2e-6)
    assert result['specific_fluid_work_j_kg'] == pytest.approx((hc1-h1)+(hc2-hcool), rel=2e-7)
    assert result['specific_heat_rejected_j_kg'] == pytest.approx(hc1-hcool, rel=2e-7)
    assert result['specific_enthalpy_change_j_kg'] == pytest.approx(hc2-h1, rel=2e-7)
    assert abs(result['first_law_residual_j_kg']) < 1e-6


def test_provider_revision_and_requested_state_are_checked():
    class SwitchingProvider(IdealCaloricProvider):
        calls = 0

        def resolve(self, request):
            self.calls += 1
            result = super().resolve(request)
            return result.model_copy(update={'provider_revision': str(self.calls)})

    with pytest.raises(ValueError, match='revision changed'):
        evaluate_real_compression_path(request(), property_provider=SwitchingProvider())

    class WrongPressureProvider(IdealCaloricProvider):
        def resolve(self, request):
            return super().resolve(request).model_copy(update={'pressure_pa_abs': 2*request.inputs['P']})

    with pytest.raises(ValueError, match='requested pressure'):
        evaluate_real_compression_path(request(), property_provider=WrongPressureProvider())
