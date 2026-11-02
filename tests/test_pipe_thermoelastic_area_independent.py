"""Independent three-variable Hooke compatibility and first-order geometry."""
import math
from decimal import Decimal,localcontext
import numpy as np
import pytest
from pipeline_integrity.pipe_pressure_compliance import PressureDiameterRequest
from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot
from pipeline_integrity.pipe_thermoelastic_area import ThermoelasticAreaRequest,evaluate_thermoelastic_area as evaluate
from pipeline_integrity.pipe_thermoelastic_area import ClosedEndThermoelasticRequest,evaluate_closed_end_thermoelastic_geometry as closed_end


def request(condition='zero_axial_stress',dp=1e6,temperature=320.,basis='free_linear_engineering',nu=.3):
    factor=2 if basis=='free_area_engineering' else 1
    curve=ExpansionCurve(id='authored',material_id='synthetic',evidence_id='authored',
        knots=(ExpansionKnot(temperature_K=250.,tangent_expansion_per_K=1e-5*factor),
            ExpansionKnot(temperature_K=400.,tangent_expansion_per_K=1e-5*factor)),
        tangent_small_engineering_strain_basis_reviewed=True)
    pressure=PressureDiameterRequest(reference_internal_diameter_m=.5,wall_thickness_m=.01,young_modulus_pa=2e11,
        differential_pressure_increment_pa=dp,axial_condition=condition,poisson_ratio=nu,
        maximum_absolute_hoop_strain=.005,thin_wall_linear_elastic_and_operating_stability_reviewed=True)
    return ThermoelasticAreaRequest(pressure=pressure,reference_temperature_K=300.,temperature_K=temperature,
        thermal_expansion_curve=curve,coefficient_basis=basis,maximum_absolute_total_hoop_strain=.005,
        uniform_temperature_isotropic_linear_thermoelasticity_reviewed=True)


@pytest.mark.parametrize('condition',['zero_axial_stress','zero_axial_strain'])
@pytest.mark.parametrize('dp,temperature',[(0.,300.),(1e6,320.),(-1e6,280.),(1e6,280.),(-1e6,320.)])
@pytest.mark.parametrize('nu',[.3,-.1])
def test_independent_hooke_system_compatibility_signed_loads(condition,dp,temperature,nu):
    q=request(condition,dp,temperature,nu=nu);r=evaluate(q);assert r.status=='computed'
    E=q.pressure.young_modulus_pa
    hoop_stress=dp*q.pressure.reference_internal_diameter_m/(2*q.pressure.wall_thickness_m)
    eigenstrain=1e-5*(temperature-300)
    # Unknowns are hoop strain, axial strain and axial stress/E. These two
    # independent Hooke equations plus the selected axial constraint close them.
    matrix=np.array([[1,0,nu],[0,1,-1],[0,0,1] if condition=='zero_axial_stress' else [0,1,0]],dtype=float)
    rhs=np.array([hoop_stress/E+eigenstrain,-nu*hoop_stress/E+eigenstrain,0.])
    eps_hoop,eps_axial,normalized_axial_stress=np.linalg.solve(matrix,rhs)
    assert r.total_hoop_strain==pytest.approx(eps_hoop,abs=2e-19)
    assert r.linear_total_area_fraction==pytest.approx(2*eps_hoop,abs=4e-19)
    assert r.linearized_area_m2==pytest.approx(math.pi*.5**2/4*(1+2*eps_hoop))
    if condition=='zero_axial_strain':assert eps_axial==0
    else:assert normalized_axial_stress==0


@pytest.mark.parametrize('condition',['zero_axial_stress','zero_axial_strain'])
def test_piecewise_coefficient_integral_and_explicit_area_basis(condition):
    q=request(condition,temperature=350.)
    # Exact integral of a caller's linear coefficient from300 to350 is0.000625.
    curve=q.thermal_expansion_curve.model_copy(update={'knots':(
        ExpansionKnot(temperature_K=250.,tangent_expansion_per_K=.5e-5),
        ExpansionKnot(temperature_K=400.,tangent_expansion_per_K=2e-5))})
    q=q.model_copy(update={'thermal_expansion_curve':curve})
    r=evaluate(q);assert r.free_linear_thermal_strain==pytest.approx(.000625)
    area_curve=curve.model_copy(update={'knots':tuple(k.model_copy(update={
        'tangent_expansion_per_K':2*k.tangent_expansion_per_K}) for k in curve.knots)})
    area_result=evaluate(q.model_copy(update={'thermal_expansion_curve':area_curve,'coefficient_basis':'free_area_engineering'}))
    assert area_result.total_hoop_strain==r.total_hoop_strain
    assert area_result.linearized_area_m2==r.linearized_area_m2
    cold=evaluate(q.model_copy(update={'reference_temperature_K':350.,'temperature_K':300.}))
    assert cold.free_linear_thermal_strain==pytest.approx(-.000625)


