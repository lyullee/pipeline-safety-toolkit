"""Independent weighed parcels, manufactured excess volume and reference states."""
import math
import pytest
from pipeline_integrity.component_mass_reference import (MassMeterFlowingState as State,
    LiquidEquivalentReference as Ref,MassPartitionComponent as Component,
    ComponentMassReferenceRequest as Request,observe_component_mass_reference as solve)


N=(4.,3.,5.);M=(.03,.044,.058);RHO=(500.,600.,700.)
MASSES=tuple(n*m for n,m in zip(N,M));TOTAL=sum(MASSES)
PURE_V=tuple(m/r for m,r in zip(MASSES,RHO));SUM_V=sum(PURE_V)


def state(**changes):
    return State(**(dict(asset_id='meter',stream_id='whole-NGL',state_id='flowing-state',pressure_Pa_abs=2e6,
        temperature_K=300.,clock_id='observation-clock',observation_time_s=12.)|changes))


def request(excess=-.08,kind='volume',**changes):
    # A freely authored volume-of-mixing fixture, not an EOS/property fit.
    pairs=PURE_V[0]*PURE_V[1]+PURE_V[1]*PURE_V[2]+PURE_V[2]*PURE_V[0]
    actual=SUM_V+excess*pairs/SUM_V;factor=1.008
    components=tuple(Component(id=str(i),fraction=n/sum(N),molar_mass_kg_mol=m,
        liquid_equivalent_reference=Ref(kind='declared_liquid_equivalent_convention',reference_basis_id=f'supplied-pure-reference-{i}',
            reporting_set_id='shared-reference-volume-set',density_kg_m3=r,density_reference_applicability_reviewed=True)) for i,(n,m,r) in enumerate(zip(N,M,RHO)))
    args=dict(quantity_kind=kind,metered_actual_volume_quantity=actual/factor,supplied_meter_factor=factor,
        meter_factor_basis_id='given-measured-meter-factor',measured_bulk_density_kg_m3=TOTAL/actual,
        volume_state=state(),density_state=state(),fraction_basis='mole',components=components,
        composition_sample_id='composite-sample',composition_basis_id='representative-whole-stream',
        reference_reporting_set_id='shared-reference-volume-set',
        actual_meter_factor_density_and_composite_composition_basis_reviewed=True)
    args.update(changes);return Request(**args)


@pytest.mark.parametrize('excess',[-.08,0.,.06])
@pytest.mark.parametrize('kind',['volume','volume_flow'])
def test_independently_weighed_species_and_moles_recovered_with_nonadditive_bulk_volume(excess,kind):
    q=request(excess,kind);a=solve(q);assert a.status=='computed'
    assert a.total_mass_quantity==pytest.approx(TOTAL,rel=3e-15)
    assert a.total_molar_quantity==pytest.approx(sum(N),rel=3e-15)
    assert [c.mass_quantity for c in a.components]==pytest.approx(MASSES,rel=3e-15)
    assert [c.molar_quantity for c in a.components]==pytest.approx(N,rel=3e-15)
    assert [c.reference_equivalent_volume_quantity for c in a.components]==pytest.approx(PURE_V,rel=3e-15)
    assert a.sum_reference_equivalent_volume_quantity==pytest.approx(SUM_V,rel=3e-15)
    assert [c.reference_equivalent_volume_fraction for c in a.components]==pytest.approx([v/SUM_V for v in PURE_V],rel=3e-15)
    if excess!=0:
        assert not math.isclose(a.sum_reference_equivalent_volume_quantity,a.corrected_actual_volume_quantity,rel_tol=1e-3)
    assert a.bulk_volume_equals_reference_volume_sum_assumed is False
    assert a.actual_component_volume_fractions_inferred is False
    assert abs(a.mass_partition_residual)<1e-15 and abs(a.molar_partition_residual)<1e-14
    assert a.mass_quantity_unit==('kg' if kind=='volume' else 'kg/s')
    assert a.molar_quantity_unit==('mol' if kind=='volume' else 'mol/s')


