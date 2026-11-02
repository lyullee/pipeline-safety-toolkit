"""Independent distributed-flow limiting references and signed energy checks."""
import math
import pytest
from pipeline_integrity.inclined_two_phase_segment import (
    InclinedTwoPhaseSegmentRequest as Request, evaluate_inclined_two_phase_segment as solve,
)


def make(liquid_fraction=.9,angle=30.,froude=500.):
    rl=900.;rg=5.;d=.05;g=9.80665;vm=math.sqrt(froude*g*d)
    bulk=liquid_fraction*rl+(1-liquid_fraction)*rg
    mass=bulk*math.pi*d*d/4*vm
    quality=(1-liquid_fraction)*rg/bulk
    return Request(total_mass_flow_kg_s=mass,gas_mass_quality=quality,liquid_density_kg_m3=rl,
        gas_density_kg_m3=rg,liquid_dynamic_viscosity_Pa_s=.003,gas_dynamic_viscosity_Pa_s=1e-5,
        surface_tension_N_m=.05,internal_diameter_m=d,length_m=2.,inclination_deg=angle,
        local_absolute_pressure_Pa=1e8,phase_property_state_basis="local synthetic oil-gas state A",
        local_constant_state_smooth_pipe_and_holdup_model_reviewed=True)


def smooth_pipe_darcy_from_implicit_relation(re):
    # Independent bracket solve of the smooth Colebrook wall-friction equation.
    low,high=.0001,.2
    for _ in range(110):
        trial=(low+high)/2
        residual=1/math.sqrt(trial)+2*math.log10(2.51/(re*math.sqrt(trial)))
        if residual>0:low=trial
        else:high=trial
    return (low+high)/2


def distributed_no_slip_floor_reference(q,liquid_fraction):
    area=math.pi*q.internal_diameter_m**2/4
    vm=q.total_mass_flow_kg_s*((1-q.gas_mass_quality)/q.liquid_density_kg_m3+
        q.gas_mass_quality/q.gas_density_kg_m3)/area
    bulk=liquid_fraction*q.liquid_density_kg_m3+(1-liquid_fraction)*q.gas_density_kg_m3
    viscosity=liquid_fraction*q.liquid_dynamic_viscosity_Pa_s+(1-liquid_fraction)*q.gas_dynamic_viscosity_Pa_s
    re=bulk*q.internal_diameter_m*vm/viscosity
    darcy=smooth_pipe_darcy_from_implicit_relation(re)
    # For this preselected distributed/no-slip-floor branch the algebraic
    # wall-work multiplier simplifies to this linear reciprocal expression.
    multiplier=2.2/liquid_fraction-1.2
    friction=multiplier*darcy*q.length_m/q.internal_diameter_m*bulk*vm**2/2
    gravity=bulk*q.gravity_m_s2*q.length_m*math.sin(math.radians(q.inclination_deg))
    return friction+gravity,friction,gravity


@pytest.mark.parametrize("fraction",[.85,.9,.95])
@pytest.mark.parametrize("angle",[0.,15.,45.,90.])
def test_independent_wallwork_plus_exact_elevation_in_distributed_limit(fraction,angle):
    q=make(fraction,angle);r=solve(q)
    expected,friction,gravity=distributed_no_slip_floor_reference(q,fraction)
    assert r.status=="computed"
    assert r.signed_pressure_drop_Pa==pytest.approx(expected,rel=3e-11)
    assert r.estimated_outlet_absolute_pressure_Pa==pytest.approx(q.local_absolute_pressure_Pa-expected)
    assert r.acceleration_included is False


@pytest.mark.parametrize("scale",[.01,3.,20.])
def test_local_constant_state_length_extensivity(scale):
    q=make();r=solve(q)
    scaled=solve(q.model_copy(update={"length_m":2*scale}))
    assert scaled.signed_pressure_drop_Pa==pytest.approx(r.signed_pressure_drop_Pa*scale)


def test_gravity_work_changes_without_extra_friction_in_distributed_floor_branch():
    q=make();r=solve(q);scaled=solve(q.model_copy(update={"gravity_m_s2":q.gravity_m_s2/2}))
    _,_,gravity=distributed_no_slip_floor_reference(q,.9)
    assert scaled.signed_pressure_drop_Pa==pytest.approx(r.signed_pressure_drop_Pa-gravity/2)


def test_absolute_pressure_does_not_create_acceleration_work():
    q=make();a=solve(q);b=solve(q.model_copy(update={"local_absolute_pressure_Pa":2e8}))
    assert a.signed_pressure_drop_Pa==b.signed_pressure_drop_Pa


def test_downhill_pressure_recovery_is_not_clamped():
    q=make(.99,-90.,10.)
    r=solve(q)
    assert r.status=="computed" and r.signed_pressure_drop_Pa<0.
    assert r.estimated_outlet_absolute_pressure_Pa>q.local_absolute_pressure_Pa


def test_zero_length():
    r=solve(make().model_copy(update={"length_m":0.}))
    assert r.signed_pressure_drop_Pa==0.


def test_unreviewed_applicability_withheld():
    r=solve(make().model_copy(update={"local_constant_state_smooth_pipe_and_holdup_model_reviewed":False}))
    assert r.status=="needs_data" and r.signed_pressure_drop_Pa is None


def test_excess_pressure_variation_requires_segmentation():
    q=make().model_copy(update={"local_absolute_pressure_Pa":1.,"length_m":200.})
    r=solve(q)
    assert r.status=="needs_data" and r.signed_pressure_drop_Pa is None


@pytest.mark.parametrize("field,value",[("gas_mass_quality",0.),("gas_mass_quality",1.),
    ("surface_tension_N_m",0.),("inclination_deg",91.),("inclination_deg",-91.),
    ("local_absolute_pressure_Pa",0.),("gravity_m_s2",math.nan),
    ("total_mass_flow_kg_s",0.),("internal_diameter_m",0.),("length_m",-1.)])
def test_invalid_domain_contracts(field,value):
    with pytest.raises(ValueError):solve(make().model_copy(update={field:value}))
