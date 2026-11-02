"""Independent component inventory oracles; no embedded fluid correlations."""
from dataclasses import dataclass
import math
import pytest
from pipeline_integrity.offshore_inventory_release import (
    OffshoreInventoryRequest, OffshorePhaseState, evaluate_offshore_inventory_release,
)


def phase(pressure, temperature, bo, bg, rs, gor, oil=800., gas=1.):
    # Construct phase state from a unit stock-tank oil parcel, retaining dissolved
    # gas mass in its liquid phase and free gas in its own actual volume.
    liquid_volume = bo
    free_gas_volume = bg*(gor-rs)
    return OffshorePhaseState(pressure_Pa=pressure, temperature_K=temperature,
        oil_density_kg_m3=(oil+rs*gas)/liquid_volume,
        free_gas_density_kg_m3=gas/bg,
        oil_volume_fraction=liquid_volume/(liquid_volume+free_gas_volume),
        composition_id="synthetic parcel A", property_basis_id="reviewed closure A")


def request(gor=50., rs_i=40., rs_f=10., bo_i=1.1, bo_f=1.02, bg_i=.004, bg_f=.02):
    initial=phase(4e6,310.,bo_i,bg_i,rs_i,gor)
    ambient=phase(1e6,290.,bo_f,bg_f,rs_f,gor)
    return OffshoreInventoryRequest(pipe_volume_m3=200.,initial_pressure_Pa=4e6,
        initial_temperature_K=310.,ambient_pressure_Pa=1e6,ambient_temperature_K=290.,
        composition_id="synthetic parcel A", property_basis_id="reviewed closure A",
        reference_volume_basis="oil/gas both at declared 288.15K and101325Pa",
        reference_oil_density_kg_m3=800.,reference_gas_density_kg_m3=1.,
        reference_gas_oil_volume_ratio=gor,pre_shutin_oil_reference_flow_m3_s=.015,
        time_until_shutin_s=20.,initial_state=initial,ambient_state=ambient,
        equilibrium_phase_states_and_common_pvt_basis_reviewed=True,
        released_material_has_reference_gas_oil_ratio_reviewed=True,
        single_horizontal_full_bore_segment_reviewed=True)


@pytest.mark.parametrize("gor,ri,rf,bi,bf,gi,gf",[
    (50.,40.,10.,1.1,1.02,.004,.02), (100.,80.,20.,1.2,1.03,.003,.02),
    (20.,10.,0.,1.02,1.,.01,.1), (0.,0.,0.,.98,1.,.01,.02),
    (1e6,9e5,1e5,2.,1.1,1e-5,.01),
])
def test_component_parcels_determine_independent_reference_volume(gor,ri,rf,bi,bf,gi,gf):
    q=request(gor,ri,rf,bi,bf,gi,gf);r=evaluate_offshore_inventory_release(q)
    initial_oil_parcels=200./(bi+gi*(gor-ri))
    final_oil_parcels=200./(bf+gf*(gor-rf))
    released_parcels=initial_oil_parcels-final_oil_parcels
    assert r.status=="computed"
    assert r.released_inventory_oil_reference_volume_m3==pytest.approx(released_parcels,rel=2e-13)
    assert r.released_inventory_gas_reference_volume_m3==pytest.approx(released_parcels*gor,rel=2e-13)
    assert r.released_inventory_mass_kg==pytest.approx(released_parcels*(800+gor),rel=2e-13)
    assert r.released_inventory_oil_mass_kg+r.released_inventory_gas_mass_kg==pytest.approx(r.released_inventory_mass_kg)
    assert r.initial_mass_kg==pytest.approx(r.ambient_remaining_mass_kg+r.released_inventory_mass_kg)
    assert r.total_oil_reference_release_m3==pytest.approx(released_parcels+.3)
    assert r.phase_property_estimation_performed is False
    assert r.time_resolved_discharge_computed is False


def test_equal_states_zero_inventory_release_and_immediate_shutin():
    q=request();q=q.model_copy(update=dict(initial_state=q.ambient_state,
        initial_pressure_Pa=q.ambient_pressure_Pa,initial_temperature_K=q.ambient_temperature_K,
        time_until_shutin_s=0.))
    r=evaluate_offshore_inventory_release(q)
    assert r.released_inventory_mass_kg==0.
    assert r.total_oil_reference_release_m3==0.


