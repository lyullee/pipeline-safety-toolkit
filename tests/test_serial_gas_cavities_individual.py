"""Independent isentropic-work nozzle flux and serial pressure references."""
import math
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq
from pipeline_integrity.serial_gas_cavities import GasCavityRestriction,SerialGasCavitiesRequest,solve_serial_gas_cavities


def tooth(name,area=1e-4,cd=.8):
    return GasCavityRestriction(id=name,actual_flow_area_m2=area,supplied_discharge_coefficient=cd)


def request(**changes):
    d=dict(upstream_pressure_Pa_abs=7e5,downstream_pressure_Pa_abs=2e5,
        common_stagnation_temperature_K=350.,molar_mass_kg_mol=.028,heat_capacity_ratio=1.4,
        restrictions=(tooth('one'),tooth('two')),property_and_coefficient_basis_id='synthetic-ideal-nozzle',
        ideal_gas_constant_cp_adiabatic_rest_cavities_and_no_bypass_reviewed=True,
        coefficient_geometry_correlation_domain_reviewed=True)
    d.update(changes);return SerialGasCavitiesRequest(**d)


def independent_capacity(up,down,area,cd,t,k,mw):
    specific_R=8.31446261815324/mw
    ratio=max(down/up,(2/(k+1))**(k/(k-1)))
    pressure=up*ratio
    # Integrate v dp along the separately defined isentrope to obtain jet energy.
    work=quad(lambda p:specific_R*t*(p/up)**((k-1)/k)/p,pressure,up,epsabs=1e-7)[0]
    static_t=t*ratio**((k-1)/k)
    density=pressure/(specific_R*static_t)
    return cd*area*density*math.sqrt(2*work)


@pytest.mark.parametrize('k,ratio',[(1.1,.6),(1.4,.2),(1.67,.8)])
def test_two_cavity_reference_from_independent_flux_root(k,ratio):
    a,b=tooth('one',2e-4,.73),tooth('two',1.3e-4,.86)
    q=request(heat_capacity_ratio=k,downstream_pressure_Pa_abs=7e5*ratio,restrictions=(a,b))
    flux=lambda up,down,r:independent_capacity(up,down,r.actual_flow_area_m2,r.supplied_discharge_coefficient,
        q.common_stagnation_temperature_K,k,q.molar_mass_kg_mol)
    mid=brentq(lambda p:flux(7e5,p,a)-flux(p,7e5*ratio,b),7e5*ratio+1e-5,7e5-1e-5,xtol=1e-6)
    expected=flux(7e5,mid,a)
    r=solve_serial_gas_cavities(q)
    assert r.status=='computed'
    # The reused library's unit coefficients have finite decimal precision.
    assert r.cavity_pressure_Pa_abs[1]==pytest.approx(mid,rel=9e-4)
    assert r.common_mass_flow_kg_s==pytest.approx(expected,rel=9e-4)
    assert r.maximum_mass_mismatch_kg_s<=1e-12+1e-8*r.common_mass_flow_kg_s


@pytest.mark.parametrize('count',[1,3,7])
def test_multiple_rest_cavities_and_common_mass(count):
    q=request(restrictions=tuple(tooth(str(i),1e-4*(1+i*.2)) for i in range(count)))
    r=solve_serial_gas_cavities(q)
    assert r.status=='computed' and len(r.cavity_pressure_Pa_abs)==count+1
    assert all(a>b for a,b in zip(r.cavity_pressure_Pa_abs,r.cavity_pressure_Pa_abs[1:]))
    assert max(r.restriction_mass_flow_kg_s)-min(r.restriction_mass_flow_kg_s)<3e-8*r.common_mass_flow_kg_s


@pytest.mark.parametrize('scale',[.03,11.])
def test_area_scaling_not_duplicate_unit_models(scale):
    a=solve_serial_gas_cavities(request())
    b=solve_serial_gas_cavities(request(restrictions=(tooth('one',1e-4*scale),tooth('two',1e-4*scale))))
    assert b.cavity_pressure_Pa_abs==pytest.approx(a.cavity_pressure_Pa_abs,rel=1e-9)
    assert b.common_mass_flow_kg_s==pytest.approx(a.common_mass_flow_kg_s*scale,rel=1e-9)


def test_choked_first_tooth_plateau_and_terminal_event():
    q=request(upstream_pressure_Pa_abs=1e6,downstream_pressure_Pa_abs=1e4,
        restrictions=(tooth('one',1e-5),tooth('two',1e-4)))
    a=solve_serial_gas_cavities(q)
    b=solve_serial_gas_cavities(q.model_copy(update={'downstream_pressure_Pa_abs':2e4}))
    assert a.status==b.status=='computed'
    assert a.restriction_is_choked==(True,True)
    assert b.common_mass_flow_kg_s==pytest.approx(a.common_mass_flow_kg_s,rel=1e-9)
    assert a.cavity_pressure_Pa_abs[1]==pytest.approx(1e5,rel=1e-8)


def test_low_mach_series_resistance_limit_converges():
    teeth=(tooth('one',1e-4,.7),tooth('two',2e-4,.8),tooth('three',1.6e-4,.9))
    equivalent=1/math.sqrt(sum(1/(t.actual_flow_area_m2*t.supplied_discharge_coefficient)**2 for t in teeth))
    errors=[]
    for fraction in (.01,.001,.0001):
        kw=dict(downstream_pressure_Pa_abs=7e5*(1-fraction))
        a=solve_serial_gas_cavities(request(restrictions=teeth,**kw))
        b=solve_serial_gas_cavities(request(restrictions=(tooth('equivalent',equivalent,1.),),**kw))
        assert a.status==b.status=='computed'
        errors.append(abs(a.common_mass_flow_kg_s/b.common_mass_flow_kg_s-1))
    assert errors[2]<errors[1]<errors[0]
    assert errors[2]<2e-4


@pytest.mark.parametrize('change',[dict(ideal_gas_constant_cp_adiabatic_rest_cavities_and_no_bypass_reviewed=False),
    dict(coefficient_geometry_correlation_domain_reviewed=False)])
def test_unknown_closure_is_not_nominal_tooth_data(change):
    r=solve_serial_gas_cavities(request(**change))
    assert r.status=='needs_data' and r.common_mass_flow_kg_s is None


def test_unconverged_iteration_budget_not_accepted():
    r=solve_serial_gas_cavities(request(restrictions=(tooth('small',1e-5),tooth('large',3e-4)),maximum_function_evaluations=1))
    assert r.status=='not_converged' and r.maximum_mass_mismatch_kg_s>0


@pytest.mark.parametrize('change',[dict(downstream_pressure_Pa_abs=7e5),dict(heat_capacity_ratio=1.),
    dict(restrictions=(tooth('same'),tooth('same')))])
def test_invalid_direction_or_identity(change):
    with pytest.raises(ValueError):request(**change)
