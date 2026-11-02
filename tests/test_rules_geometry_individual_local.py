"""Independent SI/US-unit normalization for three individual geometry relations."""
import math

import pytest
from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.models.hydraulics import TrenchVolume,trench_volume

CTX=RunContext(asset_id="authored-uniform-section",scenario_id="unit-normalization",clock_id="static",origin="synthetic")
INCH=.0254
FOOT=.3048
POUND=.45359237
GALLON=231*INCH**3


def geometry(inner,outer,length,density):
    return evaluate("geometry.pipe",dict(inner_diameter_m=inner,outer_diameter_m=outer,
        length_m=length,wall_density_kg_m3=density),CTX).values


class TestCylinderVolume:
    @pytest.mark.parametrize("diameter_in,length_ft",[(12.0,5280.0),(4.5,100.0),(30.0,16.0)])
    def test_exact_volume_and_rounded_fill_rule_basis(self,diameter_in,length_ft):
        got=geometry(diameter_in*INCH,(diameter_in+1)*INCH,length_ft*FOOT,7850.0)
        # In inch coordinates a one-foot length contributes 12 inches; 231 in3/gallon.
        gallons=math.pi*(diameter_in/2)**2*(12*length_ft)/231
        assert got["internal_volume_m3"]/GALLON == pytest.approx(gallons,rel=2e-14)
        # The four-place unit shortcut carries a half-last-place coefficient interval.
        normalized=got["internal_volume_m3"]/GALLON/(diameter_in**2*length_ft)
        assert 0.04075 <= normalized <= 0.04085

    def test_inconsistent_inner_outer_geometry_is_refused(self):
        with pytest.raises(ValueError):geometry(.2,.1,10.0,7850.0)


class TestWallMass:
    @pytest.mark.parametrize("outer_in,thickness_in,length_ft",[(4.5,.25,10.0),(12.0,.5,100.0),(30.0,.25,1.0)])
    def test_annular_mass_matches_thickness_form_with_declared_steel_density(self,outer_in,thickness_in,length_ft):
        rho=490*POUND/FOOT**3
        got=geometry((outer_in-2*thickness_in)*INCH,outer_in*INCH,length_ft*FOOT,rho)
        # Algebra in US coordinates avoids subtraction of squared SI diameters.
        expected_lb_ft=490*math.pi*(outer_in*thickness_in-thickness_in**2)/144
        assert got["wall_mass_kg"]/POUND/length_ft == pytest.approx(expected_lb_ft,rel=2e-14)
        assert got["wall_mass_kg"]/got["wall_volume_m3"] == pytest.approx(rho,rel=2e-14)

    def test_nonpositive_density_is_refused(self):
        with pytest.raises(ValueError):geometry(.1,.12,10.0,0.0)


class TestRectangularTrench:
    @pytest.mark.parametrize("width_in,depth_in,miles",[(12.0,30.0,1.0),(42.0,60.0,.5),(24.0,48.0,2.0)])
    def test_rectangular_volume_and_declared_shortcut_rounding_bound(self,width_in,depth_in,miles):
        got=trench_volume(TrenchVolume(bottom_width_m=width_in*INCH,depth_m=depth_in*INCH,
            side_slope_horizontal_per_vertical=0.0,length_m=miles*5280*FOOT,loose_volume_ratio=1.0)).values
        # Cubic inches converted to cubic yards through 36 inches per yard.
        expected_yd3=width_in*depth_in*(miles*5280*12)/36**3
        assert got["in_place_volume_m3"]/(3*FOOT)**3 == pytest.approx(expected_yd3,rel=2e-14)
        shortcut_yd3=1.36*width_in*depth_in*miles
        # The source method explicitly states its geometric shortcut error is below 0.2%.
        assert abs(shortcut_yd3-expected_yd3)/expected_yd3 < .002

    def test_negative_trench_width_is_refused(self):
        with pytest.raises(ValueError):
            TrenchVolume(bottom_width_m=-.1,depth_m=.8,
                side_slope_horizontal_per_vertical=0.0,length_m=10.0,loose_volume_ratio=1.0)