def test_first_order_area_has_only_quadratic_geometric_difference():
    errors=[]
    for scale in (1.,.5):
        q=request(dp=1e6*scale,temperature=300+20*scale);r=evaluate(q)
        # Decimal isolates the quadratic truncation error from cancellation of
        # two ~0.2m² floats whose difference is only a few nanometres squared.
        with localcontext() as context:
            context.prec=50
            area=Decimal(str(r.reference_area_m2));strain=Decimal(str(r.total_hoop_strain))
            exact=area*(1+strain)**2;linear=area*(1+2*strain)
            error=float(exact-linear)
            assert r.linearized_area_m2==pytest.approx(float(linear),abs=4e-17)
        assert error==pytest.approx(r.reference_area_m2*r.total_hoop_strain**2,rel=2e-9)
        assert not r.finite_strain_geometry_claimed
        errors.append(error)
    assert errors[0]/errors[1]==pytest.approx(4,rel=2e-9)


@pytest.mark.parametrize('field,value',[('thermal_expansion_curve',None),('maximum_absolute_total_hoop_strain',None),
    ('uniform_temperature_isotropic_linear_thermoelasticity_reviewed',False)])
def test_unknown_inputs_are_withheld(field,value):
    assert evaluate(request().model_copy(update={field:value})).status=='needs_data'


def test_unknown_curve_basis_and_volumetric_conversion_not_inferred():
    q=request()
    assert evaluate(q.model_copy(update={'coefficient_basis':'free_volumetric_engineering'})).status=='out_of_scope'
    assert evaluate(q.model_copy(update={'thermal_expansion_curve':q.thermal_expansion_curve.model_copy(
        update={'tangent_small_engineering_strain_basis_reviewed':False})})).status=='needs_data'


def test_mechanical_and_total_support_checked_separately():
    q=request(dp=1e6,temperature=287.5)
    # Mechanical and thermal responses cancel, but that cannot extend a
    # separately reviewed mechanical support range.
    pressure=q.pressure.model_copy(update={'maximum_absolute_hoop_strain':1e-5})
    assert evaluate(q.model_copy(update={'pressure':pressure})).status=='out_of_scope'
    assert evaluate(request(dp=0).model_copy(update={'maximum_absolute_total_hoop_strain':1e-5})).status=='out_of_scope'


def test_temperature_range_pressure_review_and_fixed_axial_poisson_requirements():
    assert evaluate(request(temperature=401.)).status=='out_of_scope'
    q=request();assert evaluate(q.model_copy(update={'pressure':q.pressure.model_copy(update={
        'thin_wall_linear_elastic_and_operating_stability_reviewed':False})})).status=='needs_data'
    assert evaluate(request(nu=None)).status=='computed'
    assert evaluate(request('zero_axial_strain',nu=None)).status=='needs_data'


@pytest.mark.parametrize('dp,temperature',[(1e6,320.),(-1e6,280.),(0.,320.),(0.,300.)])
@pytest.mark.parametrize('nu',[.3,-.1])
def test_closed_end_independent_hooke_area_versus_volume_observables(dp,temperature,nu):
    q=request(dp=dp,temperature=temperature,nu=nu)
    r=closed_end(ClosedEndThermoelasticRequest(unconstrained_base=q,
        maximum_absolute_mechanical_axial_strain=.005,maximum_absolute_total_axial_strain=.005,
        closed_end_pressure_force_and_no_external_axial_restraint_reviewed=True))
    assert r.status=='computed'
    hoop_stress=dp*.5/(2*.01);axial_stress=hoop_stress/2
    eigenstrain=1e-5*(temperature-300)
    hoop=(hoop_stress-nu*axial_stress)/2e11+eigenstrain
    axial=(axial_stress-nu*hoop_stress)/2e11+eigenstrain
    assert r.total_hoop_strain==pytest.approx(hoop,abs=2e-19)
    assert r.total_axial_strain==pytest.approx(axial,abs=2e-19)
    assert r.linear_area_fraction_increment==pytest.approx(2*hoop,abs=4e-19)
    assert r.linear_volume_fraction_increment==pytest.approx(2*hoop+axial,abs=5e-19)
    assert r.linear_pressure_volume_fraction==pytest.approx((1.25-nu)*dp*.5/(2e11*.01),abs=5e-19)
    assert r.linear_thermal_volume_fraction==pytest.approx(3*eigenstrain,abs=5e-19)


def test_closed_end_support_unknown_and_wrong_owner_constraint():
    q=ClosedEndThermoelasticRequest(unconstrained_base=request(),maximum_absolute_mechanical_axial_strain=.005,
        maximum_absolute_total_axial_strain=.005,closed_end_pressure_force_and_no_external_axial_restraint_reviewed=True)
    assert closed_end(q.model_copy(update={'maximum_absolute_total_axial_strain':None})).status=='needs_data'
    assert closed_end(q.model_copy(update={'closed_end_pressure_force_and_no_external_axial_restraint_reviewed':False})).status=='needs_data'
    assert closed_end(q.model_copy(update={'maximum_absolute_total_axial_strain':1e-6})).status=='out_of_scope'
    assert closed_end(q.model_copy(update={'unconstrained_base':request('zero_axial_strain')})).status=='out_of_scope'
