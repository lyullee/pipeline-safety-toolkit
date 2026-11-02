"""Authored closed forms, mixture-basis conservation and pure-species limits."""
import pytest
from pipeline_integrity.gas_mixture_viscosity import ViscosityComponent as C,GasMixtureViscosityRequest as R,evaluate_gas_mixture_viscosity as evaluate


def request(method='herning_zipperer',components=None,basis='mole',**changes):
    args=dict(components=components or (C(id='a',fraction=.6,molar_mass_kg_mol=.004,dynamic_viscosity_Pa_s=1e-5),
        C(id='b',fraction=.4,molar_mass_kg_mol=.016,dynamic_viscosity_Pa_s=2e-5)),method=method,fraction_basis=basis,
        common_temperature_K=300.,common_pressure_Pa_abs=1e5,
        low_pressure_single_gas_common_state_and_mixing_accuracy_reviewed=True)
    args.update(changes);return R(**args)


def test_herning_mass_root_binary_authored_exact_weights():
    r=evaluate(request());assert r.dynamic_viscosity_Pa_s==pytest.approx(110/7*1e-6,rel=2e-15)
    assert r.mean_molar_mass_kg_mol==pytest.approx(.0088)


def test_wilke_equal_molar_mass_unequal_viscosity_rational_reference():
    components=(C(id='a',fraction=.5,molar_mass_kg_mol=.02,dynamic_viscosity_Pa_s=4e-5),
        C(id='b',fraction=.5,molar_mass_kg_mol=.02,dynamic_viscosity_Pa_s=1e-5))
    # Equal masses give phi_ab=9/4 and phi_ba=9/16 for viscosity ratio4.
    assert evaluate(request('wilke',components)).dynamic_viscosity_Pa_s==pytest.approx(608/325*1e-5,rel=2e-15)


@pytest.mark.parametrize('method',['wilke','herning_zipperer'])
def test_mass_basis_conversion_from_independent_species_masses(method):
    q=request(method);masses=[c.fraction*c.molar_mass_kg_mol for c in q.components];total=sum(masses)
    components=tuple(c.model_copy(update={'fraction':mass/total}) for c,mass in zip(q.components,masses))
    a=evaluate(q);b=evaluate(request(method,components,'mass'))
    assert b.mole_fractions==pytest.approx(a.mole_fractions,abs=1e-15)
    assert b.dynamic_viscosity_Pa_s==pytest.approx(a.dynamic_viscosity_Pa_s,rel=2e-15)


@pytest.mark.parametrize('method',['wilke','herning_zipperer'])
def test_permutation_and_common_viscosity_scale(method):
    q=request(method);a=evaluate(q)
    assert evaluate(q.model_copy(update={'components':q.components[::-1]})).dynamic_viscosity_Pa_s==a.dynamic_viscosity_Pa_s
    scaled=tuple(c.model_copy(update={'dynamic_viscosity_Pa_s':c.dynamic_viscosity_Pa_s*7}) for c in q.components)
    assert evaluate(q.model_copy(update={'components':scaled})).dynamic_viscosity_Pa_s==pytest.approx(7*a.dynamic_viscosity_Pa_s)


@pytest.mark.parametrize('method',['wilke','herning_zipperer'])
def test_pure_limit_and_zero_fraction_unknown_component(method):
    cs=(C(id='a',fraction=1.,molar_mass_kg_mol=.02,dynamic_viscosity_Pa_s=3e-5),
        C(id='unused',fraction=0.,molar_mass_kg_mol=.03,dynamic_viscosity_Pa_s=None))
    r=evaluate(request(method,cs));assert r.dynamic_viscosity_Pa_s==pytest.approx(3e-5)
    assert r.mole_fractions==(1.,0.)
    assert not r.material_accuracy_established


def test_same_species_split_is_composition_invariant():
    cs=(C(id='a',fraction=.3,molar_mass_kg_mol=.02,dynamic_viscosity_Pa_s=3e-5),
        C(id='b',fraction=.7,molar_mass_kg_mol=.02,dynamic_viscosity_Pa_s=3e-5))
    assert evaluate(request('wilke',cs)).dynamic_viscosity_Pa_s==pytest.approx(3e-5)


def test_unknown_active_viscosity_or_review_is_withheld():
    q=request();cs=(q.components[0].model_copy(update={'dynamic_viscosity_Pa_s':None}),q.components[1])
    assert evaluate(q.model_copy(update={'components':cs})).status=='needs_data'
    assert evaluate(q.model_copy(update={'low_pressure_single_gas_common_state_and_mixing_accuracy_reviewed':False})).status=='needs_data'


def test_invalid_fraction_sums_and_duplicate_identity():
    q=request()
    with pytest.raises(ValueError):request(components=(q.components[0],))
    with pytest.raises(ValueError):request(components=(q.components[0],q.components[1].model_copy(update={'id':'a'})))
    with pytest.raises(ValueError):C(id='a',fraction=.1,molar_mass_kg_mol=0,dynamic_viscosity_Pa_s=1e-5)
