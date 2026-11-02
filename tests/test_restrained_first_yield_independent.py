"""Independent plane-strain displacement compatibility and invariant references."""
import math,runpy
import pytest
from pipeline_integrity.restrained_first_yield import RestrainedFirstYieldRequest as Request,evaluate_restrained_first_yield as evaluate
C=runpy.run_path('examples/cyclic_fatigue.py')['make_cases']()[1]

def request(nu=.3,ratio=1.2,strength=350e6):
    return Request(asset_id=C.asset_id,location_id='restrained',clock_id=C.clock_id,inner_radius_m=.5,
        outer_radius_m=.5*ratio,young_modulus_pa=200e9,poisson_ratio=nu,supplied_uniaxial_yield_strength_pa=strength,
        material_evidence_id='authored',pressure_only_zero_axial_strain_isotropic_j2_basis_reviewed=True)

@pytest.mark.parametrize('nu',[-.2,.3,.45])
@pytest.mark.parametrize('ratio',[1.0001,1.01,1.2,2.])
def test_displacement_compatibility_boundary_tractions_and_first_yield(nu,ratio):
    q=request(nu,ratio);r=evaluate(q,C);assert r['status']=='computed'
    a=q.inner_radius_m;b=q.outer_radius_m;p=r['internal_pressure_relative_zero_exterior_pa'];E=q.young_modulus_pa
    mu=E/(2*(1+nu));lam=E*nu/((1+nu)*(1-2*nu))
    # Solve radial traction equations for u=A*r+B/r, epsilon_z=0.
    B=p/(2*mu*(1/a**2-1/b**2));A=mu*B/((lam+mu)*b*b)
    values=[]
    for x in (a,(a+b)/2,b):
        er=A-B/x**2;eh=A+B/x**2;sr=2*mu*er+lam*(er+eh);sh=2*mu*eh+lam*(er+eh);sz=lam*(er+eh)
        j2=(sr*sr+sh*sh+sz*sz-sr*sh-sh*sz-sz*sr)**.5;values.append(j2)
        assert (sz-nu*(sr+sh))/E==pytest.approx(0.,abs=1e-15)
        if x==a:
            assert (sr,sh,sz)==pytest.approx((r['bore_radial_stress_pa'],r['bore_hoop_stress_pa'],r['bore_axial_stress_pa']),rel=2e-12,abs=1e-5)
            assert sr==pytest.approx(-p,rel=2e-12)
        if x==b:assert sr==pytest.approx(0.,abs=1e-5)
    assert values[0]==pytest.approx(q.supplied_uniaxial_yield_strength_pa,rel=2e-12)
    assert values[0]>values[1]>values[2]

def test_thin_plane_stress_limit_not_free_closed_cap():
    errors=[];nu=.3;factor=1/math.sqrt(1-nu+nu*nu)
    # Retain the original last annulus, then extend the asymptotic sequence.
    # Finite-thickness stresses have their own exact reference above.
    for ratio in (1.01,1.001,1.0001,1.00005):
        q=request(nu,ratio);r=evaluate(q,C);errors.append(abs(r['bore_hoop_stress_pa']/q.supplied_uniaxial_yield_strength_pa-factor))
    assert all(b<a for a,b in zip(errors,errors[1:])) and errors[-1]<1e-4
    assert abs(factor-2/math.sqrt(3))>.02

def test_strength_scaling_and_stiffness_independence():
    q=request();a=evaluate(q,C);b=evaluate(q.model_copy(update=dict(supplied_uniaxial_yield_strength_pa=700e6,young_modulus_pa=100e9)),C)
    assert b['internal_pressure_relative_zero_exterior_pa']==pytest.approx(2*a['internal_pressure_relative_zero_exterior_pa'],rel=1e-12)

def test_unknown_or_unreviewed_basis_withheld():
    q=request()
    for update in (dict(supplied_uniaxial_yield_strength_pa=None),dict(pressure_only_zero_axial_strain_isotropic_j2_basis_reviewed=False)):
        assert evaluate(q.model_copy(update=update),C)['status']=='needs_data'
