"""Independent no-slip profiles and velocity-integral closure through the existing solver."""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from pipeline_integrity import slurry_transport as st
from pipeline_integrity.contracts import digest

_spec=importlib.util.spec_from_file_location("profile_slurry_example",Path(__file__).parents[1]/"examples/slurry_transport.py")
_example=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def manufactured_flow(n,yield_stress,wall_tau):
    radius=.025;excess=wall_tau-yield_stress
    # Exact integral of the shear-rate law after the stress coordinate substitution.
    integral=(excess**3/(3+1/n)+2*yield_stress*excess**2/(2+1/n)
              +yield_stress**2*excess/(1+1/n))/wall_tau**3
    return math.pi*radius**3*(excess/.1)**(1/n)*integral


def case(rheology="bingham",n=1.,yield_stress=5.,flow=.0001,positions=(0.,.25,.5,.75,1.),**kwargs):
    q,_=_example.make_case(reviewed=True)
    properties=q.properties.model_copy(update=dict(rheology=rheology,flow_index=n,yield_stress_Pa=yield_stress,
        flow_regime="newtonian_owner" if rheology=="newtonian" else "laminar_supplied",
        supported_absolute_velocity_m_s=(0.,20.)))
    if "properties" in kwargs:properties=kwargs.pop("properties")
    return _example.make_case(reviewed=True,properties=properties,volume_flow_m3_s=flow,
        radial_positions_fraction=positions,root_absolute_stress_tolerance_Pa=1e-11,
        quadrature_absolute_error_m3_s=1e-15,**kwargs)


def execute(**kwargs):
    q,c=case(**kwargs)
    result=st.evaluate_slurry_transport(q,c)
    assert result["status"]=="computed_diagnostic",result["notes"]
    return q,c,result,result["values"]["radial_velocity_profile"]


@pytest.mark.parametrize("n", [.4,.7,1.,1.4,2.])
@pytest.mark.parametrize("sign", [-1.,1.])
def test_power_law_profile_matches_direct_integrated_velocity_law(n,sign):
    tau=.1*4**n;flow=sign*manufactured_flow(n,0.,tau)
    q,_,result,profile=execute(rheology="power_law",n=n,yield_stress=0.,flow=flow)
    radius=q.inner_diameter_m/2;gradient=2*tau/radius
    positions=q.radial_positions_fraction
    expected=[sign*n/(n+1)*(gradient/(2*.1))**(1/n)
        *(radius**(1+1/n)-(radius*s)**(1+1/n)) for s in positions]
    assert profile["status"]=="computed_laminar"
    assert profile["velocity_m_s"]==pytest.approx(expected,rel=3e-8,abs=1e-14)
    assert profile["axis_velocity_m_s"]==pytest.approx(expected[0],rel=3e-8)
    assert profile["velocity_m_s"][-1]==0
    assert profile["plug_velocity_m_s"] is None
    assert result["values"]["wall_shear_stress_Pa"]==pytest.approx(tau,rel=3e-8)


@pytest.mark.parametrize("tau", [6.,10.,25.])
@pytest.mark.parametrize("sign", [-1.,1.])
def test_bingham_profile_has_constant_core_continuous_annulus_and_no_slip(tau,sign):
    y=5.;a=y/tau;positions=(0.,a/2,a,(a+1)/2,.99,1.)
    q,_,_,profile=execute(flow=sign*manufactured_flow(1.,y,tau),positions=positions)
    radius=q.inner_diameter_m/2
    core=sign*radius*(tau-y)**2/(2*.1*tau)
    expected=[core if s<=a else sign*radius/(2*.1)*(tau*(1-s*s)-2*y*(1-s)) for s in positions]
    assert profile["velocity_m_s"]==pytest.approx(expected,rel=4e-8,abs=2e-14)
    assert profile["plug_radius_fraction"]==pytest.approx(a,rel=3e-8)
    assert profile["plug_velocity_m_s"]==pytest.approx(core,rel=4e-8)
    assert profile["axis_velocity_m_s"]==pytest.approx(core,rel=4e-8)
    assert profile["velocity_m_s"][-1]==0


