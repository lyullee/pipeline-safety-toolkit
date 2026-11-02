"""Probe the explicit ULP boundary using an independent cubic endpoint value."""
from decimal import Decimal,localcontext
import math
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.material_exchange import prepare_material_job
from pipeline_integrity.swt_endpoint_compatibility import evaluate_swt_cycle_job

CTX=RunContext(asset_id='endpoint-boundary',scenario_id='explicit-machine-budget',clock_id='whole-cycles',origin='synthetic',
    evidence=(EvidenceRef(id='calibration',citation='Authored quadratic/cubic exponent special case; explicit roundoff-only boundary',origin='synthetic'),))


def job_below_endpoint(steps):
    with localcontext() as context:
        context.prec=80;x=1/(Decimal(2)*Decimal('1e8')).sqrt()
        reference=Decimal('900e6')**2/Decimal('200e9')*x*x+Decimal('900e6')*Decimal('.6')*x*x*x
    start=float(reference);target=start
    for _ in range(steps):target=math.nextafter(target,0.)
    p=dict(strain_amplitude=target/400e6,maximum_stress_Pa=400e6,elastic_modulus_Pa=200e9,
        fatigue_strength_coefficient_Pa=900e6,fatigue_strength_exponent=-.5,fatigue_ductility_coefficient=.6,
        fatigue_ductility_exponent=-1.,minimum_valid_cycles=1.,maximum_valid_cycles=1e8,
        stress_and_strain_measurement_basis_reviewed=True,material_applicability_reviewed=True)
    return prepare_material_job('fatigue.swt_strain_life',p,CTX,location_id='point',evidence_ids=('calibration',))


def test_default_roundoff_accepts_a_nearby_15ulp_machine_endpoint_only():
    job=job_below_endpoint(15);r=evaluate_swt_cycle_job(job,CTX)
    assert r.native_material_receipt.native.status=='out_of_scope'
    assert r.status=='computed' and r.endpoint_reconciled and r.cycles_to_failure==1e8
    assert abs(r.endpoint_parameter_residual_pa)<=r.allowed_parameter_roundoff_pa
    assert r.allowed_parameter_roundoff_pa==16*math.ulp(max(r.swt_parameter_pa,r.forward_endpoint_parameter_pa))


def test_default_roundoff_rejects_a_target_20ulps_outside():
    r=evaluate_swt_cycle_job(job_below_endpoint(20),CTX)
    assert r.native_material_receipt.native.status=='out_of_scope'
    assert r.status=='out_of_scope' and not r.endpoint_reconciled and r.cycles_to_failure is None


def test_larger_explicit_machine_budget_does_not_change_the_physical_cycle_interval():
    job=job_below_endpoint(20);before=job.model_dump()
    r=evaluate_swt_cycle_job(job,CTX,maximum_endpoint_roundoff_ulps=32)
    assert r.status=='computed' and r.endpoint_reconciled
    assert r.cycles_to_failure==job.parameters['maximum_valid_cycles'] and job.model_dump()==before
    assert r.allowed_parameter_roundoff_pa==32*math.ulp(max(r.swt_parameter_pa,r.forward_endpoint_parameter_pa))
