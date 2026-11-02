"""Actual model outputs handed to typed SI covariance-budget calculations."""
import json

import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import digest
from pipeline_integrity.metrology import MeasurementBudgetRequest, propagate_measurement_uncertainty
from properties_pipeline_cases_223 import SPECS,budget_for,produce,save_receipt


@pytest.mark.parametrize('unit',tuple(SPECS))
def test_real_observable_to_typed_budget_preserves_meaning_units_and_origin(unit,tmp_path,record_property):
    record_property('canonical_units',json.dumps(['proposal.'+unit]))
    record_property('test_kind','integration')
    observation=produce(unit,tmp_path)
    request,context,scale=budget_for(observation)
    result=propagate_measurement_uncertainty(request,context)
    assert result['status']=='computed'
    assert context.origin=='simulation'
    assert result['context']['origin']=='simulation'
    assert request.inputs[0].estimate==observation.value
    assert request.inputs[0].basis==observation.basis
    assert request.linearization.inputs_sha256==digest(request.inputs)
    output=result['outputs']['corrected-observable']
    assert output['basis']==observation.basis.model_dump(mode='json')
    assert output['estimate']==pytest.approx(observation.value+.01*scale,rel=3e-14,abs=0.)
    assert result['output_covariance'][0][0]==pytest.approx(.0007*scale*scale,rel=3e-14,abs=0.)
    assert output['standard_uncertainty']>0
    # Both context and exact input-basis bindings reject a mismatched handoff.
    with pytest.raises(ValueError,match='context must match the asset and clock'):
        propagate_measurement_uncertainty(request.model_copy(update={'clock_id':'another-clock'}),context)
    altered=request.model_dump(mode='python')
    altered['inputs'][0]['basis']['definition_id']='another-observable-definition'
    with pytest.raises(ValidationError,match='sensitivities must bind the exact input'):
        MeasurementBudgetRequest.model_validate(altered)
    save_receipt('integration',unit,observation,result)


def test_water_eos_endpoints_feed_actual_liquid_pump_mass_balance(record_property):
    record_property('canonical_units',json.dumps(['proposal.properties.liquid_water_eos_response','proposal.pump.liquid_clearance_volumetric_efficiency']))
    record_property('test_kind','integration')
    from pipeline_integrity.liquid_water_response import LiquidWaterResponseRequest,observe_liquid_water_response
    from pipeline_integrity.liquid_chamber_cycle import ChamberLiquidEndpoint,LiquidChamberCycleRequest,evaluate_liquid_chamber_cycle
    states=[]
    for pressure in (2e5,2e6):
        response=observe_liquid_water_response(LiquidWaterResponseRequest(state={'pressure_Pa_abs':pressure,'temperature_K':300.}))
        assert response.status=='computed'
        states.append(ChamberLiquidEndpoint(pressure_pa_abs=pressure,temperature_K=300.,density_kg_m3=response.density_kg_m3,
            phase='liquid',property_basis_id=response.property_basis_id))
    q=LiquidChamberCycleRequest(suction=states[0],discharge=states[1],end_discharge_dead_volume_m3=.0001,
        swept_volume_m3=.001,slip_fraction_of_swept_suction_volume=0.,
        single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed=True,common_fluid_and_slip_reference_reviewed=True)
    result=evaluate_liquid_chamber_cycle(q)
    assert result.state=='computed'
    inventory=states[0].density_kg_m3*.0011-states[1].density_kg_m3*.0001
    assert result.net_transferred_mass_kg_per_cycle==pytest.approx(inventory,rel=2e-14,abs=0)
    assert result.net_discharge_reference_volume_m3_per_cycle*states[1].density_kg_m3==pytest.approx(inventory,rel=2e-14,abs=0)


