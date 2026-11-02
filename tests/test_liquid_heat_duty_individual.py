"""Independent integrated-energy and coolant-state checks, without engine tables."""
import math
import pytest
from scipy.integrate import quad
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.liquid_heat_duty import LiquidHeatDutyRequest, size_liquid_heat_duty_flow
from pipeline_integrity.thermo import ThermodynamicState


def request(**changes):
    fields = dict(fluid='Water', pressure_Pa_abs=2e5, inlet_temperature_K=300.,
        outlet_temperature_K=340., heat_to_coolant_W=8e5, property_basis_id='test-coolant',
        same_composition_single_liquid_isobaric_path_reviewed=True,
        steady_no_shaft_work_negligible_kinetic_potential_change_reviewed=True)
    fields.update(changes)
    return LiquidHeatDutyRequest(**fields)


class CaloricLiquid:
    def __init__(self, *, reference=0., phase='liquid', cp=4000., slope=0., wrong=None):
        self.reference, self.phase, self.cp, self.slope, self.wrong = reference, phase, cp, slope, wrong

    def resolve(self, q):
        t, p = q.inputs['T'], q.inputs['P']
        h = self.reference + self.cp * (t-300.) + .5*self.slope*(t-300.)**2
        return ThermodynamicState(fluid='Other' if self.wrong=='fluid' else q.fluid,
            backend=q.backend, pressure_pa_abs=p+100 if self.wrong=='pressure' else p,
            temperature_k=t+1 if self.wrong=='temperature' else t,
            density_kg_m3=1000./(1.+.001*(t-300.)), enthalpy_j_kg=h,
            entropy_j_kg_k=0., phase=self.phase, viscosity_pa_s=None,
            cp_j_kg_k=self.cp if self.cp>0 else None, cv_j_kg_k=None,
            conductivity_w_m_k=None, speed_of_sound_m_s=None,
            provider_version='synthetic-1', provider_revision='independent-test-fixture')


@pytest.mark.parametrize('mass,duration,slope', [(2., 11., 0.), (.02, 130., 3.), (83., .7, -2.)])
def test_integrated_calorimetric_energy(mass, duration, slope):
    # Integrate the independently prescribed heat-capacity curve over a batch.
    energy = mass*quad(lambda t:4000.+slope*(t-300.), 300., 340.)[0]
    r=size_liquid_heat_duty_flow(request(heat_to_coolant_W=energy/duration),
        property_provider=CaloricLiquid(slope=slope))
    assert r.status=='computed'
    assert r.coolant_mass_flow_kg_s==pytest.approx(mass/duration, rel=2e-13)
    assert r.inlet_volume_flow_m3_s==pytest.approx(mass/duration/1000.)
    assert r.outlet_volume_flow_m3_s==pytest.approx(mass/duration*1.04/1000.)
    assert abs(r.energy_residual_W)<1e-12*energy/duration


@pytest.mark.parametrize('offset', [-9e8, 1e7])
def test_enthalpy_reference_invariance(offset):
    a=size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid())
    b=size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid(reference=offset))
    assert b.coolant_mass_flow_kg_s==pytest.approx(a.coolant_mass_flow_kg_s)
    assert b.inlet_state.enthalpy_j_kg-a.inlet_state.enthalpy_j_kg==offset


@pytest.mark.parametrize('scale', [.001, 19.])
def test_extensive_duty_scaling(scale):
    a=size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid())
    b=size_liquid_heat_duty_flow(request(heat_to_coolant_W=8e5*scale),property_provider=CaloricLiquid())
    assert b.coolant_mass_flow_kg_s==pytest.approx(a.coolant_mass_flow_kg_s*scale)


def test_zero_heat_is_zero_flow_not_missing():
    r=size_liquid_heat_duty_flow(request(heat_to_coolant_W=0),property_provider=CaloricLiquid())
    assert r.status=='computed' and r.coolant_mass_flow_kg_s==0 and r.energy_residual_W==0


@pytest.mark.parametrize('change', [dict(heat_to_coolant_W=None),
    dict(same_composition_single_liquid_isobaric_path_reviewed=False),
    dict(steady_no_shaft_work_negligible_kinetic_potential_change_reviewed=False)])
def test_unknown_boundary_never_calls_provider(change):
    class MustNotCall:
        def resolve(self,q):raise AssertionError('unreviewed problem must not resolve properties')
    r=size_liquid_heat_duty_flow(request(**change),property_provider=MustNotCall())
    assert r.status=='needs_data' and r.coolant_mass_flow_kg_s is None


@pytest.mark.parametrize('phase', ['gas','supercritical','supercritical_liquid'])
def test_other_phase_is_withheld(phase):
    r=size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid(phase=phase))
    assert r.status=='out_of_scope' and r.coolant_mass_flow_kg_s is None


@pytest.mark.parametrize('cp', [0.,-200.])
def test_nonpositive_enthalpy_rise_withheld(cp):
    r=size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid(cp=cp))
    assert r.status=='out_of_scope'


@pytest.mark.parametrize('wrong', ['fluid','pressure','temperature'])
def test_provider_preserves_fluid_and_endpoint(wrong):
    with pytest.raises(ValueError):size_liquid_heat_duty_flow(request(),property_provider=CaloricLiquid(wrong=wrong))


@pytest.mark.parametrize('change', [dict(outlet_temperature_K=300),dict(outlet_temperature_K=299),
    dict(pressure_Pa_abs=0),dict(heat_to_coolant_W=-1)])
def test_invalid_input(change):
    with pytest.raises(ValueError):request(**change)


@pytest.mark.parametrize('backend', ['HEOS','IF97'])
def test_actual_water_against_integrated_cp(backend):
    q=request(backend=backend)
    integral=quad(lambda t:PropsSI('Cpmass','P',q.pressure_Pa_abs,'T',t,backend+'::Water'),300.,340.,epsabs=1e-6)[0]
    r=size_liquid_heat_duty_flow(q)
    assert r.status=='computed'
    assert r.coolant_mass_flow_kg_s==pytest.approx(q.heat_to_coolant_W/integral,rel=3e-9)
    assert r.enthalpy_rise_J_kg==pytest.approx(integral,rel=3e-9)
    # Actual endpoint volume rates differ; neither is silently a reference rate.
    assert r.outlet_volume_flow_m3_s>r.inlet_volume_flow_m3_s
    assert math.isclose(r.coolant_mass_flow_kg_s*r.enthalpy_rise_J_kg,q.heat_to_coolant_W,rel_tol=2e-15)


def test_actual_water_vapor_endpoints_not_liquid():
    r=size_liquid_heat_duty_flow(request(pressure_Pa_abs=1e5,inlet_temperature_K=420,outlet_temperature_K=460))
    assert r.status=='out_of_scope' and r.coolant_mass_flow_kg_s is None
