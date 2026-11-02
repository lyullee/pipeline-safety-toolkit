"""Independent pressure-work and separate-phase friction oracles."""
import math
import pytest
from pipeline_integrity.horizontal_two_phase_friction import (
    HorizontalTwoPhaseFrictionRequest as Request, evaluate_horizontal_two_phase_friction as solve,
)


def make(liquid_re=800.,gas_re=12000.,transition=2000.):
    m=.8;x=.25;d=.08
    return Request(total_mass_flow_kg_s=m,gas_mass_quality=x,liquid_density_kg_m3=850.,
        gas_density_kg_m3=8.,liquid_dynamic_viscosity_Pa_s=4*m*(1-x)/(math.pi*d*liquid_re),
        gas_dynamic_viscosity_Pa_s=4*m*x/(math.pi*d*gas_re),internal_diameter_m=d,length_m=17.,
        transition_reynolds=transition,phase_property_state_basis="synthetic isothermal local phase state A",
        common_state_and_horizontal_smooth_pipe_reviewed=True)


def phase_loss(mass,rho,mu,diam,length,critical):
    if mass==0:return 0.
    volume=mass/rho
    velocity=volume/(math.pi*diam**2/4)
    re=4*mass/(math.pi*diam*mu)
    if re<critical:
        # Exact Hagen-Poiseuille laminar pipe solution.
        return 128*mu*length*volume/(math.pi*diam**4)
    # Fanning wall shear fit for this named smooth-pipe correlation.
    wall_shear=(.046/re**.2)*rho*velocity**2/2
    return 4*length*wall_shear/diam


@pytest.mark.parametrize("rl,rg,c,labels",[
    (300.,700.,5.,("laminar","laminar")),
    (700.,20000.,12.,("laminar","turbulent")),
    (20000.,700.,10.,("turbulent","laminar")),
    (20000.,30000.,20.,("turbulent","turbulent")),
    (1999.,1999.,5.,("laminar","laminar")),
    (2001.,1999.,10.,("turbulent","laminar")),
    (1999.,2001.,12.,("laminar","turbulent")),
    (2001.,2001.,20.,("turbulent","turbulent")),
])
def test_four_regimes_independent_additive_interaction_oracle(rl,rg,c,labels):
    q=make(rl,rg);r=solve(q)
    dl=phase_loss(.6,850.,q.liquid_dynamic_viscosity_Pa_s,.08,17.,2000.)
    dg=phase_loss(.2,8.,q.gas_dynamic_viscosity_Pa_s,.08,17.,2000.)
    expected=dl+dg+c*math.sqrt(dl*dg)
    assert r.friction_pressure_drop_Pa==pytest.approx(expected,rel=2e-13)
    assert r.liquid_reynolds==pytest.approx(rl)
    assert r.gas_reynolds==pytest.approx(rg)
    assert (r.liquid_regime,r.gas_regime)==labels
    assert not r.gravity_and_acceleration_included


@pytest.mark.parametrize("quality",[0.,1.])
@pytest.mark.parametrize("re",[800.,15000.])
def test_single_phase_endpoint_matches_physical_pipe_friction(quality,re):
    q=make(re,re);q=q.model_copy(update=dict(gas_mass_quality=quality))
    rho=q.liquid_density_kg_m3 if quality==0 else q.gas_density_kg_m3
    mu=q.liquid_dynamic_viscosity_Pa_s if quality==0 else q.gas_dynamic_viscosity_Pa_s
    expected=phase_loss(.8,rho,mu,.08,17.,2000.)
    r=solve(q)
    assert r.friction_pressure_drop_Pa==pytest.approx(expected,rel=2e-13)
    assert r.gas_regime=="absent" if quality==0 else r.liquid_regime=="absent"


@pytest.mark.parametrize("rl,rg,power",[(100.,200.,1.),(20000.,30000.,1.8)])
def test_mass_rate_scaling_within_fixed_regimes(rl,rg,power):
    q=make(rl,rg);r=solve(q)
    doubled=solve(q.model_copy(update={"total_mass_flow_kg_s":1.6}))
    assert doubled.friction_pressure_drop_Pa==pytest.approx(r.friction_pressure_drop_Pa*2**power)


@pytest.mark.parametrize("scale",[.01,2.,100.])
def test_length_scaling(scale):
    q=make();r=solve(q)
    scaled=solve(q.model_copy(update={"length_m":17*scale}))
    assert scaled.friction_pressure_drop_Pa==pytest.approx(r.friction_pressure_drop_Pa*scale)


@pytest.mark.parametrize("rl,rg,power",[(100.,200.,4.),(20000.,30000.,4.8)])
def test_diameter_scaling_in_same_regime(rl,rg,power):
    q=make(rl,rg);r=solve(q)
    scaled=solve(q.model_copy(update={"internal_diameter_m":.16}))
    assert scaled.friction_pressure_drop_Pa==pytest.approx(r.friction_pressure_drop_Pa/2**power)


def test_explicit_transition_changes_regime_and_reference():
    q=make(1400.,1500.,1000.);r=solve(q)
    dl=phase_loss(.6,850.,q.liquid_dynamic_viscosity_Pa_s,.08,17.,1000.)
    dg=phase_loss(.2,8.,q.gas_dynamic_viscosity_Pa_s,.08,17.,1000.)
    assert r.friction_pressure_drop_Pa==pytest.approx(dl+dg+20*math.sqrt(dl*dg))
    assert r.liquid_regime==r.gas_regime=="turbulent"


@pytest.mark.parametrize("field",["total_mass_flow_kg_s","length_m"])
def test_zero_throughput_or_length(field):
    r=solve(make().model_copy(update={field:0.}))
    assert r.friction_pressure_drop_Pa==0.


def test_unreviewed_applicability_is_unknown():
    r=solve(make().model_copy(update={"common_state_and_horizontal_smooth_pipe_reviewed":False}))
    assert r.status=="needs_data" and r.friction_pressure_drop_Pa is None


@pytest.mark.parametrize("field,value",[("gas_mass_quality",-0.1),("gas_mass_quality",1.01),
    ("liquid_density_kg_m3",0.),("gas_density_kg_m3",math.inf),
    ("liquid_dynamic_viscosity_Pa_s",0.),("gas_dynamic_viscosity_Pa_s",math.nan),
    ("internal_diameter_m",0.),("length_m",-1.),("transition_reynolds",0.)])
def test_invalid_and_nonfinite_contracts(field,value):
    with pytest.raises(ValueError):solve(make().model_copy(update={field:value}))


@pytest.mark.parametrize("field,value",[("internal_diameter_m",1e-300),("total_mass_flow_kg_s",1e308)])
def test_finite_inputs_beyond_supported_arithmetic_are_rejected(field,value):
    with pytest.raises(ValueError):solve(make().model_copy(update={field:value}))
