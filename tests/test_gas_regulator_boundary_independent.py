"""Independent steady mass-network and gas coefficient-capacity references."""
import math
import pytest
from pipeline_integrity.gas_regulator_boundary import GasRegulatorBoundaryRequest as R,GasValveCapacityPoint as C,GasExternalFlowPoint as F,solve_gas_regulator_boundary as solve
from pipeline_integrity.gas_regulator_cv_capacity import GasCvCapacityConfiguration,NativeCvGasCapacityProvider
from pipeline_integrity.models.library_models import GasControlValve

FLUID='same authored gas kg/s';CAP='actual finite maximum characteristic';EXT='actual external source/load'


def request(side='downstream',target=5e5,**kw):
    data=dict(controlled_side=side,fixed_other_side_pressure_pa_abs=6e5 if side=='downstream' else 1e5,target_controlled_pressure_pa_abs=target,saturated_controlled_pressure_bounds_pa_abs=(1.01e5,target) if side=='downstream' else (target,5.99e5),fluid_and_mass_basis_id=FLUID,valve_capacity_basis_id=CAP,external_flow_basis_id=EXT,single_gas_steady_capacity_and_external_boundary_reviewed=True,continuously_modulatable_zero_to_maximum_capacity_reviewed=True,strict_saturated_mass_residual_monotonicity_reviewed=True)
    data.update(kw);return R(**data)


def providers(side,scale=1.):
    kc=scale*1e-7;ke=scale*1.2e-7
    def capacity(up,down):return C(upstream_pressure_pa_abs=up,downstream_pressure_pa_abs=down,maximum_mass_flow_kg_s=kc*math.sqrt(up*up-down*down),fluid_and_mass_basis_id=FLUID,characteristic_basis_id=CAP)
    def external(p):
        flow=ke*math.sqrt(p*p-1e10) if side=='downstream' else ke*math.sqrt(36e10-p*p)
        return F(controlled_pressure_pa_abs=p,role='outgoing_load' if side=='downstream' else 'incoming_supply',mass_flow_kg_s=flow,fluid_and_mass_basis_id=FLUID,characteristic_basis_id=EXT)
    return capacity,external


@pytest.mark.parametrize('side',['downstream','upstream'])
@pytest.mark.parametrize('target',[2e5,4e5,5e5])
@pytest.mark.parametrize('scale',[.7,2.3])
def test_two_pressure_squared_resistances_independent_common_mass_equilibrium(side,target,scale):
    capacity,external=providers(side,scale);q=request(side,target)
    out=solve(q,capacity_provider=capacity,external_flow_provider=external);assert out.state=='computed'
    # Integrating constant-state pressure-potential momentum gives
    # delta(P^2)=R*m^2. Series resistance addition gives mass first,
    # then the shared pressure; it does not call the implementation root.
    Rc=1/(scale*1e-7)**2;Re=1/(scale*1.2e-7)**2
    m=math.sqrt((36e10-1e10)/(Rc+Re))
    natural=math.sqrt(36e10-Rc*m*m) if side=='downstream' else math.sqrt(1e10+Rc*m*m)
    controlled=target<=natural if side=='downstream' else target>=natural
    expected=target if controlled else natural
    assert out.controlled_pressure_pa_abs==pytest.approx(expected,rel=3e-12,abs=2e-6)
    assert out.operating_mode==('target_controlled' if controlled else 'maximum_capacity_saturated')
    assert out.mass_balance_residual_kg_s==pytest.approx(0.,abs=1e-12)
    assert out.valve_mass_flow_kg_s==pytest.approx(out.external_mass_flow_kg_s,abs=1e-12)
    if not controlled:
        assert out.valve_mass_flow_kg_s==pytest.approx(m,rel=3e-12)
        assert out.attained_maximum_capacity_fraction==1.
    else:assert 0<=out.attained_maximum_capacity_fraction<=1.
    assert out.required_effective_Kv is None
    assert not out.stem_position_or_thermal_dynamics_solved


def test_zero_external_flow_closes_capacity_without_inventing_a_stem_curve():
    capacity,_=providers('downstream')
    external=lambda p:F(controlled_pressure_pa_abs=p,role='outgoing_load',mass_flow_kg_s=0,fluid_and_mass_basis_id=FLUID,characteristic_basis_id=EXT)
    out=solve(request(),capacity_provider=capacity,external_flow_provider=external)
    assert out.state=='computed';assert out.valve_mass_flow_kg_s==0;assert out.attained_maximum_capacity_fraction==0


@pytest.mark.parametrize('side',['downstream','upstream'])
def test_no_feasible_root_inside_user_support_is_not_an_unlimited_pressure_boundary(side):
    cap,external=providers(side)
    q=request(side,5e5 if side=='downstream' else 2e5,saturated_controlled_pressure_bounds_pa_abs=(4.99e5,5e5) if side=='downstream' else (2e5,2.01e5))
    out=solve(q,capacity_provider=cap,external_flow_provider=external)
    assert out.state=='out_of_scope';assert out.operating_mode=='unresolved'


