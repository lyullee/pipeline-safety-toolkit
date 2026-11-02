"""Independent qualification oracles for hit/miss POD inference."""

import math
import pytest
from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext

CONTEXT = RunContext(asset_id="synthetic_pod_reference", scenario_id="independent_hit_miss_logistic_reference", clock_id="ordered_reference_observations", origin="synthetic", evidence=(EvidenceRef(id="authored", citation="Independently authored binary trial and log-size oracle", origin="synthetic", license_id="MIT"),))
CLIENT = MaterialClient()

def dataset(scale=1.0, confidence=0.90, independent=True, hits=(1, 2, 4, 8, 9)):
    sizes=[0.0005*scale,0.001*scale,0.002*scale,0.004*scale,0.008*scale]
    ids=[]; flaw_sizes=[]; detected=[]
    for level,(size,successes) in enumerate(zip(sizes,hits,strict=True)):
        for index in range(10):
            ids.append(f"s{level}-{index}"); flaw_sizes.append(size); detected.append(index<successes)
    return {"specimen_ids":ids,"flaw_size_m":flaw_sizes,"detected":detected,"query_sizes_m":[sizes[1],sizes[2],sizes[3]],"target_probability":0.5,"confidence_level":confidence,"maximum_threshold_size_m":0.04*scale,"independent_trials_declared":independent}

def evaluate(parameters):
    return CLIENT.evaluate("inspection.hit_miss_pod",parameters,CONTEXT,location_id="synthetic_test_section")

def expit(x):
    if x>=0: return 1/(1+math.exp(-x))
    e=math.exp(x); return e/(1+e)

def bisect(fn,target,lo,hi):
    for _ in range(120):
        mid=(lo+hi)/2
        if fn(mid)<target: lo=mid
        else: hi=mid
    return (lo+hi)/2

def independent_logistic_mle(parameters):
    logs=[math.log(v) for v in parameters["flaw_size_m"]]
    center=sum(logs)/len(logs); scale=math.sqrt(sum((v-center)**2 for v in logs)/len(logs))
    z=[(v-center)/scale for v in logs]; y=[float(v) for v in parameters["detected"]]
    def intercept(slope): return bisect(lambda b:sum(expit(b+slope*x) for x in z),sum(y),-80,80)
    def score(slope):
        b=intercept(slope)
        return sum(x*(expit(b+slope*x)-v) for x,v in zip(z,y,strict=True))
    slope=bisect(score,0,0,80)
    return center,scale,intercept(slope),slope

def test_fitted_logistic_scores_match_independent_nested_bisection_oracle():
    p=dataset(); result=evaluate(p); assert result.status=="computed"; v=result.payload["values"]
    center,scale,b0,b1=independent_logistic_mle(p)
    assert v["log_size_center_relative_to_1_m"]==pytest.approx(center,abs=2e-12)
    assert v["standardization_log_size_scale"]==pytest.approx(scale,abs=2e-12)
    assert v["intercept_standardized"]==pytest.approx(b0,abs=2e-7)
    assert v["slope_standardized"]==pytest.approx(b1,abs=2e-7)
    assert v["score_norm"]<1e-6 and (v["observations"],v["hits"],v["misses"])==(50,24,26)

def test_predictions_are_monotone_and_preserve_query_order():
    p=dataset(); rows=evaluate(p).payload["values"]["predictions"]
    assert [r["size_m"] for r in rows]==p["query_sizes_m"]
    assert all(0<r["probability"]<1 and 0<=r["pointwise_lower_confidence_probability"]<=r["probability"] for r in rows)
    assert rows[0]["probability"]<rows[1]["probability"]<rows[2]["probability"]

def test_uniform_size_scaling_preserves_fit_and_scales_thresholds():
    a=evaluate(dataset()).payload["values"]; b=evaluate(dataset(scale=7.25)).payload["values"]
    assert b["intercept_standardized"]==pytest.approx(a["intercept_standardized"],abs=2e-7)
    assert b["slope_standardized"]==pytest.approx(a["slope_standardized"],abs=2e-7)
    assert b["probability_of_detection"]==pytest.approx(a["probability_of_detection"],abs=2e-8)
    assert b["target_size_estimate_m"]==pytest.approx(7.25*a["target_size_estimate_m"],rel=2e-7)
    assert b["target_size_upper_confidence_m"]==pytest.approx(7.25*a["target_size_upper_confidence_m"],rel=2e-6)

def test_target_size_point_and_upper_bound_are_physically_ordered():
    v=evaluate(dataset()).payload["values"]
    assert v["target_size_estimate_m"] is not None
    assert v["target_size_upper_confidence_m"]>=v["target_size_estimate_m"]
    assert v["target_size_upper_confidence_m"]<=0.04 and v["upper_confidence_bound_status"]=="bounded"

def test_increasing_confidence_does_not_reduce_one_sided_target_bound():
    a=evaluate(dataset(confidence=0.80)).payload["values"]; b=evaluate(dataset(confidence=0.95)).payload["values"]
    assert b["target_size_upper_confidence_m"]>=a["target_size_upper_confidence_m"]
    assert b["likelihood_ratio_cutoff"]>a["likelihood_ratio_cutoff"]

def test_dependence_and_complete_separation_are_withheld():
    dependent=evaluate(dataset(independent=False)); assert dependent.status=="needs_data" and dependent.payload["values"]=={}
    separated=evaluate(dataset(hits=(0,0,0,10,10))); assert separated.status=="needs_data" and separated.payload["values"]=={}
