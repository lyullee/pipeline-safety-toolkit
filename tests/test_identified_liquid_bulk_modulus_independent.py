"""Independent Tait constitutive law and distinct PT/PS liquid derivatives."""
import math
import pytest
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.identified_liquid_bulk_modulus import LiquidBulkModulusRequest as Request,observe_identified_liquid_bulk_modulus as observe
from pipeline_integrity.thermo import ThermodynamicState


class TaitProvider:
    B=1.8e8;n=7.;rho0=850.;p0=1e5
    def __init__(self,phase='liquid',sound=True,revision=False,noise=False):self.phase=phase;self.sound=sound;self.revision=revision;self.noise=noise
    def resolve(self,q):
        p=q.inputs['P'];t=q.inputs['T'];u=1+(p-self.p0)/self.B;rho=self.rho0*u**(1/self.n)
        if self.noise:rho*=1+1e-4*math.sin(p/2000)
        modulus=self.n*(self.B+p-self.p0)
        h=2200*t+self.B*self.n/(self.rho0*(self.n-1))*(u**((self.n-1)/self.n)-1)
        return ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=p,temperature_k=t,density_kg_m3=rho,
            enthalpy_j_kg=h,entropy_j_kg_k=2200*math.log(t/300),phase=self.phase,viscosity_pa_s=None,
            cp_j_kg_k=2200,cv_j_kg_k=2200,conductivity_w_m_k=None,
            speed_of_sound_m_s=math.sqrt(modulus/rho) if self.sound else None,
            provider_version='manufactured-thermodynamic-Tait',provider_revision=str(p) if self.revision else 'fixed')


def request(**changes):
    d=dict(fluid_model='n-Decane',pressure_Pa_abs=2e6,temperature_K=300.,derivative_basis='both',
        material_assignment_id='explicit-material-PVT',modeled_material_kind='identified_hydrocarbon_surrogate',
        identified_material_and_local_liquid_PT_domain_reviewed=True)
    d.update(changes);return Request(**d)


@pytest.mark.parametrize('p',[1e5,2e6,2e7])
@pytest.mark.parametrize('basis',['isothermal','isentropic','both'])
def test_independent_tait_pressure_volume_law_and_zero_expansion_caloric_identity(p,basis):
    provider=TaitProvider();r=observe(request(pressure_Pa_abs=p,derivative_basis=basis),property_provider=provider)
    expected=provider.n*(provider.B+p-provider.p0)
    assert r.status=='computed'
    assert r.isothermal_bulk_modulus_Pa==pytest.approx(expected,rel=2e-7)
    assert r.isentropic_bulk_modulus_Pa==pytest.approx(expected,rel=2e-14)
    assert r.acoustic_to_isothermal_modulus_ratio==pytest.approx(r.cp_over_cv,rel=2e-7)
    assert r.derivative_relative_change<1e-5


@pytest.mark.parametrize('fluid,p,t',[('n-Decane',1e6,300.),('n-Dodecane',2e6,330.),('n-Hexane',5e6,320.)])
def test_native_hydrocarbon_distinct_isothermal_and_isentropic_derivative_references(fluid,p,t):
    r=observe(request(fluid_model=fluid,pressure_Pa_abs=p,temperature_K=t));ref='HEOS::'+fluid
    assert r.status=='computed'
    kt=1/PropsSI('isothermal_compressibility','P',p,'T',t,ref)
    entropy=PropsSI('Smass','P',p,'T',t,ref);h=1000.
    # Independent PS-flash density derivative follows an isentrope, unlike the
    # production fixed-temperature density probes and native acoustic output.
    def rho(pressure):
        temperature=PropsSI('T','P',pressure,'Smass',entropy,ref)
        # Some native PS flashes stop at micro-J/kg/K entropy residuals that
        # jump between neighboring pressures. Enforce the actual isentrope
        # independently using dS/dT|P=Cp/T rather than selecting a favorable h.
        for _ in range(3):
            error=PropsSI('Smass','P',pressure,'T',temperature,ref)-entropy
            cp=PropsSI('Cpmass','P',pressure,'T',temperature,ref)
            temperature-=error*temperature/cp
        assert abs(PropsSI('Smass','P',pressure,'T',temperature,ref)-entropy)<1e-8
        return PropsSI('Dmass','P',pressure,'T',temperature,ref)
    derivative=((rho(p-2*h)-rho(p+2*h))+8*(rho(p+h)-rho(p-h)))/(12*h)
    ks=r.density_kg_m3/derivative
    assert r.isothermal_bulk_modulus_Pa==pytest.approx(kt,rel=2e-6)
    assert r.isentropic_bulk_modulus_Pa==pytest.approx(ks,rel=2e-5)
    assert r.acoustic_to_isothermal_modulus_ratio==pytest.approx(r.cp_over_cv,rel=2e-6)
    assert r.isentropic_bulk_modulus_Pa>r.isothermal_bulk_modulus_Pa
    assert not r.generic_crude_from_API_gravity_inferred


@pytest.mark.parametrize('change',[{'fluid_model':None,'reference_API_gravity':35.},{'derivative_basis':None},
    {'material_assignment_id':None},{'identified_material_and_local_liquid_PT_domain_reviewed':False}])
def test_unknown_material_or_thermal_basis_is_not_reconstructed_from_gravity(change):
    assert observe(request(**change),property_provider=TaitProvider()).status=='needs_data'


def test_missing_acoustic_state_preserves_isothermal_information():
    r=observe(request(),property_provider=TaitProvider(sound=False))
    assert r.status=='needs_data' and r.isothermal_bulk_modulus_Pa>0 and r.isentropic_bulk_modulus_Pa is None
    r=observe(request(derivative_basis='isothermal'),property_provider=TaitProvider(sound=False))
    assert r.status=='computed'


def test_phase_revision_pressure_support_and_unresolved_derivative_guards():
    assert observe(request(),property_provider=TaitProvider(phase='gas')).status=='out_of_scope'
    assert observe(request(),property_provider=TaitProvider(revision=True)).status=='out_of_scope'
    assert observe(request(pressure_probe_step_Pa=2e6),property_provider=TaitProvider()).status=='out_of_scope'
    assert observe(request(),property_provider=TaitProvider(noise=True)).status=='not_converged'