@pytest.mark.parametrize('field',['fluid_and_mass_basis_id','valve_capacity_basis_id','external_flow_basis_id'])
def test_unknown_characteristic_basis_not_replaced_with_a_pressure_setpoint(field):
    cap,external=providers('downstream')
    assert solve(request().model_copy(update={field:None}),capacity_provider=cap,external_flow_provider=external).state=='needs_data'


@pytest.mark.parametrize('field',['single_gas_steady_capacity_and_external_boundary_reviewed','continuously_modulatable_zero_to_maximum_capacity_reviewed','strict_saturated_mass_residual_monotonicity_reviewed'])
def test_missing_actual_domain_or_saturated_uniqueness_review_is_withheld(field):
    cap,external=providers('downstream')
    assert solve(request().model_copy(update={field:False}),capacity_provider=cap,external_flow_provider=external).state=='needs_data'


@pytest.mark.parametrize('which',['capacity','external'])
def test_unknown_trial_state_is_not_skipped_during_saturated_root(which):
    cap,external=providers('downstream')
    def capacity(up,down):return cap(up,down).model_copy(update={'maximum_mass_flow_kg_s':None}) if which=='capacity' and down<2e5 else cap(up,down)
    def load(p):return external(p).model_copy(update={'mass_flow_kg_s':None}) if which=='external' and p<2e5 else external(p)
    assert solve(request(),capacity_provider=capacity,external_flow_provider=load).state=='needs_data'


@pytest.mark.parametrize('change',[{'controlled_pressure_pa_abs':3e5},{'role':'incoming_supply'},{'fluid_and_mass_basis_id':'other fluid'},{'characteristic_basis_id':'other load'}])
def test_wrong_returned_pressure_flow_direction_or_mass_frame_is_diagnostic(change):
    cap,external=providers('downstream')
    with pytest.raises(ValueError):solve(request(),capacity_provider=cap,external_flow_provider=lambda p:external(p).model_copy(update=change))


def test_root_iteration_budget_and_pressure_support_validation():
    cap,external=providers('downstream')
    assert solve(request(maximum_root_iterations=1),capacity_provider=cap,external_flow_provider=external).state=='out_of_scope'
    with pytest.raises(ValueError):request(target=6e5,saturated_controlled_pressure_bounds_pa_abs=(1e5,6e5))
    q=request();assert R.model_validate_json(q.model_dump_json())==q


R_UNIVERSAL=8.31446261815324;M=.028965;T=300.;RHO_NORMAL=101325*M/(R_UNIVERSAL*273.15)


def native_factory(up,down,flow):
    return GasControlValve(temperature_k=T,molar_mass_kg_mol=M,viscosity_pa_s=1.8e-5,heat_capacity_ratio=1.4,compressibility=1.,inlet_pressure_pa_abs=up,outlet_pressure_pa_abs=down,standard_volume_flow_m3_s=flow,reference_temperature_k=273.15,reference_pressure_pa_abs=101325.,inlet_diameter_m=.05,outlet_diameter_m=.05,valve_diameter_m=.05,pressure_recovery_factor=.9,valve_style_factor=.7,terminal_pressure_ratio_factor=.7)


def native_provider(**kw):
    data=dict(maximum_effective_Kv=3.,normal_gas_density_kg_m3=RHO_NORMAL,upper_normal_volume_flow_support_m3_s=.2,fluid_and_mass_basis_id=FLUID,characteristic_basis_id=CAP,actual_factory_gas_properties_geometry_and_normal_state_reviewed=True,sizing_applicability_and_monotone_capacity_over_flow_support_reviewed=True)
    data.update(kw);return NativeCvGasCapacityProvider(GasCvCapacityConfiguration(**data),native_factory)


@pytest.mark.parametrize('down',[3.5e5,3e5,1e5])
def test_native_maximum_kv_inverse_against_dimensional_gas_expansion_capacity(down):
    provider=native_provider();out=provider(4e5,down)
    # Independent Kv water-reference area and ideal-gas upstream density.
    # The native normalized-volume numerical constants are rounded; this
    # dimensional reference is bounded accordingly, not asserted identical
    # to an exact experimental valve/normal-gas calibration.
    rho=4e5*M/(R_UNIVERSAL*T);x=min((4e5-down)/4e5,.7);Y=1-x/(3*.7)
    mass=3/3600*Y*math.sqrt(.01*rho*x*4e5)
    assert out.maximum_mass_flow_kg_s==pytest.approx(mass,rel=.003)
    setting=provider.required_effective_Kv(4e5,down,out.maximum_mass_flow_kg_s)
    assert setting==pytest.approx(3.,rel=2e-10)
    assert provider.native_sizing_call_count>2


def test_native_choked_capacity_plateau_and_no_guessed_normal_density():
    p=native_provider();a=p(4e5,1e5);b=p(4e5,5e4)
    assert a.maximum_mass_flow_kg_s==pytest.approx(b.maximum_mass_flow_kg_s,rel=2e-12)
    with pytest.raises(ValueError):native_provider(normal_gas_density_kg_m3=0)
    assert native_provider(actual_factory_gas_properties_geometry_and_normal_state_reviewed=False)(4e5,1e5).maximum_mass_flow_kg_s is None