def test_actual_dc_current_handoff_to_explicit_protective_demand_owner_budget(record_property):
    record_property('canonical_units',json.dumps(['proposal.electrical.cp_current_voltage_balance','proposal.electrochemistry.anode_charge_lifetime']))
    record_property('test_kind','integration')
    from copy import deepcopy
    from pipeline_integrity.dc_tapped_cable import observe_dc_ohmic_drive
    from pipeline_integrity.anode_lifetime import evaluate_anode_lifetime
    from properties_pipeline_cases_223 import FIRST,context_for,owner_client
    cls,data=FIRST['cp_drive'];drive=cls.model_validate(data);current=observe_dc_ohmic_drive(drive).signed_current_A
    assert current>0
    cls,data=FIRST['anode'];data=deepcopy(data)
    data['budget']['parameters'].update(bare_surface_fraction=[0.,0.],bare_surface_current_density_A_m2=[0.,0.],
        additional_current_drain_A=[current,current],single_anode_mass_kg=.01)
    request=cls.model_validate(data)
    # Reviewer declares positive circuit current to be this scenario's protective demand;
    # potential protection is deliberately not inferred by this connection.
    result=evaluate_anode_lifetime(request,context_for(request,'current_to_anode'),client=owner_client())
    assert result['status']=='computed'
    assert result['demand_charge_C']==pytest.approx(current*10,rel=2e-14,abs=0)
    assert result['consumed_charge_C']==pytest.approx(min(current*10,8.),rel=2e-14,abs=0)
    assert result['charge_deficit_C']==pytest.approx(max(current*10-8.,0.),rel=2e-14,abs=0)


def test_saturated_dew_observation_feeds_actual_wet_to_dry_reference_volume(record_property):
    record_property('canonical_units',json.dumps(['proposal.moisture.dewpoint_relative_humidity','proposal.mixture.water_saturated_gas']))
    record_property('test_kind','integration')
    from properties_pipeline_cases_223 import REMAINING
    from pipeline_integrity.water_vapor_observation import observe_water_vapor,moisture_gas_basis_state
    from pipeline_integrity.gas_basis import GasBasisState,GasBasisRequest,evaluate_gas_basis,REVIEWS
    from pipeline_integrity.contracts import RunContext,EvidenceRef
    cls,data,_=REMAINING['moisture.dewpoint_relative_humidity']
    q=cls.model_validate({**data,'relative_humidity_fraction':None,'saturated_at_gas_temperature':True})
    observation=observe_water_vapor(q)
    assert observation.status=='computed'
    wet=moisture_gas_basis_state(observation,id='wet',state_evidence_id='moisture',water_fraction_evidence_id='moisture',
        gas_phase_evidence_id='moisture',single_gas_state_reviewed=True,compressibility_Z_common_R=1.,compressibility_evidence_id='moisture')
    dry=GasBasisState.model_validate({**wet.model_dump(),'id':'dry','water_vapor_mole_fraction':0.,'volume_basis':'dry_gas'})
    request=GasBasisRequest(id='dew-to-volume',asset_id='pipe',clock_id='clock',event_id='saturation-snapshot',time_s=0.,
        quantity_kind='volume',source_value=1.,dry_composition_id='nitrogen',dry_components=({'fluid':'Nitrogen','mole_fraction':1.},),
        composition_evidence_id='moisture',source=wet,target=dry,Z_mode='supplied',reviews=dict.fromkeys(REVIEWS,True),
        review_evidence_ids=dict.fromkeys(REVIEWS,'moisture'),evidence_ids=('moisture',))
    context=RunContext(asset_id='pipe',clock_id='clock',scenario_id='dew-to-volume',origin='simulation',
        evidence=(EvidenceRef(id='moisture',citation='Actual saturated dew observation and explicitly supplied ideal gas basis',origin='simulation',
            sha256=digest({'request':q,'observation':observation})),))
    result=evaluate_gas_basis(request,context)
    assert result['status']=='computed'
    assert result['values']['target_value']==pytest.approx(1-wet.water_vapor_mole_fraction,rel=2e-14,abs=0)
