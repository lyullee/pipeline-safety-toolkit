"""Measured parcel energy, external leakage and reference-basis checks."""
import pytest
from pipeline_integrity.compressor_external_energy import (
    ObservedEnergyPort,CompressorExternalEnergyRequest,observe_compressor_external_energy)


def port(name,mass,h,**changes):
    d=dict(id=name,signed_outward_mass_flow_kg_s=mass,specific_enthalpy_J_kg=h,
        composition_basis_id='gas',energy_reference_id='common-reference');d.update(changes)
    return ObservedEnergyPort(**d)


def request(**changes):
    d=dict(asset_id='compressor',clock_id='steady',composition_basis_id='gas',energy_reference_id='common-reference',
        ports=(port('inlet',-3.,2e5),port('delivery',2.8,3.4e5),port('external-seal-leak',.2,2.7e5)),
        signed_heat_into_fluid_W=-12000.,external_mechanical_loss_W=9000.,
        steady_no_reaction_storage_and_negligible_KE_PE_reviewed=True)
    d.update(changes);return CompressorExternalEnergyRequest(**d)


@pytest.mark.parametrize('duration',[.03,17.,900.])
def test_independent_parcel_heating_and_leak_export(duration):
    r=observe_compressor_external_energy(request())
    # Follow the delivered and leaked parcel masses from the common inlet state.
    delivered=2.8*duration*(3.4e5-2e5)
    leaked=.2*duration*(2.7e5-2e5)
    lost_to_room=12000.*duration
    mechanical_loss=9000.*duration
    assert r.status=='computed'
    assert r.required_external_shaft_input_W==pytest.approx((delivered+leaked+lost_to_room+mechanical_loss)/duration)
    assert abs(r.energy_budget_residual_W)<1e-8


@pytest.mark.parametrize('offset',[-3e7,8e8])
def test_common_enthalpy_reference_shift(offset):
    q=request();a=observe_compressor_external_energy(q)
    shifted=tuple(p.model_copy(update={'specific_enthalpy_J_kg':p.specific_enthalpy_J_kg+offset}) for p in q.ports)
    b=observe_compressor_external_energy(request(ports=shifted))
    assert b.required_external_shaft_input_W==pytest.approx(a.required_external_shaft_input_W,rel=1e-10)
    assert abs(b.enthalpy_reference_shift_sensitivity_kg_s)<1e-14


@pytest.mark.parametrize('scale',[.001,51.])
def test_extensive_flow_heat_loss_scaling(scale):
    q=request();a=observe_compressor_external_energy(q)
    ports=tuple(p.model_copy(update={'signed_outward_mass_flow_kg_s':p.signed_outward_mass_flow_kg_s*scale}) for p in q.ports)
    b=observe_compressor_external_energy(request(ports=ports,signed_heat_into_fluid_W=-12000*scale,external_mechanical_loss_W=9000*scale))
    assert b.required_external_shaft_input_W==pytest.approx(a.required_external_shaft_input_W*scale)


def test_heat_into_fluid_reduces_driver_work():
    a=observe_compressor_external_energy(request(signed_heat_into_fluid_W=0))
    b=observe_compressor_external_energy(request(signed_heat_into_fluid_W=31000))
    assert a.required_external_shaft_input_W-b.required_external_shaft_input_W==pytest.approx(31000)


def test_negative_observed_work_preserved_as_not_compressor_power_prediction():
    r=observe_compressor_external_energy(request(ports=(port('in',-2,4e5),port('out',2,2e5)),
        signed_heat_into_fluid_W=0,external_mechanical_loss_W=0))
    assert r.status=='computed' and r.required_fluid_work_input_W==-4e5


def test_zero_flow_port_may_have_unknown_enthalpy():
    q=request();r=observe_compressor_external_energy(request(ports=(*q.ports,port('isolated',0,None))))
    assert r.status=='computed'


@pytest.mark.parametrize('change',[dict(signed_heat_into_fluid_W=None),dict(external_mechanical_loss_W=None),
    dict(steady_no_reaction_storage_and_negligible_KE_PE_reviewed=False),
    dict(ports=(port('in',-1,2e5),port('out',1,None)))])
def test_unknown_terms_not_defaulted_to_zero(change):
    r=observe_compressor_external_energy(request(**change))
    assert r.status=='needs_data' and r.required_external_shaft_input_W is None


@pytest.mark.parametrize('ports',[(port('in',-3,2e5),port('out',2.8,3e5)),
    (port('one',1,2e5),port('two',2,3e5)),(port('one',0,None),port('two',0,None))])
def test_incomplete_or_zero_throughflow_mass_boundary(ports):
    r=observe_compressor_external_energy(request(ports=ports))
    assert r.status=='out_of_scope' and r.required_external_shaft_input_W is None


@pytest.mark.parametrize('change',[dict(energy_reference_id='other'),dict(composition_basis_id='other')])
def test_inconsistent_enthalpy_basis_is_not_relabelled(change):
    with pytest.raises(ValueError):request(ports=(port('in',-1,2e5),port('out',1,3e5,**change)))


def test_duplicate_port_identity_rejected():
    with pytest.raises(ValueError):request(ports=(port('same',-1,2e5),port('same',1,3e5)))
