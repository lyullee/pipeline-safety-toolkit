"""Independent authored geometry and mass-flow references for proving factors."""
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import quad, solve_ivp

from pipeline_integrity.master_meter_proving import MasterMeterProvingRequest as Request, MasterMeterTransferObservation as Transfer, evaluate_master_meter_proving as evaluate


def request(**changes):
    values=dict(master_meter_id="master-one",proving_run_id="calibration-one",prover_base_volume_m3=.031,
        supplied_prover_pressure_volume_factor=1.002,supplied_prover_temperature_volume_factor=.998,
        prover_volume_correction_basis_id="supplied-shell-state-corrections",prover_actual_density_kg_m3=870.,prover_state_basis_id="prover-state",
        calibration_turbine_density_kg_m3=823.,calibration_turbine_state_basis_id="turbine-calibration-state",
        nominal_turbine_pulse_factor_per_m3=17300.,nominal_pulse_factor_basis_id="indicated-pulses-per-volume",
        prover_sweep_elapsed_time_s=4.1,whole_pulse_interval_elapsed_time_s=1.9,whole_turbine_pulses_in_interval=137,
        elapsed_time_clock_basis_id="elapsed-seconds",reference_volume_state_and_correction_applicability_reviewed=True,
        synchronized_calibration_mass_window_and_single_phase_reviewed=True,clock_scale_and_representative_frequency_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("rho_p,rho_t,master_bias,downstream_bias",[(870.,823.,.97,1.021),(31.,27.,1.013,.983),(990.,990.,1.,1.)])
def test_geometry_volume_and_independent_mass_counter_odes(rho_p,rho_t,master_bias,downstream_bias):
    radius,length=.071,.83
    hoop_strain,axial_strain,thermal_strain=.00071,-.00013,.00093
    base_volume=math.pi*radius**2*length
    cp=(1+hoop_strain)**2*(1+axial_strain)
    ct=(1+thermal_strain)**3
    radius_at_state=radius*(1+hoop_strain)*(1+thermal_strain)
    length_at_state=length*(1+axial_strain)*(1+thermal_strain)
    # Reference integrates actual cross-section at state, not the factor product.
    volume=quad(lambda z:math.pi*radius_at_state**2,0,length_at_state,epsabs=1e-14)[0]
    reference_mass=volume*rho_p
    duration=3.71;true_mass_rate=reference_mass/duration;nominal=19071.;count=71
    def dynamics(t,y):
        indicated_rate=true_mass_rate/master_bias
        return (true_mass_rate,indicated_rate,nominal*indicated_rate/rho_t)
    def whole_count(t,y):return y[2]-count
    whole_count.terminal=True
    sol=solve_ivp(dynamics,(0,10*duration),(0.,0.,0.),events=whole_count,rtol=1e-12,atol=1e-13,max_step=duration/40)
    pulse_interval=float(sol.t_events[0][0])
    true_transfer_mass=17.3;rho_transfer=.96*rho_t
    indicated_turbine_mass=true_transfer_mass/master_bias
    t=Transfer(turbine_pulses_in_transfer_window=nominal*indicated_turbine_mass/rho_transfer,
        turbine_density_kg_m3=rho_transfer,turbine_state_basis_id="transfer-turbine-state",
        downstream_indicated_mass_kg=true_transfer_mass/downstream_bias,downstream_indication_basis_id="downstream-indication",
        common_mass_transfer_window_id="matched-transfer-window",synchronized_window_and_negligible_intermediate_storage_reviewed=True,
        calibrated_master_factor_and_pulse_basis_applicable_reviewed=True)
    q=request(prover_base_volume_m3=base_volume,supplied_prover_pressure_volume_factor=cp,
        supplied_prover_temperature_volume_factor=ct,prover_actual_density_kg_m3=rho_p,
        calibration_turbine_density_kg_m3=rho_t,nominal_turbine_pulse_factor_per_m3=nominal,
        prover_sweep_elapsed_time_s=duration,whole_pulse_interval_elapsed_time_s=pulse_interval,
        whole_turbine_pulses_in_interval=count,transfer=t)
    r=evaluate(q)
    assert r.status==r.transfer_status=="computed"
    assert r.corrected_prover_volume_m3==pytest.approx(volume,rel=3e-14)
    assert r.reference_prover_mass_kg==pytest.approx(reference_mass,rel=3e-14)
    assert r.master_true_over_indicated_mass_factor==pytest.approx(master_bias,rel=3e-13)
    assert r.transfer_reference_mass_kg==pytest.approx(true_transfer_mass,rel=4e-13)
    assert r.downstream_true_over_indicated_mass_factor==pytest.approx(downstream_bias,rel=4e-13)
    assert r.reference_volume_at_turbine_state_m3*rho_t==pytest.approx(reference_mass)
    assert r.raw_prover_over_indicated_turbine_volume_ratio*rho_p/rho_t==pytest.approx(master_bias)
    if rho_p!=rho_t:assert r.raw_prover_over_indicated_turbine_volume_ratio!=pytest.approx(master_bias,rel=.001)


def test_no_default_unity_steel_correction_or_density_state():
    for field in ("supplied_prover_pressure_volume_factor","supplied_prover_temperature_volume_factor",
                  "prover_volume_correction_basis_id","prover_actual_density_kg_m3","calibration_turbine_density_kg_m3",
                  "prover_state_basis_id","calibration_turbine_state_basis_id"):
        assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["master_meter_id","proving_run_id","nominal_turbine_pulse_factor_per_m3","nominal_pulse_factor_basis_id",
    "prover_sweep_elapsed_time_s","whole_pulse_interval_elapsed_time_s","whole_turbine_pulses_in_interval","elapsed_time_clock_basis_id"])
