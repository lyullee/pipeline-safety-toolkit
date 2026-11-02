"""Independent physical thermodynamics and line/field conservation references."""
import math
import numpy as np
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.elastic_area_gas_field import (ElasticAreaLineEOS,ElasticAreaGasFieldRequest as Request,
    PhysicalElasticPipeBoundary as Boundary,solve_elastic_area_gas_field as solve)
from pipeline_integrity.gas_acoustic_pipe import (IdealGasInternalEnergyEOS,GasAcousticPipeSettings,
    GasPipeBoundary,GasPipeBoundaryStage,solve_gas_acoustic_pipe,gas_conservative_state)
A0=.01;R=290.;GAMMA=1.4;PE=1e5;P0=2e5;T0=300.

def fluid(reference=0.):return IdealGasInternalEnergyEOS(R,GAMMA,reference)
def eos(c=2e-9,reference=0.):return ElasticAreaLineEOS(fluid(reference),A0,c,PE,.05)
def request(n=16,length=10.,duration=.001,maximum_step=1e9,c=2e-9,p=None,t=None,v=None,heat=0.,tw=300.,kind='periodic',**kw):
 p=np.full(n,P0) if p is None else np.asarray(p);t=np.full(n,T0) if t is None else np.asarray(t);v=np.zeros(n) if v is None else np.asarray(v)
 d=dict(id='authored',reference_pipe_numerics=GasAcousticPipeSettings(length_m=length,inner_diameter_m=math.sqrt(4*A0/math.pi),duration_s=duration,maximum_step_s=maximum_step),
  area_compliance_m2_pa=c,exterior_pressure_pa_abs=PE,fixed_wall_temperature_K=tw,constant_heat_conductance_per_length_W_m_K=heat,maximum_reviewed_area_fractional_change=.05,
  initial_pressure_pa_abs=tuple(p),initial_temperature_K=tuple(t),initial_velocity_m_s=tuple(v),left=Boundary(kind=kind),right=Boundary(kind=kind),
  wall_constitutive_evidence_id='caller-reviewed-linear-compliance',fixed_length_quasistatic_linear_tensile_wall_and_constant_bath_reviewed=True)
 d.update(kw);return Request(**d)

@pytest.mark.parametrize('c,reference',[(0.,0.),(1e-10,-5e5),(2e-9,9e5)])
def test_actual_ideal_PVT_area_and_energy_restore(c,reference):
 e=eos(c,reference);p=2.2e5;t=321.;v=.4
 u=e.physical_to_owner_conservative(p,t,v);rho_line=u[0];energy=u[2]/rho_line-.5*v*v;s=e.recover_physical(rho_line,energy)
 assert s['pressure_pa_abs']==pytest.approx(p,rel=3e-14);assert s['temperature_K']==pytest.approx(t,rel=3e-14)
 area=A0+c*(p-PE);rho=p/(R*t);elastic=quad(lambda area_delta:area_delta/c,0,c*(p-PE))[0] if c else 0.
 external=PE*(area-A0)
 expected=rho*area*(fluid(reference).internal_energy_from_temperature(t)+.5*v*v)+elastic+external
 assert A0*u[2]==pytest.approx(expected,rel=3e-14)
 assert rho*area==pytest.approx(A0*u[0]);assert s['area_m2']==pytest.approx(area)

@pytest.mark.parametrize('p1,p2',[(1.7e5,2.4e5),(2.4e5,1.8e5)])
def test_physical_work_and_momentum_flux_identities(p1,p2):
 e=eos();c=e.area_compliance_m2_pa;s=e.physical_point(p2,T0)
 work=quad(lambda p:p*c,p1,p2)[0]
 psi=lambda p:e.physical_point(p,T0)['wall_plus_exterior_potential_J_m']
 assert psi(p2)-psi(p1)==pytest.approx(work)
 # Wall-side pressure traction integral is the difference between actual pA and canonical Pi.
 assert s['momentum_pressure_potential_N']+s['wall_plus_exterior_potential_J_m']==pytest.approx(p2*s['area_m2'])
 mass=s['mass_per_length_kg_m'];v=.7;ef=s['fluid_specific_internal_energy_J_kg'];line=mass*(ef+.5*v*v)+s['wall_plus_exterior_potential_J_m']
 assert v*(line+s['momentum_pressure_potential_N'])==pytest.approx(mass*v*(ef+.5*v*v+p2/s['fluid_density_kg_m3']))

