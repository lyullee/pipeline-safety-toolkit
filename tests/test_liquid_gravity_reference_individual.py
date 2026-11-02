"""Known reference coordinate pairs, inverse boundaries and basis withholding."""
import pytest

from pipeline_integrity.liquid_gravity_reference import LiquidGravityConversionRequest as Request
from pipeline_integrity.liquid_gravity_reference import evaluate_liquid_gravity_conversion as evaluate


@pytest.mark.parametrize("sg,api",[(1.,10.),(.8,45.375),(.5,151.5),(2/3,80.75)])
@pytest.mark.parametrize("mode",["specific_gravity_to_api","api_to_specific_gravity"])
def test_known_reference_pairs_and_inverse(mode,sg,api):
    kwargs={"specific_gravity_60F":sg} if mode=="specific_gravity_to_api" else {"api_gravity_60F":api}
    result=evaluate(Request(mode=mode,common_60F_reference_reviewed=True,**kwargs))
    assert result.status=="computed"
    assert result.specific_gravity_60F==pytest.approx(sg,rel=2e-15)
    assert result.api_gravity_60F==pytest.approx(api,rel=2e-15)


@pytest.mark.parametrize("mode",["specific_gravity_to_api","api_to_specific_gravity"])
def test_unknown_coordinate_and_unreviewed_reference_are_not_assumed(mode):
    assert evaluate(Request(mode=mode,common_60F_reference_reviewed=True)).status=="needs_data"
    result=evaluate(Request(mode=mode,specific_gravity_60F=.8,api_gravity_60F=45.375))
    assert result.status=="needs_data" and result.specific_gravity_60F is None


@pytest.mark.parametrize("api",[-131.5,-140.])
def test_nonpositive_inverse_specific_gravity_is_rejected(api):
    with pytest.raises(ValueError):
        evaluate(Request(mode="api_to_specific_gravity",api_gravity_60F=api,common_60F_reference_reviewed=True))
