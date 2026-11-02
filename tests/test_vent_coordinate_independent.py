"""Unit/reference checks on a supplied empirical coordinate, not vent dynamics."""
from decimal import Decimal, localcontext

import pytest

from pipeline_integrity.reviewed_dynamic_correlations import DeflagrationVentCoordinateRequest as Request, evaluate_deflagration_vent_coordinate as evaluate


def request(**changes):
    values=dict(enclosure_internal_surface_area_m2=17.3,reduced_pressure_differential_pa=7100.,
        supplied_fuel_geometry_coefficient_sqrt_pa=2.93,fuel_geometry_pressure_reference_and_coefficient_units_basis_id="authored-reviewed-coordinate",
        empirical_vent_correlation_applicability_reviewed=True)
    values.update(changes);return Request(**values)


@pytest.mark.parametrize("pressure",[73.,7100.,190000.])
def test_independent_dimensioned_reference_in_us_and_si_coordinates(pressure):
    q=request(reduced_pressure_differential_pa=pressure)
    with localcontext() as ctx:
        ctx.prec=75;d=Decimal.from_float
        pa_per_psi=Decimal('6894.757293168');m2_per_ft2=Decimal('.3048')**2
        area_ft2=d(q.enclosure_internal_surface_area_m2)/m2_per_ft2
        coefficient_psi=d(q.supplied_fuel_geometry_coefficient_sqrt_pa)/pa_per_psi.sqrt()
        pressure_psi=d(pressure)/pa_per_psi
        expected_m2=coefficient_psi*area_ft2/pressure_psi.sqrt()*m2_per_ft2
    r=evaluate(q)
    assert r['status']=='computed'
    assert r['vent_area_coordinate_m2']==pytest.approx(float(expected_m2),rel=3e-15)
    assert not r['fuel_calibration_dynamic_venting_or_code_acceptance_qualified']


def test_pressure_area_coefficient_scaling_and_dimensionless_identity():
    q=request();base=evaluate(q)['vent_area_coordinate_m2']
    assert evaluate(request(reduced_pressure_differential_pa=28400.))['vent_area_coordinate_m2']==pytest.approx(base/2)
    assert evaluate(request(enclosure_internal_surface_area_m2=34.6))['vent_area_coordinate_m2']==pytest.approx(base*2)
    assert base**2*q.reduced_pressure_differential_pa==pytest.approx(q.supplied_fuel_geometry_coefficient_sqrt_pa**2*q.enclosure_internal_surface_area_m2**2)


def test_missing_calibration_or_review_and_nonfinite_derived_area():
    assert evaluate(request(supplied_fuel_geometry_coefficient_sqrt_pa=None))['status']=='needs_data'
    assert evaluate(request(empirical_vent_correlation_applicability_reviewed=False))['status']=='needs_data'
    assert evaluate(request(enclosure_internal_surface_area_m2=1e308,supplied_fuel_geometry_coefficient_sqrt_pa=1e308))['status']=='out_of_scope'
