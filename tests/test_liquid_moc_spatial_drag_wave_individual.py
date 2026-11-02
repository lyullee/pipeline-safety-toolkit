"""A nonuniform-drag acoustic pulse against an independent modal field solve."""
import math
import json
import os
from pathlib import Path
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.integrate import solve_ivp
from pipeline_integrity.liquid_moc_friction import LiquidMOCQuasisteadyDarcy
from pipeline_integrity.transient import History,simulate_liquid_transient
from test_liquid_moc_state_friction_individual import fixture,CONTEXT,G

L=10.;C=100.;D=.01;T=.16;PULSE=.06;AMP=.002

def boundary(t):
    return AMP*math.sin(math.pi*t/PULSE)**2 if 0<=t<PULSE else 0.

def derivative(t):
    return AMP*math.pi/PULSE*math.sin(2*math.pi*t/PULSE) if 0<=t<PULSE else 0.

def modal_reference(count):
    """Sine pressure/cosine velocity Galerkin, with independent dense ODE.

    h=a(t)(1-x/L)+sum A_n sin(n*pi*x/L).
    v=a'(t)*ell(x)+sum V_n cos(n*pi*x/L), ell'=-(g/c²)(1-x/L).
    The zero-mean quadratic velocity lift enforces the actual nonzero endpoint
    velocity gradient. A plain cosine trace converges too slowly at the driven
    boundary; that earlier failed32/64-mode attempt is retained in provenance.
    The viscosity profile is a spatial multiplication operator, not a frozen
    scalar damping or the production characteristic-foot update.
    """
    xi,w=leggauss(2*count+8);x=(xi+1)/2;w=w/2
    modes=np.arange(count+1)
    basis=np.cos(np.pi*modes[:,None]*x)
    beta=4*(1+.5*np.sin(2*np.pi*x))
    drag=(basis*(w*beta))@basis.T
    drag[1:]*=2
    lift=G*L/C**2*(1/3-x+x*x/2)
    lift_coeff=basis@(w*lift);lift_coeff[1:]*=2
    drag_lift=basis@(w*beta*lift);drag_lift[1:]*=2
    wave=np.pi*np.arange(1,count+1)/L
    a=np.zeros((2*count+1,2*count+1))
    a[:count,count+1:]=np.diag(C*C/G*wave)
    a[count+1:,:count]=-np.diag(G*wave)
    a[count:,count:]=-drag
    def forced_rhs(t,y):
        f=a@y
        da=AMP*math.pi/PULSE*math.sin(2*math.pi*t/PULSE)
        dda=2*AMP*(math.pi/PULSE)**2*math.cos(2*math.pi*t/PULSE)
        f[count:]-=da*drag_lift+dda*lift_coeff
        f[count]+=G*boundary(t)/L
        return f
    first=solve_ivp(forced_rhs,(0,PULSE),np.zeros(2*count+1),method='DOP853',rtol=2e-11,atol=2e-14,dense_output=True,max_step=PULSE/20)
    second=solve_ivp(lambda t,y:a@y,(PULSE,T),first.y[:,-1],method='DOP853',rtol=2e-11,atol=2e-14,dense_output=True,max_step=PULSE/20)
    assert first.success and second.success
    def velocity_trace(times):
        tt=np.asarray(times)
        solution=np.where((tt<=PULSE)[None,:],first.sol(np.minimum(tt,PULSE)),second.sol(np.maximum(tt,PULSE)))
        return np.sum(solution[count:],axis=0)+np.array([derivative(float(t)) for t in tt])*G*L/(3*C*C)
    return velocity_trace

def test_spatial_viscosity_characteristic_feet_wave_and_work_budget():
    reference32=modal_reference(32);reference64=modal_reference(64)
    errors=[];energy_defects=[];mass_errors=[];metrics=[]
    for segments in [40,80,160]:
        dt=L/(C*segments)
        times=np.arange(round(T/dt)+1)*dt
        # End clock is exactly the acoustic owner clock, not a rounded scan.
        times[-1]=T
        spec=fixture(dt,v0=0.,drive=0.,duration=T)
        up=spec.nodes[0].model_copy(update={'history':History(time_s=tuple(map(float,times)),values=tuple(40+boundary(float(t)) for t in times))})
        spec=spec.model_copy(update={'nodes':(up,spec.nodes[1])})
        states=[]
        def viscosity(s):
            states.append(s)
            return tuple(4*(1+.5*math.sin(2*math.pi*x/L))*D*D/32 for x in s.chainage_m)
        result=simulate_liquid_transient(spec,CONTEXT,momentum_decay_rate_provider=LiquidMOCQuasisteadyDarcy(viscosity),momentum_decay_rate_evidence_id='authored_spatial_viscosity_profile')
        clock=np.array(result['time_s'])
        speed=np.array(result['pipes']['pipe']['start_flow_m3_s'])/(math.pi*D*D/4)
        high=reference64(clock)
        assert np.max(np.abs(high-reference32(clock)))<1e-6
        errors.append(float(np.max(np.abs(speed-high))))
        assert result['status']=='computed'
        mass_errors.append(result['maximum_pipe_volume_residual_m3'])
        # Use actual internal station states supplied at the previous-time
        # characteristic feet, through the penultimate physical time.
        states={s.time_s:s for s in states}
        last=states[sorted(states)[-1]]
        last_t=last.time_s;indices=clock<=last_t
        work=np.trapezoid(G*np.array([boundary(float(t)) for t in clock[indices]])*speed[indices],clock[indices])
        diss=[];st=[]
        for t,s in sorted(states.items()):
            x=np.array(s.chainage_m);v=np.array(s.velocity_m_s)
            beta=4*(1+.5*np.sin(2*np.pi*x/L))
            diss.append(np.trapezoid(beta*v*v,x));st.append(t)
        loss=np.trapezoid(diss,st)
        x=np.array(last.chainage_m);v=np.array(last.velocity_m_s)
        h=(np.array(last.pressure_pa_abs)-101325.)/(1000*G)-40.
        energy=.5*np.trapezoid(v*v+(G*h/C)**2,x)
        energy_defects.append(abs(energy+loss-work)/max(abs(work),1e-30))
        metrics.append(dict(segments=segments,timestep_s=result['timestep_s'],boundary_velocity_error_m_s=errors[-1],
            maximum_volume_residual_m3=mass_errors[-1],relative_mechanical_energy_defect=energy_defects[-1],
            modal32_64_difference_m_s=float(np.max(np.abs(high-reference32(clock)))),
            fixed_budgets_met=bool(errors[-1]<3e-6 and mass_errors[-1]<1e-12 and energy_defects[-1]<.02)))
        if os.environ.get('PIPELINE_PHYSICAL_METRICS_PATH'):
            Path(os.environ['PIPELINE_PHYSICAL_METRICS_PATH']).write_text(json.dumps(metrics,indent=2)+'\n',encoding='utf8')
    assert errors[-1]<3e-6
    assert errors[-1]<errors[0]/2.5
    # Original40-cell volume defect exceeded this unchanged budget. Retain
    # and label coarse results; only the refined endpoint may qualify.
    assert mass_errors[-1]<1e-12
    assert mass_errors[-1]<mass_errors[0]
    assert energy_defects[-1]<.02
    assert energy_defects[-1]<energy_defects[0]
