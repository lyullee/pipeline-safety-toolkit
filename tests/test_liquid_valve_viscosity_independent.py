"""Independent unit-basis, correction-law and sizing-capacity references."""
import math
from decimal import Decimal,localcontext
import pytest
from fluids.control_valve import Reynolds_valve,Reynolds_factor
from pipeline_integrity.models.library_models import LiquidControlValve,liquid_control_valve

WATER=999.10329075702327

def reynolds(nu,q_si,diameter_m,fl,fd,kv):
    qh=q_si*3600.;dm=diameter_m*1000.
    # A dimensionless fourth-power relation avoids copying the native sqrt tree.
    numerator=(.0707*fd*qh/nu)**4
    return (numerator*(1/(kv*fl)**2+1/(.0016*dm**4)))**.25

def correction(fl,kv,diameter_m,re,full):
    with localcontext() as ctx:
        ctx.prec=55
        F=Decimal(str(fl));C=Decimal(str(kv));D=Decimal(str(diameter_m))*1000;Re=Decimal(str(re))
        density=C/D**2
        group=Decimal('.0016')/min(density,Decimal('.04'))**2 if full else 1+Decimal(140)*density**(Decimal(2)/3)
        creeping=Decimal('.026')/F*(group*Re).sqrt()
        if not full:creeping=min(creeping,Decimal(1))
        if Re<10:return float(creeping)
        logarithmic=1+Decimal('.33')*F.sqrt()/group.sqrt().sqrt()*(Re/10000).log10()
        return float(min(creeping,logarithmic))

def fixture(c0,re0,fl=.8,fd=.5):
    rho=900.;q=c0/3600*math.sqrt(WATER/rho)
    nu=reynolds(1.,q,.1,fl,fd,c0)/re0
    return LiquidControlValve(density_kg_m3=rho,vapour_pressure_pa_abs=1000.,critical_pressure_pa_abs=1e7,
        viscosity_pa_s=nu*rho,inlet_pressure_pa_abs=1e6,outlet_pressure_pa_abs=9e5,volume_flow_m3_s=q,
        inlet_diameter_m=.1,outlet_diameter_m=.1,valve_diameter_m=.1,
        pressure_recovery_factor=fl,valve_style_factor=fd)

def capacity(p,c):
    rev=reynolds(p.viscosity_pa_s/p.density_kg_m3,p.volume_flow_m3_s,p.inlet_diameter_m,
        p.pressure_recovery_factor,p.valve_style_factor,c)
    full=c/(p.valve_diameter_m*1000)**2<=.016*.865
    return c*correction(p.pressure_recovery_factor,c,p.valve_diameter_m,rev,full)

@pytest.mark.parametrize('kv',[20.,200.])
@pytest.mark.parametrize('target_re',[1000.,3000.,5000.])
def test_native_low_re_sizing_is_reached_and_brackets_independent_capacity_root(kv,target_re):
    p=fixture(kv,target_re);r=liquid_control_valve(p).values;c=r['kv'];d=r['diagnostics']
    assert d['laminar'] is True and d['choked'] is False
    assert d['FR'] is not None and 0<d['FR']<1
    assert d['Rev']==pytest.approx(reynolds(p.viscosity_pa_s/p.density_kg_m3,p.volume_flow_m3_s,.1,.8,.5,c),rel=3e-14)
    full=c/100**2<=.016*.865
    assert d['FR']==pytest.approx(correction(.8,c,.1,d['Rev'],full),rel=3e-14)
    # Independent monotone bisection of required effective capacity, not another
    # call to the sizing routine. Native output is a conservative trial size.
    lo,hi=kv,c
    assert capacity(p,lo)<=kv<=capacity(p,hi)
    for _ in range(150):
        mid=(lo+hi)/2
        if capacity(p,mid)<kv:lo=mid
        else:hi=mid
    root=(lo+hi)/2
    assert capacity(p,root)==pytest.approx(kv,rel=3e-14)
    assert root<=c<=1.3*root*(1+1e-14)
    assert c* d['FR']>kv # This native trial is not labelled the exact minimum root.

@pytest.mark.parametrize('full',[True,False])
@pytest.mark.parametrize('re',[1.,9.,50.,1000.,10000.])
def test_low_re_factor_matches_independent_high_precision_relation_and_limits(full,re):
    kv=200.;dm=.1;fl=.8
    native=Reynolds_factor(fl,kv,dm*1000.,re,full_trim=full)
    assert native==pytest.approx(correction(fl,kv,dm,re,full),rel=3e-14)
    if re==10000:assert native==pytest.approx(1.,abs=1e-14)
    if re<10:
        other=Reynolds_factor(fl,kv,dm*1000.,re/4,full_trim=full)
        assert native==pytest.approx(2*other,rel=3e-14)

def test_dynamic_to_kinematic_viscosity_and_diameter_hour_bases_are_explicit():
    p=fixture(20.,3000.)
    expected=reynolds(p.viscosity_pa_s/p.density_kg_m3,p.volume_flow_m3_s,.1,.8,.5,20.)
    native=Reynolds_valve(p.viscosity_pa_s/p.density_kg_m3,p.volume_flow_m3_s*3600.,100.,.8,.5,20.)
    assert native==pytest.approx(expected,rel=3e-14)
    # Helper function arguments use internal hourly/mm convention.
    wrong=Reynolds_valve(p.viscosity_pa_s,p.volume_flow_m3_s,.1,.8,.5,20.)
    assert abs(wrong/expected-1)>.1

def test_style_factor_enters_valve_reynolds_linearly_not_density_or_pipe_reynolds():
    nu=1e-5;kwargs=dict(nu=nu,Q=100.,D1=100.,FL=.8,C=30.)
    a=Reynolds_valve(Fd=.2,**kwargs);b=Reynolds_valve(Fd=.8,**kwargs)
    assert b==pytest.approx(4*a,rel=3e-15)

def test_turbulent_viscosity_limit_removes_the_correction_without_trial_margin():
    p=fixture(20.,1e7);r=liquid_control_valve(p).values
    assert r['diagnostics']['laminar'] is False
    assert r['diagnostics']['FR'] is None
    assert r['kv']==pytest.approx(20.,rel=3e-14)

def test_kv_result_is_not_an_unlabelled_imperial_cv_input_or_output():
    p=fixture(20.,5000.)
    q_gpm=p.volume_flow_m3_s/.0000630901964
    drop_psi=(p.inlet_pressure_pa_abs-p.outlet_pressure_pa_abs)/6894.757293168
    imperial_required=q_gpm*math.sqrt((p.density_kg_m3/WATER)/drop_psi)
    # Derive conversion from volume/time and pressure units, not a valve fit.
    cv_per_kv=(1/3600/.0000630901964)*math.sqrt(6894.757293168/100000.)
    r=liquid_control_valve(p).values
    assert imperial_required/20.==pytest.approx(cv_per_kv,rel=2e-14)
    assert r['kv']*cv_per_kv==pytest.approx(1.3*imperial_required,rel=2e-14)
    assert abs(r['kv']/(1.3*imperial_required)-1)>.1