def test_measured_mass_fraction_coordinate_reconstructs_counted_moles_without_gas_EOS():
    q=request();components=tuple(c.model_copy(update={'fraction':m/TOTAL}) for c,m in zip(q.components,MASSES))
    a=solve(q.model_copy(update={'fraction_basis':'mass','components':components}))
    assert [c.mole_fraction for c in a.components]==pytest.approx([n/sum(N) for n in N],rel=3e-15)
    assert [c.mass_quantity for c in a.components]==pytest.approx(MASSES,rel=3e-15)
    assert a.EOS_or_component_density_predicted is False


def test_changed_actual_PT_and_measured_density_preserve_mass_without_reference_volume_additivity():
    q=request();a=solve(q);new_state=state(state_id='second-condition',pressure_Pa_abs=5e6,temperature_K=330.)
    b=solve(q.model_copy(update={'volume_state':new_state,'density_state':new_state,
        'metered_actual_volume_quantity':q.metered_actual_volume_quantity*1.05,
        'measured_bulk_density_kg_m3':q.measured_bulk_density_kg_m3/1.05}))
    assert b.total_mass_quantity==pytest.approx(a.total_mass_quantity,rel=3e-15)
    assert b.corrected_actual_volume_quantity==pytest.approx(a.corrected_actual_volume_quantity*1.05)
    assert b.sum_reference_equivalent_volume_quantity==pytest.approx(a.sum_reference_equivalent_volume_quantity)


@pytest.mark.parametrize('change',[{'pressure_Pa_abs':2.1e6},{'temperature_K':301.},{'state_id':'different-state'},
    {'asset_id':'another-meter'},{'stream_id':'fractionated-stream'},{'clock_id':'unreconciled-clock'},
    {'observation_time_s':13.}])
def test_meter_density_PT_provenance_mismatch_not_silently_compensated(change):
    a=solve(request(density_state=state(**change)))
    assert a.status=='out_of_scope' and a.total_mass_quantity is None


@pytest.mark.parametrize('field',['metered_actual_volume_quantity','supplied_meter_factor','meter_factor_basis_id','measured_bulk_density_kg_m3'])
def test_unknown_meter_density_or_factor_not_filled_by_a_reference_state(field):
    a=solve(request(**{field:None}));assert a.status=='needs_data' and not a.components


def test_unknown_active_molar_mass_and_unreviewed_composite_sample_are_not_inferred():
    q=request();components=(q.components[0].model_copy(update={'molar_mass_kg_mol':None}),*q.components[1:])
    assert solve(q.model_copy(update={'components':components})).status=='needs_data'
    assert solve(request(actual_meter_factor_density_and_composite_composition_basis_reviewed=False)).status=='needs_data'


@pytest.mark.parametrize('reference',[None,
    Ref(kind='declared_liquid_equivalent_convention',reference_basis_id='unknown-property',density_kg_m3=None,density_reference_applicability_reviewed=True),
    Ref(kind='declared_liquid_equivalent_convention',reference_basis_id='unreviewed-property',density_kg_m3=500.),
    Ref(kind='actual_single_liquid_state',reference_basis_id='missing-actual-PT',density_kg_m3=500.,density_reference_applicability_reviewed=True)])
def test_unknown_reference_density_or_phase_basis_retains_known_component_mass(reference):
    q=request();cs=(q.components[0].model_copy(update={'liquid_equivalent_reference':reference}),*q.components[1:])
    a=solve(q.model_copy(update={'components':cs}))
    assert a.status=='needs_data' and a.mass_partition_status=='computed'
    assert a.components[0].mass_quantity==pytest.approx(MASSES[0])
    assert a.components[0].reference_equivalent_volume_quantity is None
    assert a.sum_reference_equivalent_volume_quantity is None
    assert all(c.reference_equivalent_volume_fraction is None for c in a.components)


def test_actual_single_liquid_reference_and_conventional_equivalent_are_separate_coordinates():
    q=request();ref=Ref(kind='actual_single_liquid_state',reference_basis_id='reviewed-pure-liquid-state',
        reporting_set_id='shared-reference-volume-set',density_kg_m3=450.,pressure_Pa_abs=3e6,temperature_K=290.,density_reference_applicability_reviewed=True)
    cs=(q.components[0].model_copy(update={'liquid_equivalent_reference':ref}),*q.components[1:])
    a=solve(q.model_copy(update={'components':cs}))
    assert a.status=='computed' and a.components[0].reference_equivalent_volume_quantity==pytest.approx(MASSES[0]/450)
    assert a.components[0].liquid_equivalent_reference.kind=='actual_single_liquid_state'
    assert a.components[1].liquid_equivalent_reference.kind=='declared_liquid_equivalent_convention'


