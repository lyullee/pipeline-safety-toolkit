"""Small, reproducible calibration feasibility checks; no production defaults change."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,math,os,platform,runpy,sys,time
from pathlib import Path
from importlib.metadata import version
from scipy.optimize import minimize_scalar
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
PRIVATE=ROOT.parent/'tmp/analysis'
from pipeline_integrity.models.hydraulics import LiquidHeadLoss,liquid_head_loss
from pipeline_integrity.reviewed_dynamic_correlations import DynamicStrengthCoordinateRequest,evaluate_dynamic_strength_coordinate

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def pipe_option():
    m=runpy.run_path(str(ROOT/'tests/test_pipe_friction_hydralab_heldout_qualification.py'))
    rows=m['_rows']();training={r:rows[r] for r in m['TRAIN_RUNS']};diameter=m['DIAMETER_M'];gravity=m['GRAVITY_M_S2']
    def state(row):return m['_state'](row)
    def observed(row,a,b):
        rho,*_=state(row)
        return (row[a+'_bar_g']-row[b+'_bar_g'])*1e5/(rho*gravity)
    def head(row,length,rr):
        rho,nu,flow,_=state(row);velocity=flow/(math.pi*diameter**2/4)
        return m['_colebrook'](velocity*diameter/nu,rr)*length/diameter*velocity**2/(2*gravity)
    # Fitting receives only the training rows; all comparison rows are evaluated later.
    fitted={}
    for a,b,length in m['SEGMENTS']:
        fit=minimize_scalar(lambda log_rr:sum((head(row,length,10**log_rr)/observed(row,a,b)-1)**2 for row in training.values()),
            bounds=(-6.,-2.),method='bounded',options={'xatol':1e-10})
        if not fit.success or min(abs(fit.x+6),abs(fit.x+2))<1e-5:raise ValueError('Effective roughness fit failed or reached its original search boundary')
        fitted[(a,b)]={'relative_roughness':10**fit.x,'length_m':length}
    common_rr=m['_fit_relative_roughness'](rows)
    comparisons=[];max_discrepancy=0.
    for run in m['HOLDOUT_RUNS']:
        row=rows[run];rho,nu,flow,temp=state(row)
        for a,b,length in m['SEGMENTS']:
            rr=fitted[(a,b)]['relative_roughness']
            request=LiquidHeadLoss(diameter_m=diameter,length_m=length,roughness_m=rr*diameter,
                volume_flow_m3_s=flow,kinematic_viscosity_m2_s=nu,gravity_m_s2=gravity,friction_method='Colebrook')
            result=liquid_head_loss(request)
            assert result.status=='computed'
            actual=result.values['required_inlet_minus_outlet_head_m'];reference=head(row,length,rr)
            max_discrepancy=max(max_discrepancy,abs(actual/reference-1))
            assert abs(actual/reference-1)<2e-12
            measured=observed(row,a,b)
            comparisons.append({'run':run,'segment':a+'-'+b,'baseline_relative_error':head(row,length,common_rr)/measured-1,
                'candidate_relative_error':actual/measured-1,'candidate_accepts_existing_7pct_gate':abs(actual/measured-1)<=m['MAX_RELATIVE_HEAD_ERROR']})
    support={k:[min(state(r)[i] for r in training.values()),max(state(r)[i] for r in training.values())] for k,i in [('flow_m3_s',2),('temperature_K',3)]}
    outside=[run for run in m['HOLDOUT_RUNS'] if not support['flow_m3_s'][0]<=state(rows[run])[2]<=support['flow_m3_s'][1]]
    private_profile={'schema':'pipeline_integrity.rig_effective_roughness_candidate.v1','status':'exploratory_review_required','training_runs':list(training),
        'previously_inspected_comparison_runs':list(m['HOLDOUT_RUNS']),'diameter_m':diameter,'fluid':'Water','gravity_m_s2':gravity,
        'density_viscosity_provider':'CoolProp','provider_version':version('CoolProp'),'observed_training_support':support,
        'segments':{a+'-'+b:v for (a,b),v in fitted.items()},'comparison_rows':comparisons,
        'physical_wall_roughness_identified':False,'applicability_outside_observed_training_support_approved':False,
        'new_independent_validation':False,'note':'A segment-dependent effective coefficient can absorb unidentified rig losses or observation bias. It does not identify which physical cause is present.'}
    public={'model':'hydraulics.circular_pipe_friction','strategy':'three segment-specific effective roughness inputs to unchanged public API',
        'training_runs':list(training),'comparison_runs':list(m['HOLDOUT_RUNS']),'fitted_parameter_count':3,'baseline_parameter_count':1,
        'baseline_failed_comparison_runs':sum(any(c['run']==r and abs(c['baseline_relative_error'])>.07 for c in comparisons) for r in m['HOLDOUT_RUNS']),
        'candidate_accepted_comparison_runs':sum(all(c['candidate_accepts_existing_7pct_gate'] for c in comparisons if c['run']==r) for r in m['HOLDOUT_RUNS']),
        'candidate_accepted_segment_cases':sum(c['candidate_accepts_existing_7pct_gate'] for c in comparisons),'segment_case_count':len(comparisons),
        'baseline_max_absolute_error_percent':100*max(abs(c['baseline_relative_error']) for c in comparisons),
        'candidate_max_absolute_error_percent':100*max(abs(c['candidate_relative_error']) for c in comparisons),
        'independent_public_API_relative_difference_max':max_discrepancy,
        'comparison_runs_outside_observed_training_flow_interval':outside,'heldout_already_inspected_before_candidate_design':True,
        'independent_confirmatory_validation':False,'physical_cause_resolved':False,'production_default_changed':False,
        'disposition':'Retain as a rig-specific exploratory calibration candidate; original common-roughness failures remain. Further scope qualification needs new conditions or independent evidence.'}
    return public,private_profile

def strength_option():
    m=runpy.run_path(str(PRIVATE/'nist_rate_strength_reference.py'));rows=[]
    for kind in ('yield','ultimate'):
        for law in ('log_rate_ratio','power_rate_ratio'):
            for rate in (60.,95.,236.):
                ref=m['calibrate_and_reference'](kind,law,rate)
                assert ref['held']['specimen'] not in {r['specimen'] for r in ref['training']}
                q=DynamicStrengthCoordinateRequest(law=law,strain_rate_magnitude_per_s=rate,
                    reference_strain_rate_per_s=ref['reference_rate'],minimum_reviewed_rate_per_s=.000107,maximum_reviewed_rate_per_s=367.,
                    supplied_static_reference_strength_pa=ref['anchor_pa'],supplied_ratio_intercept=ref['ratio_intercept'],
                    supplied_ratio_multiplier=ref['ratio_multiplier'],supplied_power_exponent=.2 if law=='power_rate_ratio' else None,
                    material_temperature_rate_and_strength_kind_basis_id='N99-C3M1-L-HSR-TN-room-'+kind,
                    material_temperature_rate_law_and_units_reviewed=True)
                result=evaluate_dynamic_strength_coordinate(q)
                assert result['status']=='computed' and result['material_calibration_or_dynamic_allowable_qualified'] is False
                predicted=result['dynamic_strength_coordinate_pa'];assert math.isclose(predicted,ref['predicted_pa'],rel_tol=2e-13)
                error=predicted-ref['observed_pa'];width=ref['predictive_halfwidth_pa']
                rows.append({'strength_kind':kind,'law':law,'comparison_rate_per_s':rate,'error_MPa':error/1e6,'prediction_halfwidth_MPa':width/1e6,'accepted_existing_gate':abs(error)<=width})
    groups=[]
    for kind in ('yield','ultimate'):
        for law in ('log_rate_ratio','power_rate_ratio'):
            group=[r for r in rows if r['strength_kind']==kind and r['law']==law]
            groups.append({'strength_kind':kind,'law':law,'cases':len(group),'accepted_cases':sum(r['accepted_existing_gate'] for r in group),
                'RMSE_MPa':math.sqrt(sum(r['error_MPa']**2 for r in group)/len(group)),
                'MAE_MPa':sum(abs(r['error_MPa']) for r in group)/len(group),
                'mean_95pct_prediction_halfwidth_MPa':sum(r['prediction_halfwidth_MPa'] for r in group)/len(group)})
    assert sum(not r['accepted_existing_gate'] for r in rows)==1
    return {'model':'strain_rate_strength_correlation','computed_comparison_cases':12,'law_comparison':groups,
        'failed_existing_calibration_case_count':1,'all_materials_default_law_selected':False,'material_allowable_qualified':False,
        'disposition':'Log-law yield predictions have lower error on the inspected comparisons, but also wider intervals. Power-law ultimate predictions have lower RMSE here. Keep the failed fixed-power yield calibration; do not choose a universal law from these five specimens.'},rows

def main():
    started=time.monotonic();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=ROOT/'artifacts/quick-empirical-options'/stamp;output.mkdir(parents=True)
    private=PRIVATE/'quick-empirical-options'/stamp;private.mkdir(parents=True)
    files=[Path(__file__),ROOT/'src/pipeline_integrity/models/hydraulics.py',ROOT/'src/pipeline_integrity/reviewed_dynamic_correlations.py',
        ROOT/'tests/test_pipe_friction_hydralab_heldout_qualification.py',ROOT/'tests/test_rate_strength_nist_holdout.py',
        ROOT/'tests/test_cmsx6_creep_specimen_holdout_qualification.py',ROOT/'data/raw/hydralab_large_pipeline/steady_runs_240_330s.csv',
        PRIVATE/'nist_rate_strength_reference.py',PRIVATE/'public-references/nist-ncstar-rate-strength/n99_same_geometry_observations.json']
    before={str(p):sha(p) for p in files}
    with ThreadPoolExecutor(max_workers=2) as pool:
        pipe_future=pool.submit(pipe_option);strength_future=pool.submit(strength_option)
        pipe,profile=pipe_future.result();strength,strength_rows=strength_future.result()
    private_profile=private/'rig_calibration_candidate.json';private_profile.write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    (private/'strength_comparison.json').write_text(json.dumps(strength_rows,ensure_ascii=False,indent=2),encoding='utf-8')
    after={str(p):sha(p) for p in files};assert before==after
    report={'schema':'pipeline_integrity.quick_empirical_options.v1','pipe':pipe,'strength':strength,
        'candidate_scope':'Small exploratory calibration checks only; no production model, data, tolerance or original failed gate changed.',
        'original_empirical_failures_retained':4,'creep_reworked':False,'model_physical_scope_progress':{'completed_profiles':24,'total_models':223,'percent':100*24/223},
        'private_candidate_profile_sha256':sha(private_profile),'private_candidate_profile':str(private_profile),
        'elapsed_wall_s':time.monotonic()-started,'parallel_analysis_workers':2,'source_data_and_original_test_sha256':after,'source_data_or_original_tests_changed':False,
        'python':sys.version,'platform':platform.platform(),'packages':{k:version(k) for k in ('numpy','scipy','CoolProp','pydantic')}}
    path=output/'receipt.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output.parent/'latest.json').write_text(json.dumps({'receipt':path.relative_to(ROOT).as_posix(),'sha256':sha(path)},indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
