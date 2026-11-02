"""Finite effective-line references and explicit approximate geometry guards."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.earth_electrode import SlenderEarthElectrode,EarthElectrodeRequest
from pipeline_integrity.groundbed_effective_envelope import GroundbedEnvelopeRequest,evaluate_groundbed_effective_envelope


def request(axis='vertical',count=1):
    outer=[];inner=[];assignment={}
    for i in range(count):
        start=(i*6.,0.,.3)
        outer.append(SlenderEarthElectrode(id=f'envelope{i}',start_xyz_depth_m=start,axis=axis,length_m=3.,radius_m=.08))
        core_start=(start[0],0.,.8) if axis=='vertical' else (start[0]+.5,0.,.3)
        inner.append(SlenderEarthElectrode(id=f'core{i}',start_xyz_depth_m=core_start,axis=axis,length_m=1.,radius_m=.02))
        assignment[f'core{i}']=f'envelope{i}'
    model=EarthElectrodeRequest(uniform_resistivity_ohm_m=70.,electrodes=tuple(outer),mode='rod_average_common_bus',total_signed_current_A=2.,
        coordinate_frame_id='given-earth-frame',uniform_leakage_slender_line_and_homogeneous_soil_reviewed=True,current_return_at_infinity_reviewed=True)
    return GroundbedEnvelopeRequest(effective_envelope_ground=model,metal_core_geometries=tuple(inner),core_to_envelope_id=assignment,
        effective_geometry_basis_id='caller-selected-approximate-envelope',uniform_leakage_effective_envelope_approximation_selected=True)


def finite_uniform_line_reference(a,b,same):
    da=np.array((0.,0.,1.) if a.axis=='vertical' else (1.,0.,0.));db=np.array((0.,0.,1.) if b.axis=='vertical' else (1.,0.,0.))
    start_a=np.array(a.start_xyz_depth_m);start_b=np.array(b.start_xyz_depth_m)
    def integrand(s,t):
        point=start_a+s*da;source=start_b+t*db;image=source.copy();image[2]*=-1
        soft=a.radius_m**2 if same else 0.
        return 1/math.sqrt(float((point-source)@(point-source))+soft)+1/math.sqrt(float((point-image)@(point-image))+soft)
    value=quad(lambda s:quad(lambda t:integrand(s,t),0,b.length_m,epsabs=1e-10,epsrel=1e-10)[0],0,a.length_m,epsabs=1e-10,epsrel=1e-10)[0]
    return 70./(4*math.pi*a.length_m*b.length_m)*value


@pytest.mark.parametrize('axis',['vertical','horizontal'])
@pytest.mark.parametrize('count',[1,2])
def test_independent_finite_envelope_self_mutual_current_and_power_reference(axis,count):
    q=request(axis,count);result=evaluate_groundbed_effective_envelope(q)
    assert result.status=='computed';model=result.approximate_ground
    rods=q.effective_envelope_ground.electrodes
    R=np.array([[finite_uniform_line_reference(a,b,i==j) for j,b in enumerate(rods)] for i,a in enumerate(rods)])
    assert model.resistance_matrix_ohm==pytest.approx(R,rel=3e-10)
    if count==1:bus=R[0,0]
    else:bus=(R[0,0]+R[0,1])/2
    assert model.common_bus_resistance_ohm==pytest.approx(bus,rel=3e-10)
    assert sum(model.signed_currents_A)==pytest.approx(2.,abs=1e-12)
    assert model.model_ohmic_power_W==pytest.approx(4*bus,rel=3e-10)
    assert not result.backfill_conductivity_or_exact_metal_equipotential_solved
    assert not model.exact_finite_metal_surface_equipotential


def test_unidentified_or_unreviewed_effective_geometry_stays_unknown():
    q=request()
    for change in [{'effective_geometry_basis_id':None},{'uniform_leakage_effective_envelope_approximation_selected':False}]:
        r=evaluate_groundbed_effective_envelope(q.model_copy(update=change))
        assert r.status=='needs_data' and r.approximate_ground is None


@pytest.mark.parametrize('change',[{'radius_m':.09},{'length_m':4.},{'axis':'horizontal'}])
def test_core_outside_or_not_coaxial_with_envelope_rejected(change):
    q=request();core=q.metal_core_geometries[0].model_copy(update=change)
    with pytest.raises(ValueError):evaluate_groundbed_effective_envelope(q.model_copy(update={'metal_core_geometries':(core,)}))


def test_missing_core_to_envelope_assignment_not_guessed_from_names():
    q=request()
    with pytest.raises(ValueError):evaluate_groundbed_effective_envelope(q.model_copy(update={'core_to_envelope_id':{}}))


def test_core_current_not_silently_mixed_with_envelope_control():
    q=request();core=q.metal_core_geometries[0].model_copy(update={'signed_current_into_ground_A':2.})
    with pytest.raises(ValueError):evaluate_groundbed_effective_envelope(q.model_copy(update={'metal_core_geometries':(core,)}))


def test_unknown_homogeneous_soil_not_replaced_by_effective_backfill_material():
    q=request();model=q.effective_envelope_ground.model_copy(update={'uniform_resistivity_ohm_m':None})
    result=evaluate_groundbed_effective_envelope(q.model_copy(update={'effective_envelope_ground':model}))
    assert result.status=='needs_data' and result.approximate_ground.common_bus_resistance_ohm is None


def finite_backfill_request(count=1,rho=2.):
    q=request(count=count)
    return q.model_copy(update={'internal_backfill_model':'difference_of_homogeneous_self_estimates',
        'uniform_backfill_resistivity_ohm_m_by_core':{c.id:rho for c in q.metal_core_geometries},
        'backfill_difference_approximation_reviewed':True})


def test_internal_difference_has_independent_long_coaxial_radial_ohm_limit():
    errors=[];rho=2.;length=3.
    # Integrate radial J=I/(2*pi*r*L) and E=rho*J across a uniform
    # cylindrical sleeve: dV/I=rho*ln(rout/rin)/(2*pi*L).
    reference=rho*math.log(4)/(2*math.pi*length)
    for outer_radius in [.03,.003,.0003]:
        q=finite_backfill_request();outer=q.effective_envelope_ground.electrodes[0].model_copy(update={'radius_m':outer_radius})
        inner=q.metal_core_geometries[0].model_copy(update={'length_m':length,'radius_m':outer_radius/4,'start_xyz_depth_m':outer.start_xyz_depth_m})
        q=q.model_copy(update={'metal_core_geometries':(inner,),
            'effective_envelope_ground':q.effective_envelope_ground.model_copy(update={'electrodes':(outer,)})})
        result=evaluate_groundbed_effective_envelope(q);assert result.status=='computed'
        errors.append(abs(result.internal_backfill_resistance_ohm_by_core['core0']-reference))
    assert errors[1]<.12*errors[0] and errors[2]<.12*errors[1]
    assert errors[-1]/reference<1e-4


def test_equal_array_internal_bank_resistance_and_independent_energy_budget():
    q=finite_backfill_request(count=2);result=evaluate_groundbed_effective_envelope(q);assert result.status=='computed'
    outer=q.effective_envelope_ground.electrodes[0];core=q.metal_core_geometries[0]
    # Independent finite self-potential integrals, scaled to explicit backfill
    # resistivity, rather than calling a second copy of the production observer.
    internal=(finite_uniform_line_reference(core,core,True)-finite_uniform_line_reference(outer,outer,True))*2/70
    R=np.array(result.approximate_ground.resistance_matrix_ohm)
    assert list(result.internal_backfill_resistance_ohm_by_core.values())==pytest.approx([internal,internal],rel=3e-10)
    assert result.estimated_common_bus_resistance_ohm==pytest.approx((R[0,0]+R[0,1]+internal)/2,rel=3e-10)
    assert result.signed_currents_A==pytest.approx((1.,1.),rel=2e-14)
    assert result.internal_model_ohmic_power_W==pytest.approx(2*internal,rel=3e-10)
    assert result.total_model_ohmic_power_W==pytest.approx(result.approximate_ground.model_ohmic_power_W+2*internal,rel=3e-10)


def test_unequal_internal_resistance_redistributes_common_bus_current():
    q=finite_backfill_request(count=2).model_copy(update={'uniform_backfill_resistivity_ohm_m_by_core':{'core0':2.,'core1':20.}})
    result=evaluate_groundbed_effective_envelope(q);assert result.status=='computed'
    assert result.signed_currents_A[0]>result.signed_currents_A[1]
    assert sum(result.signed_currents_A)==pytest.approx(2.,rel=2e-14)
    assert result.total_rod_average_potentials_V[0]==pytest.approx(result.total_rod_average_potentials_V[1],rel=2e-14)
    assert result.total_model_ohmic_power_W==pytest.approx(2*result.total_rod_average_potentials_V[0],rel=2e-14)


def test_missing_backfill_properties_or_difference_review_preserved():
    q=finite_backfill_request()
    for update in [{'uniform_backfill_resistivity_ohm_m_by_core':None},
        {'uniform_backfill_resistivity_ohm_m_by_core':{}},{'backfill_difference_approximation_reviewed':False}]:
        result=evaluate_groundbed_effective_envelope(q.model_copy(update=update))
        assert result.status=='needs_data' and result.total_resistance_matrix_ohm==()


def test_backfill_resistivity_zero_limit_does_not_infer_default_material():
    q=finite_backfill_request();small=evaluate_groundbed_effective_envelope(q.model_copy(update={'uniform_backfill_resistivity_ohm_m_by_core':{'core0':1e-9}}))
    external=evaluate_groundbed_effective_envelope(q.model_copy(update={'internal_backfill_model':'effective_envelope_only'}))
    assert small.estimated_common_bus_resistance_ohm==pytest.approx(external.estimated_common_bus_resistance_ohm,rel=1e-10)
    with pytest.raises(ValueError):evaluate_groundbed_effective_envelope(q.model_copy(update={'uniform_backfill_resistivity_ohm_m_by_core':{'core0':-1.}}))


def test_prescribed_current_control_includes_series_drop_without_bus_constraint():
    q=finite_backfill_request(count=2);model=q.effective_envelope_ground.model_copy(update={'mode':'prescribed_currents','total_signed_current_A':None,
        'electrodes':tuple(r.model_copy(update={'signed_current_into_ground_A':i}) for r,i in zip(q.effective_envelope_ground.electrodes,[2.,0.]))})
    result=evaluate_groundbed_effective_envelope(q.model_copy(update={'effective_envelope_ground':model}))
    assert result.status=='computed' and result.estimated_common_bus_resistance_ohm is None
    assert result.signed_currents_A==(2.,0.)
    assert result.total_model_ohmic_power_W==pytest.approx(2*result.total_rod_average_potentials_V[0],rel=1e-14)
