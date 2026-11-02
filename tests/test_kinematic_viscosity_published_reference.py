"""Independent author-calculated Refutas outputs; not field acceptance tests."""
from decimal import Decimal
import json
from pathlib import Path

import pytest

from pipeline_integrity.kinematic_viscosity_blend import (
    KinematicViscosityBlendRequest as Request, ViscosityBlendComponent as Component,
    evaluate_kinematic_viscosity_blend as evaluate,
)

DATA=Path(__file__).resolve().parents[1]/'data/reference/viscosity_blends'
REFERENCE=json.loads((DATA/'energies-2026-4711-refutas-predictions.json').read_text())
MEASUREMENTS=json.loads((DATA/'energies-2026-4711-selected.json').read_text())['observations']


def rounded_interval(text):
    """Half of the last displayed decimal unit, not measurement uncertainty."""
    d=Decimal(text);half=Decimal(5).scaleb(d.as_tuple().exponent-1)
    return float(d-half),float(d+half)


def prediction(row, oil_nu, diesel_nu, density):
    t=row['temperature_K'];f=row['diesel_mass_fraction']
    q=Request(components=tuple(Component(id=k,fraction=w,temperature_K=t,pressure_Pa_abs=101325,
        kinematic_viscosity_m2_s=nu*1e-6,property_origin='doi:10.3390/en19194711:A2')
        for k,w,nu in [('oil',1-f,oil_nu),('diesel',f,diesel_nu)]),fraction_basis='mass',
        temperature_K=t,pressure_Pa_abs=101325,state_id='same-nominal-ambient-coordinate',
        homogeneous_newtonian_liquid_reviewed=True,actual_mixture_density_kg_m3=density)
    return evaluate(q)


@pytest.mark.parametrize('oil',['SAE30','SAE40'])
@pytest.mark.parametrize('temperature',[313.15,323.15,333.15,343.15,353.15,363.15,373.15])
def test_all_group_pointwise_published_refutas_outputs(oil,temperature):
    rows=[r for r in REFERENCE['rows'] if r['oil_grade']==oil and r['temperature_K']==temperature
        and 0<r['diesel_mass_fraction']<1]
    assert len(rows)==6
    for r in rows:
        a=r['published_oil_kinematic_viscosity_cSt_decimal'];b=r['published_diesel_kinematic_viscosity_cSt_decimal']
        rho=r['published_density_kg_m3_decimal'];p=r['published_refutas_dynamic_viscosity_mPa_s_decimal']
        lower=prediction(r,rounded_interval(a)[0],rounded_interval(b)[0],rounded_interval(rho)[0])
        upper=prediction(r,rounded_interval(a)[1],rounded_interval(b)[1],rounded_interval(rho)[1])
        # Positive viscosity-coordinate monotonicity and positive density make
        # these extremal outputs rigorous input-rounding envelopes.
        expected_low,expected_high=[v*1e-3 for v in rounded_interval(p)]
        assert lower.blended_dynamic_viscosity_Pa_s <= expected_high+2e-14,(r,lower,upper)
        assert upper.blended_dynamic_viscosity_Pa_s >= expected_low-2e-14,(r,lower,upper)
        central=prediction(r,float(a),float(b),float(rho))
        assert not central.field_or_standard_qualified


@pytest.mark.parametrize('oil',['SAE30','SAE40'])
def test_published_narrative_maximum_error_magnitude(oil):
    """Reproduce reported magnitude only; printed signed-max Eq15 is separate.

    This checks an independently published output statistic's decimal display.
    It does not impose 5.72/7.74 percent as a newly invented accuracy tolerance.
    """
    errors=[]
    for r in REFERENCE['rows']:
        if r['oil_grade']!=oil or not 0<r['diesel_mass_fraction']<1:continue
        pred=prediction(r,float(r['published_oil_kinematic_viscosity_cSt_decimal']),
            float(r['published_diesel_kinematic_viscosity_cSt_decimal']),float(r['published_density_kg_m3_decimal']))
        observed=next(m for m in MEASUREMENTS if m['oil_grade']==oil and m['temperature_K']==r['temperature_K']
            and m['diesel_mass_fraction']==r['diesel_mass_fraction'])
        # At identical positive density, relative errors in mu and nu coincide.
        err=(pred.blended_kinematic_viscosity_m2_s-observed['kinematic_viscosity_m2_s'])/observed['kinematic_viscosity_m2_s']
        errors.append(err*100)
    assert len(errors)==42
    lo,hi=rounded_interval(REFERENCE['published_narrative_maximum_error_percent'][oil])
    assert lo<=max(map(abs,errors))<=hi
    # Preserve the actual definition discrepancy instead of claiming that a
    # positive magnitude also reproduces the paper's printed signed maximum.
    assert not lo<=max(errors)<=hi


@pytest.mark.parametrize('oil',['SAE30','SAE40'])
def test_all_pure_published_endpoints_are_consistency_only(oil):
    rows=[r for r in REFERENCE['rows'] if r['oil_grade']==oil and r['diesel_mass_fraction'] in (0,1)]
    assert len(rows)==14
    for r in rows:
        a=r['published_oil_kinematic_viscosity_cSt_decimal'];b=r['published_diesel_kinematic_viscosity_cSt_decimal']
        rho=r['published_density_kg_m3_decimal'];p=r['published_refutas_dynamic_viscosity_mPa_s_decimal']
        lower=prediction(r,rounded_interval(a)[0],rounded_interval(b)[0],rounded_interval(rho)[0])
        upper=prediction(r,rounded_interval(a)[1],rounded_interval(b)[1],rounded_interval(rho)[1])
        expected_low,expected_high=[v*1e-3 for v in rounded_interval(p)]
        assert lower.blended_dynamic_viscosity_Pa_s <= expected_high+2e-14
        assert upper.blended_dynamic_viscosity_Pa_s >= expected_low-2e-14
        central=prediction(r,float(a),float(b),float(rho))
        expected_nu=float(b if r['diesel_mass_fraction']==1 else a)*1e-6
        assert central.blended_kinematic_viscosity_m2_s==pytest.approx(expected_nu,rel=2e-14)