@pytest.mark.parametrize("rheology,n,y", [("power_law",.6,0.),("bingham",1.,5.),("herschel_bulkley",1.4,5.)])
@pytest.mark.parametrize("sign", [-1.,1.])
def test_profile_integral_matches_the_existing_bulk_flow_without_another_flow_solver(rheology,n,y,sign):
    tau=10. if y else .1*4**n
    a=y/tau;nodes,weights=np.polynomial.legendre.leggauss(64)
    inner=.5*a*(nodes+1);outer=a+.5*(1-a)*(nodes+1)
    positions=tuple(float(x) for x in np.r_[inner,outer])
    q,_,_,profile=execute(rheology=rheology,n=n,yield_stress=y,
        flow=sign*manufactured_flow(n,y,tau),positions=positions)
    velocity=np.asarray(profile["velocity_m_s"])
    radial_integral=.5*a*np.dot(weights,inner*velocity[:64])+.5*(1-a)*np.dot(weights,outer*velocity[64:])
    predicted=2*math.pi*(q.inner_diameter_m/2)**2*radial_integral
    assert predicted==pytest.approx(q.volume_flow_m3_s,rel=2e-8,abs=2e-13)


def test_newtonian_laminar_limit_is_the_hagen_poiseuille_parabola():
    q,_,_,profile=execute(rheology="newtonian",n=1.,yield_stress=0.)
    mean=q.volume_flow_m3_s/(math.pi*(q.inner_diameter_m/2)**2)
    expected=[2*mean*(1-s)*(1+s) for s in q.radial_positions_fraction]
    assert profile["status"]=="computed_laminar"
    assert profile["velocity_m_s"]==pytest.approx(expected,rel=2e-12,abs=1e-15)


def test_turbulent_newtonian_owner_keeps_bulk_values_and_withholds_laminar_profile_only():
    q,_=case(rheology="newtonian",yield_stress=0.)
    properties=q.properties.model_copy(update={"consistency_Pa_s_n":.001})
    _,_,result,profile=execute(rheology="newtonian",yield_stress=0.,flow=.001,properties=properties)
    assert result["native_newtonian"]["values"]["reynolds"]>4000
    assert result["values"]["dissipated_power_W"]>0
    assert profile["status"]=="out_of_scope" and profile["velocity_m_s"] is None
    assert profile["axis_velocity_m_s"] is None


@pytest.mark.parametrize("y", [0.,5.])
def test_zero_flow_profile_is_zero_despite_nonunique_subyield_pressure(y):
    _,_,result,profile=execute(yield_stress=y,flow=0.)
    assert profile["velocity_m_s"]==[0.]*5
    assert profile["axis_velocity_m_s"]==0
    if y:
        assert profile["plug_velocity_m_s"]==0 and profile["plug_radius_fraction"]==1
        assert result["values"]["outlet_pressure_pa_abs"] is None


@pytest.mark.parametrize("y", [0.,5.])
def test_near_wall_velocity_retains_the_shear_gradient_without_cancellation(y):
    tau=10. if y else .1*4**.7;n=1. if y else .7
    s=1-2**-40
    _,_,_,profile=execute(rheology="bingham" if y else "power_law",n=n,yield_stress=y,
        flow=manufactured_flow(n,y,tau),positions=(s,1.))
    expected=(.025*(1-s))*((tau-y)/.1)**(1/n)
    assert profile["velocity_m_s"][0]==pytest.approx(expected,rel=3e-8)
    assert profile["velocity_m_s"][0]>0 and profile["velocity_m_s"][1]==0


def test_not_requested_is_distinct_from_axis_only_and_sample_order_is_preserved():
    _,_,_,absent=execute(positions=None)
    assert absent["status"]=="not_requested" and absent["axis_velocity_m_s"] is None
    _,_,_,axis=execute(positions=())
    assert axis["status"]=="computed_laminar" and axis["velocity_m_s"]==[]
    _,_,_,ordered=execute(positions=(1.,0.,1.))
    assert ordered["radial_positions_fraction"]==[1.,0.,1.]
    assert ordered["velocity_m_s"][0]==ordered["velocity_m_s"][2]==0


def test_profile_is_bound_into_request_context_and_actual_receipt_replay():
    q,c,r,_=execute()
    assert st.verify_slurry_transport_record(r,request=q,context=c)["valid"]
    changed=q.model_copy(update={"radial_positions_fraction":(.5,)})
    with pytest.raises(ValueError):st.evaluate_slurry_transport(changed,c)
    r["values"]["radial_velocity_profile"]["velocity_m_s"][0]+=.1
    r["record_digest"]=digest({k:v for k,v in r.items() if k!="record_digest"})
    assert not st.verify_slurry_transport_record(r,request=q,context=c)["valid"]


@pytest.mark.parametrize("positions", [(-.1,), (1.1,), (None,), (float("nan"),)])
def test_invalid_radial_coordinates_are_not_clipped_or_dropped(positions):
    q,c=case()
    with pytest.raises(ValueError):st.evaluate_slurry_transport(q.model_copy(update={"radial_positions_fraction":positions}),c)
