"""Independent equilibrium and elastic-work checks for a declared simple span."""
import runpy

import numpy as np
import pytest
from scipy.integrate import quad

from pipeline_integrity.structural import FrameLineLoad, FrameSupport, solve_frame

EXAMPLE = runpy.run_path("examples/pipe_frame.py")


@pytest.mark.parametrize("length,load", [(4.,100.),(2.,220.),(7.,15.),(4.,-100.),(4.,0.)])
def test_simple_span_complete_resultants_deflection_and_elastic_work(length,load):
    q=EXAMPLE['make_case']();c=EXAMPLE['context']()
    fractions=tuple(float(x) for x in np.linspace(0,1,21))
    q=q.model_copy(update={
        'nodes':(q.nodes[0],q.nodes[1].model_copy(update={'position_m':(length,0.,0.)})),
        'members':(q.members[0].model_copy(update={'sample_fractions':fractions}),),
        'supports':(FrameSupport(node_id='anchor',fixed_dofs=('DX','DY','DZ','RX'),evidence_id='reference'),
                    FrameSupport(node_id='tip',fixed_dofs=('DY','DZ'),evidence_id='reference')),
        'node_loads':(),
        'line_loads':(FrameLineLoad(member_id='pipe',global_direction='FY',start_n_m=-load,
                                   end_n_m=-load,case_id='point',evidence_id='reference'),)})
    r=solve_frame(q,c);assert r['status']=='computed'
    out=r['combinations']['service'];rows=out['members']['pipe']['samples']
    ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    for name in ('anchor','tip'):
        assert out['nodes'][name]['reaction_force_n'][1]==pytest.approx(load*length/2,abs=1e-9)
        assert out['nodes'][name]['reaction_moment_nm'][2]==pytest.approx(0.,abs=1e-9)
    # Independent cut equilibrium supplies moment; integrating curvature twice
    # with zero displacement at both supports supplies the full deflection.
    for row in rows:
        x=row['x_m'];moment=load*x*(length-x)/2
        assert abs(row['moment_z_nm'])==pytest.approx(abs(moment),abs=2e-9)
        displacement=-load*x*(length**3-2*length*x*x+x**3)/(24*ei)
        assert row['local_translation_m'][1]==pytest.approx(displacement,abs=2e-13,rel=1e-9)
    assert abs(rows[10]['moment_z_nm'])==pytest.approx(abs(load)*length**2/8,abs=2e-9)
    assert max(abs(row['local_translation_m'][1]) for row in rows)==pytest.approx(
        5*abs(load)*length**4/(384*ei),abs=2e-13)
    strain_energy=quad(lambda x:(load*x*(length-x)/2)**2/(2*ei),0,length)[0]
    external_work=quad(lambda x:.5*load*load*x*(length**3-2*length*x*x+x**3)/(24*ei),0,length)[0]
    assert strain_energy==pytest.approx(external_work,rel=2e-13,abs=1e-15)
    assert out['static_balance']['passed']