@pytest.mark.parametrize("scale",[.001,2.,1e5])
def test_extensive_volume_scaling(scale):
    q=request();base=evaluate_offshore_inventory_release(q)
    r=evaluate_offshore_inventory_release(q.model_copy(update=dict(pipe_volume_m3=q.pipe_volume_m3*scale,
        pre_shutin_oil_reference_flow_m3_s=q.pre_shutin_oil_reference_flow_m3_s*scale)))
    assert r.total_oil_reference_release_m3==pytest.approx(base.total_oil_reference_release_m3*scale)


def test_reference_volume_reexpression_preserves_masses():
    q=request();base=evaluate_offshore_inventory_release(q)
    # Re-express gas reference volume with factor7 and oil reference volume3.
    q=q.model_copy(update=dict(reference_oil_density_kg_m3=800./3,
        reference_gas_density_kg_m3=1./7,reference_gas_oil_volume_ratio=50.*7/3,
        pre_shutin_oil_reference_flow_m3_s=.015*3,reference_volume_basis="transformed basis"))
    r=evaluate_offshore_inventory_release(q)
    assert r.released_inventory_mass_kg==base.released_inventory_mass_kg
    assert r.released_inventory_oil_reference_volume_m3==pytest.approx(base.released_inventory_oil_reference_volume_m3*3)
    assert r.released_inventory_gas_reference_volume_m3==pytest.approx(base.released_inventory_gas_reference_volume_m3*7)
    assert r.released_inventory_oil_mass_kg==pytest.approx(base.released_inventory_oil_mass_kg)


@pytest.mark.parametrize("flag",["equilibrium_phase_states_and_common_pvt_basis_reviewed",
    "released_material_has_reference_gas_oil_ratio_reviewed","single_horizontal_full_bore_segment_reviewed"])
def test_review_unknown_is_withheld_without_provider_call(flag):
    class Never:
        def state(self,**kw):raise AssertionError("unreviewed request must not call provider")
    r=evaluate_offshore_inventory_release(request().model_copy(update={flag:False,"initial_state":None,"ambient_state":None}),provider=Never())
    assert r.status=="needs_data" and r.total_oil_reference_release_m3 is None


@pytest.mark.parametrize("missing",["initial_state","ambient_state"])
def test_missing_state_is_preserved(missing):
    r=evaluate_offshore_inventory_release(request().model_copy(update={missing:None}))
    assert r.status=="needs_data"
    assert r.released_inventory_mass_kg is None


def test_provider_matches_direct_states_and_receives_declared_context():
    q=request();seen=[]
    class Provider:
        def state(self,**kw):
            seen.append(kw)
            return q.initial_state if kw["pressure_Pa"]==q.initial_pressure_Pa else q.ambient_state
    r=evaluate_offshore_inventory_release(q.model_copy(update=dict(initial_state=None,ambient_state=None)),provider=Provider())
    assert r==evaluate_offshore_inventory_release(q)
    assert len(seen)==2 and all(k['composition_id']==q.composition_id for k in seen)


@pytest.mark.parametrize("field,bad",[("pressure_Pa",4e6+1),("temperature_K",311.),
    ("composition_id","other"),("property_basis_id","other")])
def test_provider_state_context_mismatch_rejected(field,bad):
    q=request();state=q.initial_state.model_copy(update={field:bad})
    with pytest.raises(ValueError):evaluate_offshore_inventory_release(q.model_copy(update={"initial_state":state}))


def test_negative_release_is_rejected_without_clamping():
    q=request();s=q.ambient_state.model_copy(update=dict(oil_density_kg_m3=1e5,oil_volume_fraction=1.))
    with pytest.raises(ValueError,match="ambient mass exceeds"):evaluate_offshore_inventory_release(q.model_copy(update={"ambient_state":s}))


@pytest.mark.parametrize("field,bad",[("pipe_volume_m3",0.),("initial_pressure_Pa",0.),
    ("reference_gas_oil_volume_ratio",-1.),("time_until_shutin_s",-1.),
    ("reference_oil_density_kg_m3",math.inf),("reference_gas_density_kg_m3",math.nan)])
def test_invalid_quantities_revalidated(field,bad):
    with pytest.raises(ValueError):evaluate_offshore_inventory_release(request().model_copy(update={field:bad}))


def test_nonoutward_pressure_rejected():
    with pytest.raises(ValueError):evaluate_offshore_inventory_release(request().model_copy(update={"ambient_pressure_Pa":5e6}))


def test_large_mass_or_reference_coordinates_rejected():
    q=request().model_copy(update={"pipe_volume_m3":1e308})
    with pytest.raises(ValueError):evaluate_offshore_inventory_release(q)
