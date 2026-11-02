"""All ideal cavity end pairs versus independently separated wave eigenfunctions."""
import math
import numpy as np
import pytest
from pipeline_integrity.acoustic_cavity_modes import AcousticCavityModeRequest as Request,evaluate_acoustic_cavity_modes as evaluate
from pipeline_integrity.contracts import EvidenceRef,RunContext
C=RunContext(asset_id='uniform-cavity',scenario_id='independent-modes',clock_id='reference',origin='synthetic',
    evidence=(EvidenceRef(id='authored',citation='Authored uniform-cavity properties and ideal boundaries',origin='synthetic',license_id='MIT'),))
def request(left='rigid_closed',right='pressure_release_open',elements=16):
    return Request(asset_id=C.asset_id,clock_id=C.clock_id,length_m=5.,sound_speed_m_s=340.,left_boundary=left,right_boundary=right,
        elements=elements,requested_modes=3,property_geometry_boundary_evidence_id='authored',
        uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed=True)

@pytest.mark.parametrize('left,right',[(a,b) for a in ('rigid_closed','pressure_release_open') for b in ('rigid_closed','pressure_release_open')])
def test_all_end_pairs_eigenfrequencies_shapes_and_mesh_convergence(left,right):
    errors=[]
    for elements in (8,16,32):
        q=request(left,right,elements);r=evaluate(q,C);assert r['status']=='computed'
        errors_this=[];x=np.asarray(r['node_position_m'])
        for n,mode in enumerate(r['modes'],start=1):
            wave=n*math.pi/q.length_m if left==right else (2*n-1)*math.pi/(2*q.length_m)
            exact=q.sound_speed_m_s*wave/(2*math.pi)
            errors_this.append(abs(mode['frequency_hz']/exact-1))
            shape=np.sin(wave*x) if left=='pressure_release_open' else np.cos(wave*x)
            shape/=max(abs(shape))
            assert mode['normalized_nodal_pressure_shape']==pytest.approx(shape,abs=2e-11)
            assert mode['relative_eigen_residual']<1e-11
        errors.append(max(errors_this))
        assert r['excluded_constant_pressure_zero_modes']==int(left==right=='rigid_closed')
    assert errors[2]<errors[1]<errors[0] and errors[-1]<.004

def test_sound_speed_length_similarity_without_inferred_fluid_or_structural_mass():
    q=request();a=evaluate(q,C);b=evaluate(q.model_copy(update={'length_m':10.,'sound_speed_m_s':680.}),C)
    assert [m['frequency_hz'] for m in b['modes']]==pytest.approx([m['frequency_hz'] for m in a['modes']],rel=2e-12)
    assert b['structural_displacement_or_mass_predicted'] is False

def test_unknown_sound_speed_is_withheld():
    assert evaluate(request().model_copy(update={'sound_speed_m_s':None}),C)['status']=='needs_data'

def test_unreviewed_boundary_is_withheld():
    assert evaluate(request().model_copy(update={'uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed':False}),C)['status']=='needs_data'
