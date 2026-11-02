"""Independent stresslet, concentration-ODE and published numerical references."""
import inspect,json,math
from pathlib import Path
import numpy as np
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.suspension_viscosity import (
    SuspensionViscosityRequest as Request,evaluate_suspension_viscosity as evaluate)

DATA=Path(__file__).parent/'fixtures/suspension_reference'

def request(**kwargs):
    values=dict(id='authored',temperature_K=300.,carrier_dynamic_viscosity_Pa_s=.001,
        dry_solid_volume_fraction=.1,maximum_packing_volume_fraction=.63,
        slurry_density_kg_m3=1200.,hard_sphere_aqueous_newtonian_low_inertia_regime_reviewed=True,
        carrier_property_and_packing_evidence_id='caller-reviewed-carrier-and-sphere-packing')
    values.update(kwargs);return Request(**values)

def test_actual_local_public_entrypoint_is_selected():
    assert Path(inspect.getfile(evaluate)).resolve()==Path(__file__).resolve().parents[1]/'src/pipeline_integrity/suspension_viscosity.py'

@pytest.mark.parametrize('phi',[.01,.1,.35,.5,.6])
def test_independent_incremental_stresslet_effective_medium_ODE(phi):
    q=request(dry_solid_volume_fraction=phi);result=evaluate(q)
    # Integrate the infinitesimal Einstein increment in effective occupied volume,
    # with the conversion between added actual and displaced effective volume.
    packing=q.maximum_packing_volume_fraction;crowding=1/packing-1
    def derivative(fraction,y):
        effective=fraction/(1-crowding*fraction)
        d_effective=1/(1-crowding*fraction)**2
        return [2.5*y[0]*d_effective/(1-effective)]
    reference=solve_ivp(derivative,(0.,phi),[1.],method='DOP853',rtol=2e-12,atol=2e-13)
    assert reference.success and result.status=='computed'
    assert result.relative_dynamic_viscosity==pytest.approx(reference.y[0,-1],rel=3e-11)
    # A five-point derivative of actual public outputs also checks the slope.
    h=min(1e-5,(packing-phi)/20)
    def logged(f):return math.log(evaluate(request(dry_solid_volume_fraction=f)).relative_dynamic_viscosity)
    slope=(-logged(phi+2*h)+8*logged(phi+h)-8*logged(phi-h)+logged(phi-2*h))/(12*h)
    assert result.logarithmic_viscosity_slope_per_volume_fraction==pytest.approx(slope,rel=3e-9)

def test_sphere_surface_traction_recovers_independent_Einstein_dilute_slope():
    # Exact no-slip sphere traction for ambient symmetric strain: t=5*mu*E*n.
    # Integrate its symmetric stresslet rather than repeating the bulk law.
    radius=.004;mu=.003;gamma=2.;exy=gamma/2
    def theta_integral(theta):
        def azimuth(phi):
            nx=math.sin(theta)*math.cos(phi);ny=math.sin(theta)*math.sin(phi)
            tx=5*mu*exy*ny;ty=5*mu*exy*nx
            return .5*radius*(nx*ty+ny*tx)*radius**2*math.sin(theta)
        return quad(azimuth,0,2*math.pi,epsabs=1e-20)[0]
    stresslet=quad(theta_integral,0,math.pi,epsabs=1e-20)[0]
    sphere_volume=4*math.pi*radius**3/3
    intrinsic=stresslet/(sphere_volume*mu*gamma)
    assert intrinsic==pytest.approx(2.5,rel=3e-13)
    slopes=[]
    for phi in (1e-3,1e-4,1e-5):
        r=evaluate(request(dry_solid_volume_fraction=phi)).relative_dynamic_viscosity
        slopes.append((r-1)/phi)
    assert all(abs(b-intrinsic)<abs(a-intrinsic) for a,b in zip(slopes,slopes[1:]))
    assert slopes[-1]==pytest.approx(intrinsic,rel=4e-5)

