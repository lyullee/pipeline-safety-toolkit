"""Selected charge/mass capacity relation through the existing protection owner."""
import math
import pytest
m=pytest.importorskip('matintegrity.models.protection')
from scipy.integrate import quad

def make(**kw):
 return m.CathodicBudget(**(dict(time_s=[0.,86400.],protected_area_m2=12.,bare_surface_fraction=[.1,.1],bare_surface_current_density_A_m2=[.02,.02],additional_current_drain_A=[.01,.01],anode_electrochemical_capacity_C_kg=3e6,anode_utilization_fraction=.85,single_anode_mass_kg=.01,single_anode_minimum_available_current_A=.1,installed_anodes=1)|kw))

@pytest.mark.parametrize('duration,area,drain',[(86400.,12.,.01),(365.*86400.,100.,0.),(10.,1.,.2)])
def test_constant_load_charge_and_anode_capacity(duration,area,drain):
 q=make(time_s=[0.,duration],protected_area_m2=area,additional_current_drain_A=[drain,drain]);r=m.cathodic_budget(q).values
 current=area*.1*.02+drain;charge=current*duration
 assert r['required_charge_C']==pytest.approx(charge,rel=2e-14)
 assert r['required_continuous_anode_mass_kg']==pytest.approx(charge/(3e6*.85),rel=2e-14)
 assert r['mean_current_A']==pytest.approx(current,rel=2e-14)
 assert r['minimum_anodes_by_charge']==math.ceil(charge/(3e6*.85*.01))
 assert r['installed_charge_margin_C']==pytest.approx(.01*3e6*.85-charge,abs=1e-9)

@pytest.mark.parametrize('end_fraction,end_density',[(.5,.04),(.9,.001),(.2,.1)])
def test_varying_demand_against_independent_quadrature(end_fraction,end_density):
 q=make(bare_surface_fraction=[.1,end_fraction],bare_surface_current_density_A_m2=[.02,end_density],additional_current_drain_A=[.01,.03]);r=m.cathodic_budget(q).values
 def load(t):
  u=t/86400.;return 12*(.1+u*(end_fraction-.1))*(.02+u*(end_density-.02))+.01+u*.02
 charge=quad(load,0.,86400.,epsabs=1e-8,epsrel=1e-13)[0]
 assert r['required_charge_C']==pytest.approx(charge,rel=2e-13)
 assert r['peak_current_A']>=max(load(0.),load(86400.))-1e-14

@pytest.mark.parametrize('scale',[.1,10.])
def test_time_and_charge_capacity_scale(scale):
 a=m.cathodic_budget(make()).values;b=m.cathodic_budget(make(time_s=[0.,86400.*scale])).values
 assert b['required_charge_C']==pytest.approx(a['required_charge_C']*scale)
 assert b['required_continuous_anode_mass_kg']==pytest.approx(a['required_continuous_anode_mass_kg']*scale)
 assert b['mean_current_A']==pytest.approx(a['mean_current_A'])

def test_charge_budget_does_not_supply_protective_potential():
 r=m.cathodic_budget(make()).values
 assert 'protective_potential_V' not in r and 'corrosion_rate' not in r

@pytest.mark.parametrize('kw',[{'time_s':[1.,1.]},{'anode_utilization_fraction':0.},{'bare_surface_fraction':[.1,1.1]}])
def test_invalid_physical_inputs(kw):
 with pytest.raises(ValueError):make(**kw)