@pytest.mark.parametrize('c',[0.,1e-10,2e-9])
def test_effective_wave_from_independent_physical_isentrope(c):
 e=eos(c);rho0=P0/(R*T0);h=.001*P0
 def mass(p):return rho0*(p/P0)**(1/GAMMA)*(A0+c*(p-PE))
 # Pi differences are independent integrals of actual pressure force coefficient A(p).
 dPi=quad(lambda p:A0+c*(p-PE),P0-h,P0+h)[0]
 derivative=dPi/(mass(P0+h)-mass(P0-h))
 p=e.physical_point(P0,T0)
 assert p['effective_wave_speed_m_s']**2==pytest.approx(derivative,rel=1e-6)
 assert p['effective_wave_speed_m_s']<=p['physical_fluid_sound_speed_m_s']

@pytest.mark.parametrize('c',[0.,2e-9])
def test_effective_line_cv_by_fixed_mass_energy_temperature_derivative(c):
 e=eos(c);base=e.physical_point(P0,T0);mass=base['mass_per_length_kg_m'];h=20.
 def data(p):
  area=A0+c*(p-PE);t=p*area/(mass*R)
  energy=mass*fluid().internal_energy_from_temperature(t)+quad(lambda pressure:pressure*c,PE,p)[0]
  return t,energy
 t1,u1=data(P0-h);t2,u2=data(P0+h)
 assert (u2-u1)/(mass*(t2-t1))==pytest.approx(base['effective_line_cv_J_kg_K'],rel=2e-10)


def test_C_zero_actual_rigid_FV_equivalence():
 n=24;length=10.;x=(np.arange(n)+.5)*length/n;p=P0+3*np.sin(2*math.pi*x/length);t=np.full(n,T0);v=.01*np.cos(2*math.pi*x/length)
 q=request(n=n,length=length,duration=.001,c=0.,p=p,t=t,v=v);a=solve(q,fluid_eos=fluid())
 initial=gas_conservative_state(p/(R*t),v,fluid().cv_J_kg_K*t)
 b=solve_gas_acoustic_pipe(initial,q.reference_pipe_numerics,eos=fluid(),boundary_stages=(GasPipeBoundaryStage(start_time_s=0.,left=GasPipeBoundary(kind='periodic'),right=GasPipeBoundary(kind='periodic')),))
 assert a.final.physical['pressure_pa_abs']==pytest.approx(b.pressure_pa_abs,abs=2e-7)
 assert a.final.physical['fluid_density_kg_m3']==pytest.approx(b.conservative[:,0],abs=2e-12)
 assert a.final.physical['velocity_m_s']==pytest.approx(b.velocity_m_s,abs=2e-11)