def test_mass_only_request_does_not_need_unrequested_liquid_reference_data():
    q=request();cs=tuple(c.model_copy(update={'liquid_equivalent_reference':None}) for c in q.components)
    a=solve(q.model_copy(update={'components':cs,'component_reference_volumes_requested':False}))
    assert a.status=='computed' and a.reference_volume_status=='not_requested'
    assert a.sum_reference_equivalent_volume_quantity is None


def test_known_zero_quantity_retains_composition_and_zero_conserved_amounts():
    a=solve(request(metered_actual_volume_quantity=0.))
    assert a.status=='computed' and a.total_mass_quantity==a.total_molar_quantity==0
    assert a.sum_reference_equivalent_volume_quantity==0
    assert [c.mole_fraction for c in a.components]==pytest.approx([n/sum(N) for n in N])
    assert [c.reference_equivalent_volume_fraction for c in a.components]==pytest.approx([v/SUM_V for v in PURE_V])


@pytest.mark.parametrize('scale',[.001,19.])
def test_meter_quantity_scaling_and_component_permutation(scale):
    q=request();a=solve(q);b=solve(q.model_copy(update={'metered_actual_volume_quantity':q.metered_actual_volume_quantity*scale,'components':q.components[::-1]}))
    assert b.total_mass_quantity==pytest.approx(a.total_mass_quantity*scale)
    keyed={c.id:c for c in b.components}
    for c in a.components:assert keyed[c.id].mass_quantity==pytest.approx(c.mass_quantity*scale)


def test_reconciled_clock_shift_and_sample_identity_survive_without_numeric_relabeling():
    q=request();shift=state(observation_time_s=212.);a=solve(q.model_copy(update={'volume_state':shift,'density_state':shift,'composition_sample_id':'new-composite'}))
    assert a.volume_state.observation_time_s==212. and a.composition_sample_id=='new-composite'
    assert a.total_mass_quantity==pytest.approx(TOTAL)


@pytest.mark.parametrize('change',[{'metered_actual_volume_quantity':-1.},{'supplied_meter_factor':0.},
    {'measured_bulk_density_kg_m3':0.},{'measured_bulk_density_kg_m3':float('nan')}])
def test_invalid_actual_quantities_rejected(change):
    with pytest.raises(ValueError):request(**change)


def test_invalid_fraction_sum_or_duplicate_identity_is_rejected_by_existing_composition_owner():
    q=request()
    with pytest.raises(ValueError):solve(q.model_copy(update={'components':(q.components[0],)}))
    with pytest.raises(ValueError):solve(q.model_copy(update={'components':(q.components[0],q.components[0],q.components[2])}))


def test_reference_reporting_set_unknown_or_mismatched_never_normalizes_incompatible_volumes():
    q=request();a=solve(q.model_copy(update={'reference_reporting_set_id':None}))
    assert a.mass_partition_status=='computed' and a.reference_volume_status=='needs_data'
    ref=q.components[0].liquid_equivalent_reference.model_copy(update={'reporting_set_id':'different-contract-reference'})
    cs=(q.components[0].model_copy(update={'liquid_equivalent_reference':ref}),*q.components[1:])
    b=solve(q.model_copy(update={'components':cs}))
    assert b.reference_volume_status=='needs_data' and all(c.reference_equivalent_volume_fraction is None for c in b.components)


def test_reference_fractions_are_not_mole_mass_or_actual_mixture_volume_fractions():
    a=solve(request())
    phi=[c.reference_equivalent_volume_fraction for c in a.components]
    assert math.fsum(phi)==pytest.approx(1.,abs=2e-16)
    assert phi[0]!=pytest.approx(a.components[0].mole_fraction)
    assert phi[0]!=pytest.approx(a.components[0].mass_fraction)
    assert a.reference_volume_fractions_are_reporting_coordinates is True