@pytest.mark.parametrize('side,target,kexternal',[('downstream',2e5,1e-7),('downstream',3e5,4e-7),('upstream',5e5,1e-7),('upstream',2e5,2e-7)])
def test_actual_native_kv_capacity_with_both_control_sides_and_saturation(side,target,kexternal):
    provider=native_provider()
    fixed=4e5 if side=='downstream' else 1e5
    def external(p):
        mass=kexternal*math.sqrt(p*p-1e10) if side=='downstream' else kexternal*math.sqrt(36e10-p*p)
        return F(controlled_pressure_pa_abs=p,role='outgoing_load' if side=='downstream' else 'incoming_supply',mass_flow_kg_s=mass,fluid_and_mass_basis_id=FLUID,characteristic_basis_id=EXT)
    q=request(side,target,fixed_other_side_pressure_pa_abs=fixed)
    out=solve(q,capacity_provider=provider,external_flow_provider=external,required_effective_Kv_provider=provider.required_effective_Kv)
    assert out.state=='computed';assert abs(out.mass_balance_residual_kg_s)<1e-9
    assert 0<=out.required_effective_Kv<=3*(1+1e-8)
    assert provider.native_sizing_call_count>2
    if out.operating_mode=='maximum_capacity_saturated':assert out.required_effective_Kv==pytest.approx(3.,rel=2e-8)


def test_native_factory_cannot_change_property_state_with_trial_requested_flow():
    p=native_provider()
    def wrong(up,down,flow):return native_factory(up,down,flow).model_copy(update={'temperature_k':300+flow})
    p.request_factory=wrong
    with pytest.raises(ValueError):p(4e5,2e5)
    with pytest.raises(ValueError):native_provider(upper_normal_volume_flow_support_m3_s=1e-7)(4e5,2e5)


@pytest.mark.parametrize('side',['downstream','upstream'])
def test_saturated_pressure_root_refinement_retains_original_coarse_tolerance(side):
    cap,external=providers(side)
    target=5e5 if side=='downstream' else 2e5
    Rc=1/(1e-7)**2;Re=1/(1.2e-7)**2
    m2=(36e10-1e10)/(Rc+Re)
    truth=math.sqrt(36e10-Rc*m2) if side=='downstream' else math.sqrt(1e10+Rc*m2)
    errors=[]
    # A deliberately explicit loose mass budget lets the retained coarse
    # pressure solve report its error; all original37 use their unchanged
    # strict budgets, and the fine reference pressure must still be accurate.
    for tolerance in [1e4,10.,1e-6]:
        q=request(side,target,absolute_pressure_root_tolerance_pa=tolerance,absolute_mass_balance_tolerance_kg_s=.01,relative_mass_balance_tolerance=1e-3)
        out=solve(q,capacity_provider=cap,external_flow_provider=external)
        assert out.state=='computed';errors.append(abs(out.controlled_pressure_pa_abs-truth))
    assert errors[-1]<2e-6
    assert errors[-1]<errors[0]/1000
    assert errors[1]<errors[0]


def test_actual_existing_coolprop_factory_and_explicit_normal_mass_basis():
    pytest.importorskip('CoolProp')
    from CoolProp import AbstractState,PT_INPUTS
    import CoolProp.CoolProp as CP
    from pipeline_integrity.thermo import CoolPropProvider,StateRequest
    eos=CoolPropProvider(use_cache=False);Mreal=CP.PropsSI('M','Air')
    reference=eos.resolve(StateRequest(fluid='Air',inputs={'P':101325.,'T':273.15}))
    calls=[]
    def factory(up,down,flow):
        state=eos.resolve(StateRequest(fluid='Air',inputs={'P':up,'T':300.}));calls.append((up,down,flow))
        assert state.phase in ['gas','supercritical_gas','supercritical']
        Z=up*Mreal/(state.density_kg_m3*R_UNIVERSAL*300.)
        return native_factory(up,down,flow).model_copy(update={'molar_mass_kg_mol':Mreal,'viscosity_pa_s':state.viscosity_pa_s,'heat_capacity_ratio':state.cp_j_kg_k/state.cv_j_kg_k,'compressibility':Z})
    cfg=native_provider().configuration.model_copy(update={'normal_gas_density_kg_m3':reference.density_kg_m3})
    provider=NativeCvGasCapacityProvider(cfg,factory)
    q=request('downstream',2e5,fixed_other_side_pressure_pa_abs=4e5)
    _,external=providers('downstream')
    out=solve(q,capacity_provider=provider,external_flow_provider=external,required_effective_Kv_provider=provider.required_effective_Kv)
    assert out.state=='computed';assert out.pressure_target_attained
    assert len(calls)>2
    low=AbstractState('HEOS','Air');low.update(PT_INPUTS,101325.,273.15)
    assert reference.density_kg_m3==pytest.approx(low.rhomass(),rel=1e-13)
    assert out.required_effective_Kv<=3.
    assert out.valve_mass_flow_kg_s==pytest.approx(out.external_mass_flow_kg_s,abs=1e-12)