def test_homogeneous_heat_pressure_ODE_time_convergence_and_physical_energy_partition():
 c=2e-9;area=A0+c*(P0-PE);mass=P0/(R*T0)*area;cv=R/(GAMMA-1);heat=5.;tw=350.;duration=.8
 def derivative(time,state):
  p=state[0];area=A0+c*(p-PE);t=p*area/(mass*R)
  # Independent pressure-variable form of d(M cv T)/dt+p*dA/dt=Uline*(Tw-T).
  capacity=cv/R*(area+p*c)+p*c
  return [heat*(tw-t)/capacity]
 oracle=solve_ivp(derivative,(0,duration),[P0],method='DOP853',rtol=2e-12,atol=1e-8)
 assert oracle.success;target=oracle.y[0,-1];errors=[]
 for step in (.1,.05,.025):
  q=request(n=2,length=2000.,duration=duration,maximum_step=step,heat=heat,tw=tw,c=c)
  a=solve(q,fluid_eos=fluid());f=a.final.physical;errors.append(abs(f['pressure_pa_abs'][0]-target))
  dx=q.reference_pipe_numerics.length_m/2;heat_added=a.conservative_line_ledger.integrated_distributed_sources[2]
  dfluid=dx*np.sum(f['fluid_internal_energy_J_m']-a.initial.physical['fluid_internal_energy_J_m'])
  dPsi=dx*np.sum(f['wall_plus_exterior_potential_J_m']-a.initial.physical['wall_plus_exterior_potential_J_m'])
  assert dfluid+dPsi==pytest.approx(heat_added,abs=2e-6)
  assert f['area_m2'][0]>a.initial.physical['area_m2'][0]
  assert f['temperature_K'][0]>T0
  assert abs(a.conservative_line_ledger.residual[2])<2e-6
  rho_initial=P0/(R*T0)
  entropy=dx*np.sum(f['mass_per_length_kg_m']*(cv*np.log(f['temperature_K']/T0)-R*np.log(f['fluid_density_kg_m3']/rho_initial)))
  assert entropy-heat_added/tw>0 # Gas plus constant-temperature bath entropy production.
 assert errors[0]/errors[1]>3.5;assert errors[1]/errors[2]>3.5
 assert errors[-1]<2.


def test_periodic_smallwave_independent_characteristic_phase_and_grid_convergence():
 e=eos();base=e.physical_point(P0,T0);rho=P0/(R*T0);c=2e-9;h=.0001*P0
 # Characteristic reference comes from physical entropy and pressure-force integration, not the adapter eigenvalue.
 mass=lambda p:rho*(p/P0)**(1/GAMMA)*(A0+c*(p-PE))
 speed=math.sqrt(quad(lambda p:A0+c*(p-PE),P0-h,P0+h)[0]/(mass(P0+h)-mass(P0-h)))
 length=10.;time=.18*length/speed;amp=1.
 errors=[];last_phase=None
 for n in (32,64,128):
  x=(np.arange(n)+.5)*length/n;shape=np.sin(2*math.pi*x/length);p=P0+amp*shape
  t=T0*(p/P0)**((GAMMA-1)/GAMMA);v=amp/(rho*speed)*shape
  q=request(n=n,length=length,duration=time,p=p,t=t,v=v);a=solve(q,fluid_eos=fluid());observed=a.final.physical['pressure_pa_abs']-P0
  target=amp*np.sin(2*math.pi*(x-speed*time)/length);errors.append(np.mean(abs(observed-target)))
  sine=2*np.mean(observed*np.sin(2*math.pi*x/length));cosine=2*np.mean(observed*np.cos(2*math.pi*x/length));last_phase=math.atan2(-cosine,sine)
  assert max(abs(a.conservative_line_ledger.residual))<2e-8
 assert errors[0]>errors[1]>errors[2];assert errors[0]/errors[1]>1.6;assert errors[1]/errors[2]>1.6
 measured=last_phase*length/(math.tau*time)
 assert measured==pytest.approx(speed,rel=.002)
 assert abs(measured-base['physical_fluid_sound_speed_m_s'])>abs(measured-speed)*3


