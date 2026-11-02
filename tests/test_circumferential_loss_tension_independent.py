"""Independent uniform-ligament limit and explicitly bounded correlation checks."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.circumferential_loss_tension import CircumferentialLossTensionRequest as Request,evaluate_circumferential_loss_tension as evaluate

def req(depth=.003,width=.02):
    return Request(nominal_wall_thickness_m=.01,loss_depth_m=depth,circumferential_loss_width_m=width,
        supplied_reference_failure_stress_pa=400e6,geometry_material_and_tension_only_basis_id='authored-ligament-limit',
        isolated_blunt_loss_away_from_discontinuities_and_tension_only_reviewed=True)

@pytest.mark.parametrize('fraction',[.1,.4,.8])
def test_wide_loss_converges_to_uniform_ligament_force_equilibrium(fraction):
    # Resolve a uniform remaining annulus independently; reference nominal area
    # uses that same outer radius. Thin limit removes finite-wall area correction.
    errors=[]
    for ratio in (1e-3,1e-4,1e-5):
        radius=1.;t=radius*ratio;d=t*fraction
        q=req().model_copy(update={'nominal_wall_thickness_m':t,'loss_depth_m':d,'circumferential_loss_width_m':1.})
        r=evaluate(q)
        nominal=quad(lambda rad:2*math.pi*rad,radius-t,radius)[0]
        remaining=quad(lambda rad:2*math.pi*rad,radius-t+d,radius)[0]
        exact=q.supplied_reference_failure_stress_pa*remaining/nominal
        errors.append(abs(r['nominal_axial_failure_stress_coordinate_pa']/exact-1))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<2e-5

@pytest.mark.parametrize('depth',[0.,.002,.009])
def test_bounds_and_zero_width_endpoint(depth):
    values=[evaluate(req(depth,w))['remaining_nominal_stress_fraction'] for w in (0.,.001,.01,1.)]
    assert values[0]==pytest.approx(1.)
    assert all(1-depth/.01<=v<=1 for v in values)
    assert all(a>=b for a,b in zip(values,values[1:]))

def test_length_and_stress_units_scale_independently():
    q=req();r=evaluate(q)
    scaled=q.model_copy(update={'nominal_wall_thickness_m':.02,'loss_depth_m':.006,'circumferential_loss_width_m':.04,'supplied_reference_failure_stress_pa':800e6})
    s=evaluate(scaled)
    assert s['remaining_nominal_stress_fraction']==pytest.approx(r['remaining_nominal_stress_fraction'])
    assert s['nominal_axial_failure_stress_coordinate_pa']==pytest.approx(2*r['nominal_axial_failure_stress_coordinate_pa'])
    assert s['correlation_calibration_or_field_acceptance_qualified'] is False

def test_unknown_basis_and_strength_are_not_defaulted():
    assert evaluate(req().model_copy(update={'supplied_reference_failure_stress_pa':None}))['status']=='needs_data'
    assert evaluate(req().model_copy(update={'isolated_blunt_loss_away_from_discontinuities_and_tension_only_reviewed':False}))['status']=='needs_data'

def test_through_wall_loss_is_outside_positive_ligament_coordinate():
    with pytest.raises(ValueError,match='positive nominal ligament'):evaluate(req(.01))
