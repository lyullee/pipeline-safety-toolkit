"""Source volume-recipe bridge to a declared modern SI viscosity estimate.

This deliberately implements no instrument-efflux conversion or legacy SUS law.
"""
from decimal import Decimal
import json
from pathlib import Path
import pytest
from pipeline_integrity.kinematic_viscosity_blend import (
    KinematicViscosityBlendRequest as Request,ViscosityBlendComponent as Component,
    evaluate_kinematic_viscosity_blend as evaluate,
)

DATA=Path(__file__).resolve().parents[1]/'data/reference/viscosity_blends'
REF=json.loads((DATA/'energies-2026-4711-refutas-predictions.json').read_text())['rows']
MEASURED=json.loads((DATA/'energies-2026-4711-selected.json').read_text())['observations']


def interval(text):
    d=Decimal(text);half=Decimal(5).scaleb(d.as_tuple().exponent-1)
    return float(d-half),float(d+half)


def component(name,fraction,nu=1e-5,rho=800.,T=313.15,P=101325.):
    return Component(id=name,fraction=fraction,temperature_K=T,pressure_Pa_abs=P,
        dynamic_viscosity_Pa_s=None if nu is None or rho is None else nu*rho,
        density_kg_m3=rho,property_origin='explicit-same-state-measurement')


def request(parts,T=313.15,P=101325.,**kw):
    return Request(components=parts,fraction_basis='charged_component_volume',temperature_K=T,
        pressure_Pa_abs=P,state_id='source-volume-modern-SI-bridge',
        homogeneous_newtonian_liquid_reviewed=kw.pop('homogeneous_newtonian_liquid_reviewed',True),**kw)


@pytest.mark.parametrize('oil',['SAE30','SAE40'])
@pytest.mark.parametrize('T',[313.15,353.15])
def test_independent_author_prediction_from_reference_volume_and_dynamic_inputs(oil,T):
    # A priori fixed2×2 cross product, not selected from prediction residuals.
    row=next(r for r in REF if r['oil_grade']==oil and r['temperature_K']==T and r['diesel_mass_fraction']==.5)
    rho_o=next(r['density_kg_m3'] for r in MEASURED if r['oil_grade']==oil and r['temperature_K']==T and r['diesel_mass_fraction']==0)
    rho_d=next(r['density_kg_m3'] for r in MEASURED if r['oil_grade']==oil and r['temperature_K']==T and r['diesel_mass_fraction']==1)
    # Weigh0.5kg of each constituent. Independently observed stock densities
    # determine charged volumes; no additive final blend volume is assumed.
    vo=.5/rho_o;vd=.5/rho_d;fo=vo/(vo+vd);fd=vd/(vo+vd)
    outputs=[]
    for bound in [0,1]:
        nu_o=interval(row['published_oil_kinematic_viscosity_cSt_decimal'])[bound]*1e-6
        nu_d=interval(row['published_diesel_kinematic_viscosity_cSt_decimal'])[bound]*1e-6
        rho_actual=interval(row['published_density_kg_m3_decimal'])[bound]
        q=request((component('oil',fo,nu_o,rho_o,T),component('diesel',fd,nu_d,rho_d,T)),T=T,
            actual_mixture_density_kg_m3=rho_actual,measured_blend_applicability_evidence='doi:10.3390/en19194711:independent-author-outputs')
        result=evaluate(q);assert result.status=='computed'
        assert result.mass_fractions==pytest.approx({'oil':.5,'diesel':.5},abs=2e-15)
        assert result.component_kinematic_viscosities_m2_s==pytest.approx({'oil':nu_o,'diesel':nu_d})
        assert not result.field_or_standard_qualified
        assert not any('SUS' in name.upper() for name in result.model_fields)
        outputs.append(result.blended_dynamic_viscosity_Pa_s)
    lo,hi=[v*1e-3 for v in interval(row['published_refutas_dynamic_viscosity_mPa_s_decimal'])]
    # These are published output rounding intervals, not a fitted accuracy band.
    assert outputs[0]<=hi+2e-14 and outputs[1]>=lo-2e-14


def test_independent_component_mass_inventory_and_separate_actual_blend_density():
    volumes=(.01,.02,.005);densities=(600.,800.,1000.);viscosities=(2e-6,10e-6,70e-6)
    total_v=sum(volumes);masses=[v*r for v,r in zip(volumes,densities)];total_m=sum(masses)
    q=request(tuple(component(str(i),v/total_v,nu,rho) for i,(v,rho,nu) in enumerate(zip(volumes,densities,viscosities))),actual_mixture_density_kg_m3=912.)
    result=evaluate(q)
    assert result.mass_fractions==pytest.approx({str(i):m/total_m for i,m in enumerate(masses)})
    assert result.blended_dynamic_viscosity_Pa_s==pytest.approx(result.blended_kinematic_viscosity_m2_s*912.)
    assert 912.!=pytest.approx(total_m/total_v)


def test_source_volume_recipe_with_unknown_density_stays_unknown():
    q=request((component('a',.5,rho=None),component('b',.5)))
    result=evaluate(q)
    assert result.status=='needs_data' and result.blended_kinematic_viscosity_m2_s is None


def test_common_state_density_and_dynamic_viscosity_cannot_be_shifted():
    for change in [{'temperature_K':323.15},{'pressure_Pa_abs':202650.}]:
        a=component('a',.5).model_copy(update=change)
        with pytest.raises(ValueError,match='share'):
            evaluate(request((a,component('b',.5))))


def test_no_unsupported_SUS_input_or_double_log_domain_extrapolation():
    a=component('a',1.)
    raw=a.model_dump();raw['efflux_time_SUS_s']=45.
    with pytest.raises(ValueError):Component.model_validate(raw)
    with pytest.raises(ValueError,match='0.2 cSt'):
        evaluate(request((component('a',1.,nu=.2e-6),)))


def test_zero_absent_constituent_does_not_invent_density_or_viscosity():
    z=Component(id='unobserved-absent',fraction=0.,temperature_K=313.15,pressure_Pa_abs=101325.,property_origin='absent-in-source-recipe')
    a=component('present',1.,nu=7e-6,rho=900.)
    result=evaluate(request((z,a)))
    assert result.status=='computed' and result.mass_fractions['unobserved-absent']==0
    assert result.blended_kinematic_viscosity_m2_s==pytest.approx(7e-6)
    assert result.blended_dynamic_viscosity_Pa_s is None


def test_source_approximation_selection_does_not_establish_material_applicability():
    parts=(component('a',.4),component('b',.6,nu=8e-5))
    unknown=evaluate(request(parts,homogeneous_newtonian_liquid_reviewed=False))
    assert unknown.status=='needs_data'
    computed=evaluate(request(parts))
    assert computed.status=='computed' and computed.issues
    assert not computed.field_or_standard_qualified