def test_periodic_energy_reference_shift_and_component_mass_energy_balances():
 n=24;x=(np.arange(n)+.5)/n;p=P0+20*np.sin(math.tau*x);t=T0*(p/P0)**((GAMMA-1)/GAMMA);v=.01*np.sin(math.tau*x)
 q=request(n=n,p=p,t=t,v=v,duration=.001);a=solve(q,fluid_eos=fluid(0.));b=solve(q,fluid_eos=fluid(5e5))
 for key in ('pressure_pa_abs','temperature_K','area_m2','fluid_density_kg_m3'):
  assert a.final.physical[key]==pytest.approx(b.final.physical[key],rel=2e-11,abs=2e-8)
 mass=a.conservative_line_ledger.final_mass_momentum_energy[0]
 assert b.conservative_line_ledger.final_mass_momentum_energy[2]-a.conservative_line_ledger.final_mass_momentum_energy[2]==pytest.approx(5e5*mass,rel=3e-13)
 for result in (a,b):
  f=result.final.physical;parts=f['fluid_internal_energy_J_m']+f['fluid_kinetic_energy_J_m']+f['elastic_energy_J_m']+f['exterior_pressure_potential_J_m']
  assert parts==pytest.approx(f['line_total_energy_J_m'],rel=3e-13)
  assert np.max(abs(result.conservative_line_ledger.residual))<2e-8
 assert a.request_digest!=b.request_digest

@pytest.mark.parametrize('kind',['reflective','prescribed_physical_PT_velocity_ghost'])
def test_supported_boundary_physical_restore_and_actual_conservative_ledgers(kind):
 n=12;q=request(n=n,kind='reflective',duration=.0005,v=np.full(n,.2 if kind=='reflective' else 0.))
 if kind!='reflective':
  q=q.model_copy(update=dict(left=Boundary(kind=kind,pressure_pa_abs=P0+100,temperature_K=T0,velocity_m_s=0.),right=Boundary(kind=kind,pressure_pa_abs=P0,temperature_K=T0,velocity_m_s=0.)))
 a=solve(q,fluid_eos=fluid());assert a.owner_line_coordinate_result.phase_by_cell==('gas',)*n
 assert np.max(abs(a.conservative_line_ledger.residual))<2e-8
 assert a.coordinate_and_domain_receipt['normalized_owner_coordinates'].startswith('rho_owner=M/A0')
 if kind=='reflective':assert abs(a.conservative_line_ledger.integrated_boundary_inflow[2])<1e-9
 else:assert a.conservative_line_ledger.integrated_boundary_inflow[0]>0

@pytest.mark.parametrize('case',['compression','strain','native_drag','nozzle','unreviewed'])
def test_out_of_domain_conditions_are_withheld(case):
 if case=='compression':
  with pytest.raises(ValueError):eos().physical_point(PE-1,T0)
 elif case=='strain':
  with pytest.raises(ValueError):eos().physical_point(PE+.06*A0/2e-9,T0)
 elif case=='native_drag':
  q=request();s=q.reference_pipe_numerics.model_copy(update=dict(darcy_friction_factor=.02))
  with pytest.raises(ValueError):request(reference_pipe_numerics=s)
 elif case=='nozzle':
  with pytest.raises(ValueError):Boundary(kind='pressure_outflow')
 else:
  with pytest.raises(ValueError):solve(request(fixed_length_quasistatic_linear_tensile_wall_and_constant_bath_reviewed=False),fluid_eos=fluid())


def test_adiabatic_periodic_physical_entropy_budget():
 n=64;length=10.;e=eos();base=e.physical_point(P0,T0);speed=base['effective_wave_speed_m_s'];rho0=P0/(R*T0)
 x=(np.arange(n)+.5)*length/n;shape=np.sin(math.tau*x/length);p=P0+200*shape
 t=T0*(p/P0)**((GAMMA-1)/GAMMA);v=200/(rho0*speed)*shape
 q=request(n=n,length=length,duration=.12*length/speed,p=p,t=t,v=v);a=solve(q,fluid_eos=fluid())
 def entropy(snapshot):
  f=snapshot.physical
  return length/n*np.sum(f['mass_per_length_kg_m']*(R/(GAMMA-1)*np.log(f['temperature_K']/T0)-R*np.log(f['fluid_density_kg_m3']/rho0)))
 production=entropy(a.final)-entropy(a.initial)
 assert production>1e-10
 assert np.max(abs(a.conservative_line_ledger.integrated_boundary_inflow))<1e-8
 assert np.max(abs(a.conservative_line_ledger.integrated_distributed_sources))==0
 assert np.max(abs(a.conservative_line_ledger.residual))<2e-8
