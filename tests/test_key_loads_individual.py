"""Independent section force/moment, contact-area and similarity checks."""
import pytest
from pipeline_integrity.key_loads import KeyEngagement as K,KeyLoadRequest as Q,evaluate_key_loads as solve

def make(**kw):return Q(**(dict(shaft_diameter_m=.04,applied_torque_N_m=100.,keys=(K(id='key',shear_width_m=.005,engaged_length_m=.1,actual_bearing_height_m=.0025,torque_share=1.),),nominal_radius_engagement_and_load_sharing_reviewed=True)|kw))

@pytest.mark.parametrize('torque',[-100.,0.,100.])
def test_contact_integrals_reconstruct_force_and_moment(torque):
 r=solve(make(applied_torque_N_m=torque)).values;row=r['key_results'][0];tau=row['nominal_signed_shear_stress_Pa'];bearing=row['nominal_bearing_magnitude_Pa']
 assert tau*.005*.1*.02==pytest.approx(torque,abs=1e-12)
 assert bearing*.0025*.1*.02==pytest.approx(abs(torque),abs=1e-12)
 assert row['signed_tangential_force_N']*.02==pytest.approx(torque,abs=1e-12)
 assert abs(r['torque_budget_residual_N_m'])<1e-12

def test_actual_bearing_height_is_independent_of_shear_width():
 q=make();s=q.keys[0].model_copy(update={'actual_bearing_height_m':.001});r=solve(q.model_copy(update={'keys':(s,)})).values['key_results'][0]
 assert r['nominal_signed_shear_stress_Pa']==pytest.approx(1e7)
 assert r['nominal_bearing_magnitude_Pa']==pytest.approx(5e7)

def test_unequal_reviewed_key_shares_and_different_contact_areas():
 ks=(K(id='a',shear_width_m=.005,engaged_length_m=.1,actual_bearing_height_m=.002,torque_share=.2),K(id='b',shear_width_m=.01,engaged_length_m=.05,actual_bearing_height_m=.004,torque_share=.8))
 r=solve(make(keys=ks)).values
 assert [x['signed_tangential_force_N'] for x in r['key_results']]==pytest.approx([1000.,4000.])
 assert sum(x['signed_tangential_force_N']*.02 for x in r['key_results'])==pytest.approx(100.)

@pytest.mark.parametrize('scale',[.2,3.])
def test_geometric_cube_torque_similarity(scale):
 q=make();s=q.keys[0];ss=s.model_copy(update={k:getattr(s,k)*scale for k in ('shear_width_m','engaged_length_m','actual_bearing_height_m')})
 a=solve(q).values['key_results'][0];b=solve(q.model_copy(update={'shaft_diameter_m':.04*scale,'applied_torque_N_m':100*scale**3,'keys':(ss,)})).values['key_results'][0]
 assert b['nominal_signed_shear_stress_Pa']==pytest.approx(a['nominal_signed_shear_stress_Pa'])
 assert b['nominal_bearing_magnitude_Pa']==pytest.approx(a['nominal_bearing_magnitude_Pa'])

@pytest.mark.parametrize('field',['shear_width_m','engaged_length_m','actual_bearing_height_m','torque_share'])
def test_unknown_contact_or_share_withheld(field):
 q=make();assert solve(q.model_copy(update={'keys':(q.keys[0].model_copy(update={field:None}),)})).status=='needs_data'

def test_load_shares_are_not_automatically_normalized():
 q=make()
 with pytest.raises(ValueError):solve(q.model_copy(update={'keys':(q.keys[0].model_copy(update={'torque_share':.5}),)}))

def test_missing_model_review_withheld():assert solve(make(nominal_radius_engagement_and_load_sharing_reviewed=False)).status=='needs_data'
