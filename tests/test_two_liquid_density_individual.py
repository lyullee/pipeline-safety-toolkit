"""Independent mass/volume references and inverse identifiability checks."""
import pytest

from pipeline_integrity.liquid_mixture import TwoLiquidMixtureRequest as Request
from pipeline_integrity.liquid_mixture import evaluate_two_liquid_mixture as evaluate


def calc(mode, **kwargs):
    return evaluate(Request(mode=mode, additive_volumes_and_common_state_reviewed=True, **kwargs))


@pytest.mark.parametrize("a,b,va,vb", [(730.,910.,1.,3.), (998.,785.,3.,2.), (850.,850.,1.,4.)])
def test_forward_density_is_component_mass_over_additive_volume(a,b,va,vb):
    result=calc("density_from_fraction",first_density_kg_m3=a,second_density_kg_m3=b,
                first_volume_fraction=va/(va+vb))
    assert result.status=="computed"
    assert result.mixture_density_kg_m3==pytest.approx((a*va+b*vb)/(va+vb),rel=2e-15)


@pytest.mark.parametrize("a,b", [(730.,910.), (998.,785.)])
@pytest.mark.parametrize("fraction", [0.,0.2,0.75,1.])
def test_inverse_recovers_authored_volume_fractions_and_component_swap(a,b,fraction):
    density=fraction*a+(1-fraction)*b
    result=calc("fraction_from_density",first_density_kg_m3=a,second_density_kg_m3=b,
                mixture_density_kg_m3=density)
    swapped=calc("fraction_from_density",first_density_kg_m3=b,second_density_kg_m3=a,
                 mixture_density_kg_m3=density)
    assert result.status=="computed"
    assert result.first_volume_fraction==pytest.approx(fraction,abs=2e-15)
    assert swapped.first_volume_fraction==pytest.approx(result.second_volume_fraction,abs=2e-15)


@pytest.mark.parametrize("density", [729.9,910.1])
def test_extrapolation_is_not_clipped_into_a_physical_fraction(density):
    result=calc("fraction_from_density",first_density_kg_m3=730,second_density_kg_m3=910,
                mixture_density_kg_m3=density)
    assert result.status=="out_of_scope"
    assert result.first_volume_fraction is None
    assert result.mixture_density_kg_m3==density


@pytest.mark.parametrize("a,b,limit", [(850.,850.,0.), (850.,850.5,1.)])
def test_unresolved_density_contrast_does_not_invent_fractions(a,b,limit):
    result=calc("fraction_from_density",first_density_kg_m3=a,second_density_kg_m3=b,
                mixture_density_kg_m3=(a+b)/2,minimum_identifiable_density_contrast_kg_m3=limit)
    assert result.status=="non_identifiable"
    assert result.first_volume_fraction is None


@pytest.mark.parametrize("mode", ["density_from_fraction", "fraction_from_density"])
def test_missing_data_or_unreviewed_state_is_withheld(mode):
    result=calc(mode,first_density_kg_m3=800,second_density_kg_m3=None)
    assert result.status=="needs_data"
    result=evaluate(Request(mode=mode,first_density_kg_m3=800,second_density_kg_m3=900,
                           first_volume_fraction=.5,mixture_density_kg_m3=850))
    assert result.status=="needs_data"
