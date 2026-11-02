"""Modern steady operating-geometry hose-loss purpose, with no hose fit defaults.

Actual stationary circular operating diameters, liquid properties and roughness
are supplied. This is not a nominal-to-loaded hose deformation model or an
experimental identity of a particular manufacturer friction curve.
"""
import math
import pytest
from scipy.optimize import brentq
from scipy.integrate import quad
from pipeline_integrity.liquid_path_system import LiquidPipePathElement as E,LiquidSeriesPath as P,LiquidPathSystemRequest as R,evaluate_liquid_path_system as evaluate


def request(flow,rho,mu,roughness):
    series=P(path_id='reviewed stationary operating hose string',elements=(E(diameter_m=.095,length_m=23.,roughness_m=roughness),E(diameter_m=.057,length_m=37.,roughness_m=roughness)))
    return R(paths=(series,),boundary='prescribed_total_flow',inlet_node_id='loading source',outlet_node_id='receiving port',outlet_minus_inlet_elevation_m=0.,pressure_basis='mechanical_total_pressure',density_kg_m3=rho,dynamic_viscosity_pa_s=mu,liquid_state_basis_id='supplied homogeneous actual operating temperature liquid state',total_volume_flow_m3_s=flow,no_intermediate_injection_and_common_end_nodes_reviewed=True,actual_single_liquid_state_and_all_loss_bases_reviewed=True,friction_method='Colebrook',transition_policy='withhold')


@pytest.mark.parametrize('flow',[-.045,-.0045,0.,.0045,.045])
@pytest.mark.parametrize('roughness',[0.,1e-5,2e-4])
@pytest.mark.parametrize('rho,mu',[(820.,.0012),(790.,.0007)])
def test_operating_geometry_turbulent_colebrook_wall_force_and_pressure_work(flow,roughness,rho,mu):
    q=request(flow,rho,mu,roughness);out=evaluate(q);assert out.status=='computed'
    independent=[];dissipation=0.
    for element,row in zip(q.paths[0].elements,out.paths[0].elements):
        radius=element.diameter_m/2
        area=quad(lambda r:2*math.pi*r,0,radius,epsabs=1e-15)[0]
        perimeter=quad(lambda theta:radius,0,2*math.pi,epsabs=1e-15)[0]
        velocity=flow/area;re=rho*abs(velocity)*element.diameter_m/mu
        if not flow:
            assert row.reynolds==0;independent.append(0.);continue
        assert re>4000
        # Independent monotone solve in x=1/sqrt(f); native solves in f.
        root=brentq(lambda x:x+2*math.log10(roughness/element.diameter_m/3.7+2.51*x/re),1.,30.,xtol=1e-13)
        darcy=1/root**2
        signed_wall_shear=darcy*rho*velocity*abs(velocity)/8
        wall_force=quad(lambda x:signed_wall_shear*perimeter,0,element.length_m,epsabs=1e-10)[0]
        pressure=wall_force/area
        independent.append(pressure)
        dissipation+=quad(lambda x:signed_wall_shear*perimeter*velocity,0,element.length_m,epsabs=1e-10)[0]
        assert row.velocity_m_s==pytest.approx(velocity,rel=2e-15)
        assert row.darcy_friction_factor==pytest.approx(darcy,rel=3e-12)
        assert row.signed_pipe_friction_drop_pa==pytest.approx(pressure,rel=3e-12)
    assert out.common_inlet_minus_outlet_total_pressure_pa==pytest.approx(math.fsum(independent),rel=3e-12,abs=1e-12)
    assert out.total_hydraulic_dissipation_W==pytest.approx(dissipation,rel=3e-12,abs=1e-12)
    assert out.total_hydraulic_dissipation_W>=0
    assert out.absolute_pressure_phase_or_cavitation_margin_inferred is False
    assert out.field_calibration_qualified is False


def test_operating_state_unknownness_and_transition_not_guessed_from_nominal_hose():
    q=request(.0045,820.,.0012,1e-5)
    assert evaluate(q.model_copy(update={'density_kg_m3':None})).status=='needs_data'
    assert evaluate(q.model_copy(update={'dynamic_viscosity_pa_s':None})).status=='needs_data'
    assert evaluate(q.model_copy(update={'actual_single_liquid_state_and_all_loss_bases_reviewed':False})).status=='needs_data'
    e=E(diameter_m=.095,length_m=23.,roughness_m=0.)
    flow=3000*math.pi*.095*.0012/(4*820.)
    transitional=q.model_copy(update={'paths':(P(path_id='transition',elements=(e,)),),'total_volume_flow_m3_s':flow})
    assert evaluate(transitional).status=='out_of_scope'
