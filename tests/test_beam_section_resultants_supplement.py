"""Required section-moment and extreme-sag outputs beyond displacement samples."""
import math,runpy
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.structural import FrameLineLoad,FrameMember,FrameNode,FrameSupport,solve_frame

OLD=runpy.run_path('tests/test_declared_beam_layouts_independent.py');EXAMPLE=OLD['EXAMPLE'];SAMPLES=OLD['SAMPLES']


def output(q):
    r=solve_frame(q,EXAMPLE['context']());assert r['status']=='computed'
    r=r['combinations']['service'];assert r['static_balance']['passed'];return r


@pytest.mark.parametrize('kind',['fixed','propped'])
@pytest.mark.parametrize('length,load',[(2.,100.),(4.,220.),(7.,15.)])
def test_uniform_exact_restraints_full_section_moment_shear_and_work(kind,length,load):
    q=EXAMPLE['make_case']();ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    if kind=='propped':p=OLD['polynomial_span_reference'](1,'propped')[0]
    else:p=np.polynomial.Polynomial([0.,0.,-1/24,1/12,-1/24])
    roots=[x.real for x in p.deriv().roots() if abs(x.imag)<1e-12 and 0<x.real<1]
    fractions=tuple(sorted(set((*SAMPLES,*roots))))
    q=q.model_copy(update={'nodes':(q.nodes[0],q.nodes[1].model_copy(update={'position_m':(length,0.,0.)})),
        'members':(q.members[0].model_copy(update={'sample_fractions':fractions}),),
        'supports':q.supports+(FrameSupport(node_id='tip',fixed_dofs=('DX','DY','DZ','RX','RY')+(('RZ',) if kind=='fixed' else ()),evidence_id='reference'),),
        'node_loads':(),'line_loads':(FrameLineLoad(member_id='pipe',global_direction='FY',start_n_m=-load,end_n_m=-load,case_id='point',evidence_id='reference'),)})
    r=output(q);samples=r['members']['pipe']['samples']
    for s in samples:
        x=s['fraction'];m=load*length**2*p.deriv(2)(x);shear=-load*length*p.deriv(3)(x)
        assert s['physical_section_moment_local_nm'][2]==pytest.approx(m,rel=3e-10,abs=2e-9)
        assert s['physical_section_force_local_n'][1]==pytest.approx(shear,rel=3e-10,abs=2e-9)
        assert s['elastic_bending_curvature_z_per_m']==pytest.approx(m/ei,rel=3e-10,abs=1e-14)
    expected_max=max(abs(p(x)) for x in [0.,1.,*roots])*load*length**4/ei
    assert max(abs(s['local_translation_m'][1]) for s in samples)==pytest.approx(expected_max,rel=3e-10)
    work=quad(lambda x:-load*(load*length**4/ei)*p(x/length),0,length)[0]
    energy=quad(lambda x:(load*length**2*p.deriv(2)(x/length))**2/(2*ei),0,length)[0]
    assert work==pytest.approx(2*energy,rel=3e-12)


@pytest.mark.parametrize('count',[4,8])
def test_continuous_layout_exact_section_moments_and_bending_energy(count):
    length,load=2.5,160.;q=EXAMPLE['make_case']();ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    refs=OLD['polynomial_span_reference'](count,'continuous')
    nodes=tuple(FrameNode(id=f'n{i}',position_m=(i*length,0.,0.)) for i in range(count+1))
    members=tuple(FrameMember(id=f's{i}',i_node=f'n{i}',j_node=f'n{i+1}',section_id='annulus',material_id='declared-elastic',sample_fractions=SAMPLES) for i in range(count))
    supports=tuple(FrameSupport(node_id=n.id,fixed_dofs=(('DX',) if i==0 else ())+('DY','DZ','RX','RY'),evidence_id='reference') for i,n in enumerate(nodes))
    loads=tuple(FrameLineLoad(member_id=m.id,global_direction='FY',start_n_m=-load,end_n_m=-load,case_id='point',evidence_id='reference') for m in members)
    q=q.model_copy(update={'nodes':nodes,'members':members,'supports':supports,'line_loads':loads,'node_loads':()});r=output(q)
    energy=work=0.
    for i,p in enumerate(refs):
        for s in r['members'][f's{i}']['samples']:
            assert s['physical_section_moment_local_nm'][2]==pytest.approx(load*length**2*p.deriv(2)(s['fraction']),rel=3e-10,abs=2e-9)
        energy+=quad(lambda z:(load*length**2*p.deriv(2)(z))**2*length/(2*ei),0,1)[0]
        work+=quad(lambda z:-load*(load*length**4/ei)*p(z)*length,0,1)[0]
    assert work==pytest.approx(2*energy,rel=3e-11)


@pytest.mark.parametrize('fraction',[.2,.45,.8])
def test_eccentric_load_exact_peak_sag_and_section_moment(fraction):
    length,load=4.,1000.;a=length*fraction;b=length-a;q=OLD['point_layout'](length,fraction,load,True)
    candidates=[x for x in [math.sqrt((length**2-b*b)/3),length-math.sqrt((length**2-a*a)/3)] if 0<x<length]
    candidates=[x for x in candidates if (x<=a and abs(x-math.sqrt((length**2-b*b)/3))<1e-12) or (x>=a and abs(x-(length-math.sqrt((length**2-a*a)/3)))<1e-12)]
    deflections=[];members=[]
    for m,start,end in zip(q.members,[0.,a],[a,length]):
        fs=tuple(sorted(set((*SAMPLES,*[(x-start)/(end-start) for x in candidates if start<x<end]))))
        members.append(m.model_copy(update={'sample_fractions':fs}))
    q=q.model_copy(update={'members':tuple(members)});r=output(q);ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    def reference(x):return -load*b*x*(length**2-b*b-x*x)/(6*length*ei) if x<=a else -load*a*(length-x)*(length**2-a*a-(length-x)**2)/(6*length*ei)
    for m,start in zip(q.members,[0.,a]):
        for s in r['members'][m.id]['samples']:
            x=start+s['x_m'];moment=load*b/length*x-load*max(0.,x-a)
            assert s['physical_section_moment_local_nm'][2]==pytest.approx(moment,rel=3e-10,abs=2e-9)
            deflections.append(abs(s['local_translation_m'][1]))
    assert max(deflections)==pytest.approx(max(abs(reference(x)) for x in candidates),rel=3e-10)


@pytest.mark.parametrize('length,force',[(2.,100.),(4.,1000.),(7.,250.)])
def test_actual_end_loaded_cantilever_traction_bending_work_and_tip_sag(length,force):
    q=EXAMPLE['make_case']();q=q.model_copy(update={'nodes':(q.nodes[0],q.nodes[1].model_copy(update={'position_m':(length,0.,0.)})),
        'members':(q.members[0].model_copy(update={'sample_fractions':SAMPLES}),),
        'node_loads':(q.node_loads[0].model_copy(update={'value':-force}),)})
    r=output(q);ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    for s in r['members']['pipe']['samples']:
        assert s['physical_section_moment_local_nm'][2]==pytest.approx(-force*(length-s['x_m']),rel=3e-10,abs=2e-9)
    tip=r['nodes']['tip']['translation_m'][1]
    assert tip==pytest.approx(-force*length**3/(3*ei),rel=3e-10)
    energy=quad(lambda x:(force*(length-x))**2/(2*ei),0,length)[0]
    assert -force*tip==pytest.approx(2*energy,rel=3e-10)
