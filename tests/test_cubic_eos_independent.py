"""Primitive cubic pressure-volume roots; no new equation-of-state calibration."""
import math
import pytest
from scipy.optimize import brentq
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.thermo import CoolPropProvider,StateRequest

@pytest.mark.parametrize('backend',['PR','SRK'])
@pytest.mark.parametrize('fluid,p,t',[('Methane',1e5,300.),('Methane',3e6,350.),('Nitrogen',2e6,350.),('CarbonDioxide',8e6,400.)])
def test_pure_supercritical_temperature_state_matches_independent_cubic_volume_root(backend,fluid,p,t,record_property):
    ref=backend+'::'+fluid
    tc=PropsSI('Tcrit',ref);pc=PropsSI('Pcrit',ref);omega=PropsSI('acentric',ref);R=PropsSI('gas_constant',ref)
    if backend=='PR':
        k=.37464+1.54226*omega-.26992*omega**2
        a=.4572355289213822*R*R*tc*tc/pc;b=.0777960739038885*R*tc/pc
        denominator=lambda v:v*(v+b)+b*(v-b)
    else:
        k=.480+1.574*omega-.176*omega**2
        a=.4274802335403414*R*R*tc*tc/pc;b=.0866403499649577*R*tc/pc
        denominator=lambda v:v*(v+b)
    alpha=(1+k*(1-math.sqrt(t/tc)))**2
    def pressure(v):return R*t/(v-b)-a*alpha/denominator(v)
    volume=brentq(lambda v:pressure(v)-p,b*(1+1e-8),max(100*b,10*R*t/p),xtol=1e-16,rtol=1e-13)
    molar=PropsSI('molar_mass',ref)
    provider=CoolPropProvider(use_cache=False)
    try:
        r=provider.resolve(StateRequest(fluid=fluid,backend=backend,inputs={'P':p,'T':t}))
        record_property('resolved_pair','P,T')
    except ValueError as error:
        # A supported alternate coordinate pair can be tested without guessing
        # a phase or silently treating an unresolved pressure input as a state.
        assert backend=='PR' and fluid=='Nitrogen'
        assert 'Unable to find gaseous density' in str(error)
        record_property('retained_PT_backend_limitation',str(error))
        r=provider.resolve(StateRequest(fluid=fluid,backend=backend,inputs={'T':t,'Dmass':molar/volume}))
        record_property('resolved_pair','T,Dmass')
    assert r.pressure_pa_abs==pytest.approx(p,rel=3e-8)
    assert r.density_kg_m3==pytest.approx(molar/volume,rel=3e-8)
    assert pressure(molar/r.density_kg_m3)==pytest.approx(p,rel=3e-8)
    assert r.phase in ('gas','supercritical_gas','supercritical')
