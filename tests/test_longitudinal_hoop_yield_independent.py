"""Independent upper yield-ellipse root and principal-stress invariant."""
import math
import pytest
from pipeline_integrity.contracts import RunContext,EvidenceRef
from pipeline_integrity.longitudinal_hoop_yield import LongitudinalHoopYieldRequest as Request,evaluate_longitudinal_hoop_yield as evaluate
C=RunContext(asset_id='pipe',scenario_id='external-pressure-axial',clock_id='static',origin='synthetic',evidence=(EvidenceRef(id='authored',citation='Authored directional strength coordinates',origin='synthetic',license_id='MIT'),))
def request(axial=0.,hoop_yield=300e6,axial_yield=300e6):
    return Request(asset_id=C.asset_id,clock_id=C.clock_id,location_id='shell',hoop_yield_strength_pa=hoop_yield,axial_yield_strength_pa=axial_yield,
        axial_stress_tension_positive_pa=axial*axial_yield,yield_basis_evidence_id='authored',normalized_hoop_axial_yield_ellipse_and_stress_sign_reviewed=True)

@pytest.mark.parametrize('hoop_yield,axial_yield',[(300e6,300e6),(240e6,360e6)])
@pytest.mark.parametrize('axial',[-1.,-.8,-.3,0.,.4,.8,1.])
def test_upper_ellipse_root_and_principal_stress_yield_surface(axial,hoop_yield,axial_yield):
    r=evaluate(request(axial,hoop_yield,axial_yield),C);assert r['status']=='computed'
    expected=math.sqrt(1-.75*axial**2)-.5*axial
    h=r['adjusted_compressive_hoop_yield_strength_pa']/hoop_yield
    assert h==pytest.approx(expected,abs=3e-12)
    # Independent three-principal-stress invariant, in reviewed normalized coordinates.
    assert math.sqrt(((-h-axial)**2+axial**2+h**2)/2)==pytest.approx(1.,abs=3e-12)
    assert r['plastic_collapse_curvature_buckling_or_acceptance_qualified'] is False

@pytest.mark.parametrize('axial',[-1.01,1.01])
def test_initial_axial_overload_is_withheld(axial):assert evaluate(request(axial),C)['status']=='out_of_scope'

def test_missing_strength_withheld():
    assert evaluate(request().model_copy(update={'hoop_yield_strength_pa':None}),C)['status']=='needs_data'

def test_od_nominal_pressure_reference_preserves_tensile_pressure_sign_separation():
    q=request(.4).model_copy(update={'outside_diameter_m':.3,'wall_thickness_m':.01});r=evaluate(q,C)
    pressure=r['optional_od_nominal_external_pressure_hoop_yield_reference_pa']
    assert pressure*.3/(2*.01)==pytest.approx(r['adjusted_compressive_hoop_yield_strength_pa'],rel=2e-14)

def test_unreviewed_strength_ellipse_withheld():
    assert evaluate(request().model_copy(update={'normalized_hoop_axial_yield_ellipse_and_stress_sign_reviewed':False}),C)['status']=='needs_data'
