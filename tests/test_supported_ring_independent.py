"""Continuous curved-beam virtual work versus the unchanged polygon-frame owner."""
import math
import numpy as np
import pytest
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.structural import FrameSection
from pipeline_integrity.supported_ring_observer import RingLoadHarmonic, SupportedRingRequest, evaluate_supported_ring

CTX=RunContext(asset_id='ring',scenario_id='supplied-foundation',clock_id='static',origin='synthetic',
    evidence=(EvidenceRef(id='basis',citation='Authored circular strip and bilateral isotropic foundation',origin='synthetic'),))


def request(**changes):
    b,t=.15,.006
    section=FrameSection(id='rectangular-strip',area_m2=b*t,iz_m4=b*t**3/12,
        iy_m4=t*b**3/12,torsion_constant_m4=b*t**3/3,evidence_id='basis')
    return SupportedRingRequest(**dict(dict(asset_id='ring',clock_id='static',reference_radius_m=.6,
        section=section,bending_surface_distance_m=t/2,young_modulus_pa=200e9,poisson_ratio=.3,
        isotropic_foundation_line_density_n_m2=2e5,
        load_harmonics=(RingLoadHarmonic(mode=2,radial_cosine_n_m=10.),),
        evidence_id='basis',linear_elastic_strip_full_contact_isotropic_foundation_and_load_basis_reviewed=True),**changes))


@pytest.mark.parametrize('mode',[2,3])
@pytest.mark.parametrize('foundation',[5e3,5e6])
def test_actual_frame_converges_to_continuous_ring_stationary_energy(mode,foundation):
    q=request(isotropic_foundation_line_density_n_m2=foundation,
              load_harmonics=(RingLoadHarmonic(mode=mode,radial_cosine_n_m=10.),))
    radius=q.reference_radius_m;ea=q.young_modulus_pa*q.section.area_m2
    ei=q.young_modulus_pa*q.section.iz_m4;k=foundation
    # Integrate membrane, bending and foundation energy over a full reference
    # circle for w=W cos(n theta), v=V sin(n theta). Differentiate its two
    # amplitudes, independently of polygon element stiffness matrices.
    stiffness=np.array([[ea/radius+ei*mode**4/radius**3+k*radius,
                         mode*ea/radius+ei*mode**3/radius**3],
                        [mode*ea/radius+ei*mode**3/radius**3,
                         mode**2*ea/radius+ei*mode**2/radius**3+k*radius]])
    w,v=np.linalg.solve(stiffness,[radius*10.,0.])
    stress=abs(ei*(mode*mode*w+mode*v)/radius**2)*q.bending_surface_distance_m/q.section.iz_m4
    errors=[]
    for count in [24,48,96]:
        result=evaluate_supported_ring(q.model_copy(update={'circumferential_members':count}),CTX)
        assert result['status']=='computed'
        nodes=result['owner_result']['combinations']['case']['nodes']
        actual=nodes['n0']['translation_m'][0]
        errors.append((abs(actual/w-1),abs(result['maximum_absolute_nominal_bending_stress_pa']/stress-1)))
        assert result['owner_result']['combinations']['case']['static_balance']['passed']
        # The exact linear FE solution satisfies external work=2 stored energy.
        ds=2*math.pi*radius/count
        external=0.;foundation_energy=0.
        for j in range(count):
            theta=2*math.pi*j/count;u=np.array(nodes[f'n{j}']['translation_m'][:2])
            force=10*math.cos(mode*theta)*ds*np.array([math.cos(theta),math.sin(theta)])
            external+=float(force@u);foundation_energy+=.5*k*ds*float(u@u)
        elastic_energy=0.
        for member in result['owner_result']['combinations']['case']['members'].values():
            first,last=member['samples'][0],member['samples'][-1]
            axial=first['physical_section_force_local_n'][0]
            ma,mb=(p['physical_section_moment_local_nm'][2] for p in [first,last])
            elastic_energy+=member['length_m']/(2*q.young_modulus_pa)*(axial*axial/q.section.area_m2+
                (ma*ma+ma*mb+mb*mb)/(3*q.section.iz_m4))
        assert external==pytest.approx(2*(foundation_energy+elastic_energy),rel=2e-7)
    for component in [0,1]:
        assert errors[2][component]<errors[1][component]<errors[0][component]
        assert errors[2][component]<.02


def test_uniform_radial_loading_matches_membrane_and_foundation_equilibrium():
    q=request(load_harmonics=(RingLoadHarmonic(mode=0,radial_cosine_n_m=-10.),),circumferential_members=96)
    result=evaluate_supported_ring(q,CTX);assert result['status']=='computed'
    radius=q.reference_radius_m
    w=-10/(q.young_modulus_pa*q.section.area_m2/radius**2+q.isotropic_foundation_line_density_n_m2)
    assert result['signed_x_diameter_change_m']==pytest.approx(2*w,rel=.002)
    assert result['signed_y_diameter_change_m']==pytest.approx(2*w,rel=.002)


def test_unknown_soil_basis_is_not_defaulted():
    q=request(linear_elastic_strip_full_contact_isotropic_foundation_and_load_basis_reviewed=False)
    assert evaluate_supported_ring(q,CTX)['status']=='needs_data'


def test_underresolved_angular_loading_is_withheld():
    q=request(circumferential_members=16,load_harmonics=(RingLoadHarmonic(mode=3,radial_cosine_n_m=10.),))
    with pytest.raises(ValueError,match='eight members'):
        evaluate_supported_ring(q,CTX)


def test_explicit_small_displacement_support_exit_is_preserved():
    q=request(load_harmonics=(RingLoadHarmonic(mode=2,radial_cosine_n_m=1e6),),circumferential_members=24,
              maximum_translation_over_radius=1e-5)
    assert evaluate_supported_ring(q,CTX)['status'] in ['out_of_scope','owner_withheld']
