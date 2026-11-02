"""Segmented inventory conservation against ideal-gas segment states."""
import math
import pytest
from pipeline_integrity.models.thermal import InventorySegment, SegmentedInventory, segmented_inventory
from pipeline_integrity.thermo import StateRequest,ThermodynamicState
R=287.05; CP=1005.; CV=CP-R
class AnalyticGas:
 def resolve(self,q:StateRequest):
  p=q.inputs['P'];t=q.inputs['T']
  return ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=p,temperature_k=t,
   density_kg_m3=p/(R*t),enthalpy_j_kg=CP*t,entropy_j_kg_k=CP*math.log(t/300)-R*math.log(p/1e5),phase='gas',
   viscosity_pa_s=1.8e-5,cp_j_kg_k=CP,cv_j_kg_k=CV,conductivity_w_m_k=.026,
   speed_of_sound_m_s=math.sqrt(CP/CV*R*t),provider_version='analytic-reference',provider_revision='ideal-gas-v1')

def calc(rows):
 return segmented_inventory(SegmentedInventory(fluid='Air',segments=tuple(InventorySegment(**x) for x in rows),
  reference_pressure_pa_abs=101325.,reference_temperature_k=273.15),property_provider=AnalyticGas()).values


def test_segmented_inventory_mass_energy_and_enthalpy_match_segment_sum():
 rows=[{'id':'a','pressure_pa_abs':2e5,'temperature_k':300.,'internal_volume_m3':1.2},
       {'id':'b','pressure_pa_abs':4.5e5,'temperature_k':390.,'internal_volume_m3':.7},
       {'id':'c','pressure_pa_abs':7e5,'temperature_k':420.,'internal_volume_m3':2.1}]
 out=calc(rows)
 masses=[r['pressure_pa_abs']*r['internal_volume_m3']/(R*r['temperature_k']) for r in rows]
 expected_mass=math.fsum(masses)
 expected_energy=math.fsum(m*CV*r['temperature_k'] for m,r in zip(masses,rows))
 expected_h=math.fsum(m*CP*r['temperature_k'] for m,r in zip(masses,rows))/expected_mass
 assert out['total_mass_kg']==pytest.approx(expected_mass,rel=2e-14)
 assert out['total_internal_energy_j']==pytest.approx(expected_energy,rel=2e-14)
 assert out['mass_weighted_enthalpy_j_kg']==pytest.approx(expected_h,rel=2e-14)
 assert out['total_internal_volume_m3']==pytest.approx(math.fsum(r['internal_volume_m3'] for r in rows),rel=2e-14)
 assert out['reference_volume_m3']==pytest.approx(expected_mass*R*273.15/101325.,rel=2e-14)


def test_segment_order_does_not_change_aggregate_conserved_totals():
 rows=[{'id':'a','pressure_pa_abs':2e5,'temperature_k':300.,'internal_volume_m3':1.2},
       {'id':'b','pressure_pa_abs':4.5e5,'temperature_k':390.,'internal_volume_m3':.7}]
 a=calc(rows); b=calc(list(reversed(rows)))
 for key in ('total_mass_kg','total_internal_energy_j','total_internal_volume_m3','reference_volume_m3','mass_weighted_enthalpy_j_kg'):
  assert b[key]==pytest.approx(a[key],rel=2e-14)


def test_uniform_segment_refinement_preserves_inventory_and_energy():
 row={'pressure_pa_abs':5e5,'temperature_k':350.,'internal_volume_m3':4.0}
 coarse=calc([dict(id='whole',**row)])
 split=calc([dict(id='left',**(row|{'internal_volume_m3':1.5})),dict(id='right',**(row|{'internal_volume_m3':2.5}))])
 for key in ('total_mass_kg','total_internal_energy_j','total_internal_volume_m3','reference_volume_m3','mass_weighted_enthalpy_j_kg'):
  assert split[key]==pytest.approx(coarse[key],rel=2e-14)
