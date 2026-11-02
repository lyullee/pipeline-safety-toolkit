"""CoolProp interface selection, not experimental EOS/transport accuracy."""
import math

import CoolProp.CoolProp as CP
import pytest
from pydantic import ValidationError

from pipeline_integrity.npsh_thermo_provider import PureFluidSuctionProviderConfig as Config, PureFluidSuctionPropertyProvider as Provider
from pipeline_integrity.npsh_temperature_search import NPSHTemperatureSearchRequest as Request, search_minimum_available_npsh as search
from pipeline_integrity.thermo import CoolPropProvider


def config(**changes):
    values=dict(fluid='Water',backend='HEOS',reference_pressure_pa_abs=300000.,provider_basis_id='reviewed-pure-water-heos-curve')
    values.update(changes);return Config(**values)


def request(**changes):
    values=dict(temperature_partitions_K=(280.,340.),objective_partition_shapes=('monotone',),
        objective_shape_reviewed_on_all_partitions=True,property_provider_basis_id=config().provider_basis_id,
        state_range_and_constant_density_per_temperature_reviewed=True,reservoir_suction_geometry_and_actual_flow_basis_reviewed=True,
        fixed_actual_volume_flow_m3_s=1e-6,suction_diameter_m=.01,straight_suction_length_m=7.1,
        absolute_roughness_m=0.,fixed_minor_loss_coefficient=.7,reservoir_surface_pressure_pa_abs=300000.,
        reservoir_surface_above_pump_m=1.2)
    values.update(changes);return Request(**values)


@pytest.mark.parametrize('temperature',[285.,310.,335.])
def test_adapter_selects_actual_bulk_pt_transport_and_tq_saturation_coordinates(temperature):
    c=config();actual=Provider(c)(temperature)
    bulk=CP.AbstractState('HEOS','Water');bulk.update(CP.PT_INPUTS,c.reference_pressure_pa_abs,temperature)
    saturation=CP.AbstractState('HEOS','Water');saturation.update(CP.QT_INPUTS,0.,temperature)
    assert actual.phase=='liquid' and actual.temperature_K==temperature
    assert actual.property_reference_pressure_pa_abs==c.reference_pressure_pa_abs
    assert actual.density_kg_m3==pytest.approx(bulk.rhomass(),rel=5e-13)
    assert actual.dynamic_viscosity_pa_s==pytest.approx(bulk.viscosity(),rel=5e-13)
    assert actual.vapor_pressure_pa_abs==pytest.approx(saturation.p(),rel=5e-13)


def test_coolprop_end_to_end_minimum_against_low_level_properties_and_radial_head_reference():
    q=request();provider=Provider(config());reference=[]
    bulk=CP.AbstractState('HEOS','Water');sat=CP.AbstractState('HEOS','Water')
    for i in range(61):
        T=280.+i
        bulk.update(CP.PT_INPUTS,300000.,T);sat.update(CP.QT_INPUTS,0.,T)
        rho,mu=bulk.rhomass(),bulk.viscosity()
        # Independent laminar pressure-force/no-slip radial solution.
        drop=128*mu*q.straight_suction_length_m*q.fixed_actual_volume_flow_m3_s/(math.pi*q.suction_diameter_m**4)
        velocity=4*q.fixed_actual_volume_flow_m3_s/(math.pi*q.suction_diameter_m**2)
        head=(300000.-sat.p()-drop)/(rho*q.gravity_m_s2)+q.reservoir_surface_above_pump_m-.7*velocity**2/(2*q.gravity_m_s2)
        reference.append((T,head))
    assert all(b[1]<a[1] for a,b in zip(reference,reference[1:]))
    r=search(q,provider)
    assert r.status=='computed' and r.minimum_temperature_K==340.
    assert r.minimum_available_npsh_m==pytest.approx(reference[-1][1],abs=3e-11)
    assert r.minimum_state.reynolds<2040
    assert not r.cavitation_phase_change_or_vendor_acceptance_qualified


def test_mixture_prefix_and_nonliquid_bulk_are_not_silently_relabelled():
    for fluid in ('Water[.5]&Ethanol[.5]','HEOS::Water'):
        with pytest.raises(ValidationError):config(fluid=fluid)
    p=Provider(config(reference_pressure_pa_abs=100000.))
    state=p(450.)
    assert state.phase=='gas' and state.vapor_pressure_pa_abs is None
    q=request(temperature_partitions_K=(440.,460.),reservoir_surface_pressure_pa_abs=100000.)
    assert search(q,p).status!='computed'


def test_missing_native_transport_is_preserved_without_guessed_viscosity():
    class MissingTransport:
        def __init__(self):self.base=CoolPropProvider(use_cache=False)
        def resolve(self,q):return self.base.resolve(q).model_copy(update={'viscosity_pa_s':None})
    p=Provider(config(),provider=MissingTransport())
    assert p(310.).dynamic_viscosity_pa_s is None
    r=search(request(),p)
    assert r.status=='needs_data' and r.minimum_available_npsh_m is None