@pytest.mark.parametrize('row',np.loadtxt(DATA/'sp_reference.dat'))
def test_independent_published_LBM_suspension_reference_without_parameter_fit(row):
    phi,relative,*stress=row
    # Raw stress components are tractions exerted on fluid: their sign is opposite
    # the suspension extra shear stress. Keep kinetic and contact contributions.
    assert relative==pytest.approx(1-sum(stress[:3]),abs=2e-10)
    result=evaluate(request(dry_solid_volume_fraction=float(phi)))
    assert result.status=='computed'
    assert result.relative_dynamic_viscosity==pytest.approx(relative,rel=.05)
    assert not result.field_or_material_calibration_qualified

def test_reference_metadata_and_both_numerical_methods_are_preserved():
    import hashlib
    provenance=json.loads((DATA/'PROVENANCE.json').read_text())
    assert provenance['license']=='CC-BY-4.0'
    assert provenance['doi']=='10.7488/ds/2553'
    for row in provenance['files']:
        assert hashlib.sha256((DATA/row['file']).read_bytes()).hexdigest()==row['sha256']
    sp=np.loadtxt(DATA/'sp_reference.dat');bb=np.loadtxt(DATA/'bb_comparison.dat')
    assert np.array_equal(sp[:,0],bb[:,0])
    assert np.all(sp[:,1]>bb[:,1]) # Different physical stresslet algorithms, not replicates.

def test_same_temperature_carrier_and_suspension_density_coordinates_are_explicit():
    a=evaluate(request());b=evaluate(request(carrier_dynamic_viscosity_Pa_s=.004,slurry_density_kg_m3=2400.))
    assert b.relative_dynamic_viscosity==a.relative_dynamic_viscosity
    assert b.effective_dynamic_viscosity_Pa_s==pytest.approx(4*a.effective_dynamic_viscosity_Pa_s)
    assert b.effective_kinematic_viscosity_m2_s==pytest.approx(2*a.effective_kinematic_viscosity_m2_s)
    assert evaluate(request(slurry_density_kg_m3=None)).effective_kinematic_viscosity_m2_s is None

def test_zero_solids_and_monotone_packing_approach():
    zero=evaluate(request(dry_solid_volume_fraction=0.))
    assert zero.relative_dynamic_viscosity==1 and zero.effective_dynamic_viscosity_Pa_s==.001
    values=[evaluate(request(dry_solid_volume_fraction=.63*(1-gap))).relative_dynamic_viscosity for gap in (.1,.01,.001,.0001)]
    assert all(b>a for a,b in zip(values,values[1:]))
    # Geometric free-volume asymptote has exponent 5/2, independently of mu.
    assert math.log(values[-1]/values[-2])/math.log(10)==pytest.approx(2.5,rel=.003)

@pytest.mark.parametrize('field',['carrier_dynamic_viscosity_Pa_s','dry_solid_volume_fraction','maximum_packing_volume_fraction'])
def test_unknown_required_closure_data_is_not_replaced_by_a_default(field):
    result=evaluate(request(**{field:None}))
    assert result.status=='needs_data' and result.effective_dynamic_viscosity_Pa_s is None

def test_unsupported_regimes_and_packing_do_not_return_finite_fluid_predictions():
    assert evaluate(request(hard_sphere_aqueous_newtonian_low_inertia_regime_reviewed=False)).status=='needs_data'
    for phi in (.63,.64,1.):
        assert evaluate(request(dry_solid_volume_fraction=phi)).status=='out_of_scope'
    assert evaluate(request(maximum_packing_volume_fraction=.9)).status=='out_of_scope'
    with pytest.raises(ValueError):request(dry_solid_volume_fraction=-.01)

def test_nonfinite_input_and_overflow_are_not_qualified_outputs():
    with pytest.raises(ValueError):request(carrier_dynamic_viscosity_Pa_s=math.inf)
    assert evaluate(request(carrier_dynamic_viscosity_Pa_s=1e308,dry_solid_volume_fraction=.6)).status=='out_of_scope'