def test_unknown_counter_or_frame(field):
    assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["reference_volume_state_and_correction_applicability_reviewed",
    "synchronized_calibration_mass_window_and_single_phase_reviewed","clock_scale_and_representative_frequency_reviewed"])
def test_unreviewed_calibration_mass_path_is_not_inferred(field):
    assert evaluate(request(**{field:False})).status=="needs_data"


@pytest.mark.parametrize("field",["synchronized_window_and_negligible_intermediate_storage_reviewed",
    "calibrated_master_factor_and_pulse_basis_applicable_reviewed"])
def test_unreviewed_transfer_keeps_valid_calibration_observation(field):
    values=dict(turbine_pulses_in_transfer_window=300.,turbine_density_kg_m3=820.,turbine_state_basis_id="transfer-state",
        downstream_indicated_mass_kg=15.,downstream_indication_basis_id="downstream-mass",common_mass_transfer_window_id="window",
        synchronized_window_and_negligible_intermediate_storage_reviewed=True,calibrated_master_factor_and_pulse_basis_applicable_reviewed=True)
    values[field]=False
    r=evaluate(request(transfer=Transfer(**values)))
    assert r.status=="computed" and r.transfer_status=="needs_data"
    assert r.master_true_over_indicated_mass_factor>0 and r.transfer_reference_mass_kg is None


def test_calibration_pulse_count_split_and_no_transfer_request():
    q=request();base=evaluate(q)
    doubled=evaluate(request(whole_turbine_pulses_in_interval=274,whole_pulse_interval_elapsed_time_s=3.8))
    assert doubled.master_true_over_indicated_mass_factor==pytest.approx(base.master_true_over_indicated_mass_factor)
    assert base.transfer_status=="not_requested"
    assert not base.state_density_steel_correction_or_calibration_inferred
    assert not base.measurement_uncertainty_or_field_accuracy_qualified


@pytest.mark.parametrize("field,value",[("supplied_prover_pressure_volume_factor",0.),("prover_actual_density_kg_m3",-1.),
    ("calibration_turbine_density_kg_m3",float("nan")),("whole_turbine_pulses_in_interval",True)])
def test_invalid_state_or_counter_contract(field,value):
    with pytest.raises(ValidationError):request(**{field:value})


def test_finite_support_and_replay():
    assert evaluate(request(prover_base_volume_m3=1e308)).status=="out_of_scope"
    q=request();saved=q.model_dump(mode="python")
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
    assert q.model_dump(mode="python")==saved
    with pytest.raises(ValidationError):evaluate(q.model_copy(update={"nominal_turbine_pulse_factor_per_m3":0.}))
