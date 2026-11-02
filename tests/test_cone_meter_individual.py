"""Independent annular-area SI capacity and declared operating geometry checks."""
import math,runpy
from pathlib import Path
import pytest
from pipeline_integrity.gas_meter import evaluate_gas_meter,boundary_metadata
from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot,integrate_thermal_expansion
MAKE=runpy.run_path(str(Path(__file__).resolve().parents[1]/'examples/gas_meter.py'))['make_case']

def evaluate(q,c):
 c=c.model_copy(deep=True);c.metadata['gas_meter_boundary']=boundary_metadata(q)
 return evaluate_gas_meter(q,c)

def oracle(q,r):
 D=q.geometry.inner_pipe_diameter_m;d=q.geometry.secondary_dimension_m
 A=math.pi*(D*D-d*d)/4;area_fraction=A/(math.pi*D*D/4)
 v=r['values'];rho=v['upstream_density_kg_m3']
 return q.specified_discharge_coefficient*q.specified_expansibility_factor*A*math.sqrt(2*rho*(q.upstream_pressure_pa_abs-q.downstream_pressure_pa_abs)/(1-area_fraction**2))

@pytest.mark.parametrize('diameter,cone,dp',[(.1,.075,5000),(.15,.1,2000),(.08,.06,1000),(.1,.09,7000),(.1,.04,1000),(.2,.16,2000)])
def test_independent_annular_capacity_and_reference_mass(diameter,cone,dp):
 q,c=MAKE(reviewed=True,meter_type='cone meter');q=q.model_copy(update={'geometry':q.geometry.model_copy(update={'inner_pipe_diameter_m':diameter,'secondary_dimension_m':cone}),'downstream_pressure_pa_abs':q.upstream_pressure_pa_abs-dp})
 r=evaluate(q,c);assert r['status']=='computed';v=r['values'];m=oracle(q,r)
 assert v['signed_mass_flow_kg_s']==pytest.approx(m,rel=3e-12)
 assert v['signed_actual_volume_flow_m3_s']*v['upstream_density_kg_m3']==pytest.approx(m,rel=3e-12)
 assert v['signed_reference_volume_flow_m3_s']*v['reference_density_kg_m3']==pytest.approx(m,rel=3e-12)
 assert v['beta']==pytest.approx(math.sqrt(1-(cone/diameter)**2),rel=1e-13)

@pytest.mark.parametrize('scale',[.5,2.,3.])
def test_similar_geometry_scales_area(scale):
 q,c=MAKE(reviewed=True,meter_type='cone meter');r=evaluate(q,c)
 qq=q.model_copy(update={'geometry':q.geometry.model_copy(update={'inner_pipe_diameter_m':q.geometry.inner_pipe_diameter_m*scale,'secondary_dimension_m':q.geometry.secondary_dimension_m*scale})})
 rr=evaluate(qq,c);assert rr['status']=='computed'
 assert rr['values']['signed_mass_flow_kg_s']/r['values']['signed_mass_flow_kg_s']==pytest.approx(scale**2,rel=3e-12)

@pytest.mark.parametrize('alpha_pipe,alpha_cone,temperature',[(1e-5,1e-5,320.),(1e-5,2e-5,330.),(2e-5,0.,280.)])
def test_operating_dimensions_reuse_reviewed_thermal_curve(alpha_pipe,alpha_cone,temperature):
 q,c=MAKE(reviewed=True,meter_type='cone meter');dims=[]
 for value,a in [(q.geometry.inner_pipe_diameter_m,alpha_pipe),(q.geometry.secondary_dimension_m,alpha_cone)]:
  curve=ExpansionCurve(id='authored-curve',material_id='authored-material',knots=(ExpansionKnot(temperature_K=270,tangent_expansion_per_K=a),ExpansionKnot(temperature_K=340,tangent_expansion_per_K=a)),evidence_id='authored',tangent_small_engineering_strain_basis_reviewed=True)
  eps=integrate_thermal_expansion(curve,300,temperature)['free_engineering_strain'];assert eps==pytest.approx(a*(temperature-300),abs=1e-15);dims.append(value*(1+eps))
 qq=q.model_copy(update={'geometry':q.geometry.model_copy(update={'inner_pipe_diameter_m':dims[0],'secondary_dimension_m':dims[1]}),'upstream_temperature_K':temperature})
 rr=evaluate(qq,c);assert rr['status']=='computed'
 assert rr['values']['signed_mass_flow_kg_s']==pytest.approx(oracle(qq,rr),rel=3e-12)

@pytest.mark.parametrize('dp',[1000.,5000.,10000.])
def test_native_cone_solution_against_separately_bracketed_coefficient_relation(dp):
 from fluids.flow_meter import differential_pressure_meter_C_epsilon
 from scipy.optimize import brentq
 q,c=MAKE(reviewed=True,mode='native_correlation',meter_type='cone meter');q=q.model_copy(update={'downstream_pressure_pa_abs':q.upstream_pressure_pa_abs-dp})
 r=evaluate(q,c);assert r['status']=='computed';v=r['values'];D=q.geometry.inner_pipe_diameter_m;d=q.geometry.secondary_dimension_m
 state=next(x['output'] for x in r['native_calls'] if x['function']=='CoolPropProvider.resolve');rho=v['upstream_density_kg_m3'];mu=state['viscosity_pa_s'];A=math.pi*(D*D-d*d)/4;f=1-(d/D)**2
 def residual(m):
  cd,y=differential_pressure_meter_C_epsilon(D=D,D2=d,m=m,P1=q.upstream_pressure_pa_abs,P2=q.downstream_pressure_pa_abs,rho=rho,mu=mu,k=v['exponent_supplied_to_native'],meter_type='cone meter')
  return m-cd*y*A*math.sqrt(2*rho*dp/(1-f*f))
 assert v['signed_mass_flow_kg_s']==pytest.approx(brentq(residual,1e-8,10),rel=2e-11)
 assert v['reynolds']==pytest.approx(4*v['signed_mass_flow_kg_s']/(math.pi*D*mu),rel=2e-13)

@pytest.mark.parametrize('field',['geometry','support','reference'])
def test_unknown_is_withheld(field):
 q,c=MAKE(reviewed=True,meter_type='cone meter');r=evaluate(q.model_copy(update={field:None}),c)
 assert r['status']=='needs_data' and not r['native_called']
