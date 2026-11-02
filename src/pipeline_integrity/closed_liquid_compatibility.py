"""Quasistatic uniform liquid/pipe mass compatibility with explicit property providers."""
from __future__ import annotations
import math
from typing import Annotated, Literal, Protocol
from pydantic import Field, model_validator
from scipy.optimize import brentq
from .contracts import Contract, Name, Nonnegative, Positive
from .pipe_pressure_compliance import StrainSupport
from .pipe_thermoelastic_area import (ThermoelasticAreaRequest, ClosedEndThermoelasticRequest,
    evaluate_thermoelastic_area, evaluate_closed_end_thermoelastic_geometry)
from .thermo import CheckedProvider, CoolPropProvider, StateRequest


class TestPressureCoordinate(Contract):
    value_Pa: float
    reference: Literal['absolute','gauge']
    ambient_pressure_Pa_abs: Positive | None = None

    @model_validator(mode='after')
    def reference_defined(self):
        if (self.reference=='gauge') != (self.ambient_pressure_Pa_abs is not None):
            raise ValueError('gauge pressure requires ambient absolute pressure; absolute pressure must not add it')
        self.absolute_Pa()
        return self

    def absolute_Pa(self):
        value=self.value_Pa+(self.ambient_pressure_Pa_abs if self.reference=='gauge' else 0.)
        if not math.isfinite(value) or value<=0:raise ValueError('absolute pressure must remain finite and positive')
        return value


class TestLiquidState(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_Pa_abs: Positive
    water_temperature_K: Positive
    fluid_id: Name
    property_basis_id: Name
    density_kg_m3: Positive | None = None
    phase: Name | None = None
    isothermal_compressibility_Pa_inv: Positive | None = None
    issues: tuple[str,...] = ()


class TestPipeVolumeState(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    pressure_Pa_abs: Positive
    external_pressure_Pa_abs: Positive
    wall_temperature_K: Positive
    asset_id: Name
    geometry_basis_id: Name
    volume_m3: Positive | None = None
    geometry_receipt: dict = Field(default_factory=dict)
    issues: tuple[str,...] = ()


class TestLiquidProvider(Protocol):
    def resolve_density(self, *, pressure_Pa_abs: float, water_temperature_K: float) -> TestLiquidState: ...


class TestPipeVolumeProvider(Protocol):
    def resolve_volume(self, *, pressure_Pa_abs: float, external_pressure_Pa_abs: float,
                       wall_temperature_K: float) -> TestPipeVolumeState: ...


class CoolPropTestWaterProvider:
    """HEOS water density and isothermal derivative; no acoustic-modulus substitution."""
    fluid_id='Water'
    property_basis_id='CoolProp::HEOS::Water'

    def resolve_density(self, *, pressure_Pa_abs, water_temperature_K):
        from CoolProp.CoolProp import PropsSI
        request=StateRequest(fluid='Water',backend='HEOS',inputs={'P':pressure_Pa_abs,'T':water_temperature_K})
        common=dict(pressure_Pa_abs=pressure_Pa_abs,water_temperature_K=water_temperature_K,
                    fluid_id=self.fluid_id,property_basis_id=self.property_basis_id)
        try:
            state=CheckedProvider(CoolPropProvider(use_cache=False)).resolve(request)
            if state.phase!='liquid':
                return TestLiquidState(status='out_of_scope',phase=state.phase,
                    issues=('pure compressed liquid state required',),**common)
            derivative=float(PropsSI('d(Dmass)/d(P)|T','P',pressure_Pa_abs,'T',water_temperature_K,'HEOS::Water'))
            return TestLiquidState(status='computed',density_kg_m3=state.density_kg_m3,
                phase=state.phase,isothermal_compressibility_Pa_inv=derivative/state.density_kg_m3,**common)
        except ValueError as exc:
            return TestLiquidState(status='out_of_scope',issues=('water state unavailable: '+str(exc),),**common)


class ThermoelasticTestPipeVolume(Contract):
    """Reuse pressure/thermal geometry owners with an explicit axial volume condition."""
    template: ThermoelasticAreaRequest | ClosedEndThermoelasticRequest
    geometry_reference_volume_m3: Positive
    anchor_internal_pressure_Pa_abs: Positive
    anchor_external_pressure_Pa_abs: Positive
    asset_id: Name
    geometry_basis_id: Name
    maximum_absolute_mechanical_axial_strain: StrainSupport | None = None
    maximum_absolute_total_axial_strain: StrainSupport | None = None

    @model_validator(mode='after')
    def one_axial_support_owner(self):
        if isinstance(self.template,ClosedEndThermoelasticRequest) and (self.maximum_absolute_mechanical_axial_strain is not None or self.maximum_absolute_total_axial_strain is not None):
            raise ValueError('closed-end axial supports belong to the closed-end template; no duplicate outer limits')
        return self

    def resolve_volume(self, *, pressure_Pa_abs, external_pressure_Pa_abs, wall_temperature_K):
        q=ThermoelasticTestPipeVolume.model_validate(self.model_dump(mode='python'))
        common=dict(pressure_Pa_abs=pressure_Pa_abs,external_pressure_Pa_abs=external_pressure_Pa_abs,
            wall_temperature_K=wall_temperature_K,asset_id=q.asset_id,geometry_basis_id=q.geometry_basis_id)
        delta=(pressure_Pa_abs-q.anchor_internal_pressure_Pa_abs)-(external_pressure_Pa_abs-q.anchor_external_pressure_Pa_abs)
        closed=isinstance(q.template,ClosedEndThermoelasticRequest)
        base=q.template.unconstrained_base if closed else q.template
        pressure=base.pressure.model_copy(update={'differential_pressure_increment_pa':delta})
        base=base.model_copy(update={'pressure':pressure,'temperature_K':wall_temperature_K})
        if closed:
            geometry=evaluate_closed_end_thermoelastic_geometry(q.template.model_copy(update={'unconstrained_base':base}))
            fraction=geometry.linear_volume_fraction_increment
        else:
            geometry=evaluate_thermoelastic_area(base)
            if geometry.status!='computed':
                return TestPipeVolumeState(status=geometry.status,issues=geometry.issues,
                    geometry_receipt=geometry.model_dump(mode='json'),**common)
            if q.maximum_absolute_mechanical_axial_strain is None or q.maximum_absolute_total_axial_strain is None:
                return TestPipeVolumeState(status='needs_data',issues=('axial mechanical and total strain support required for volume',),**common)
            free=geometry.free_linear_thermal_strain
            if base.pressure.axial_condition=='zero_axial_strain':
                axial_mechanical=-free;axial_total=0.
            else:
                if base.pressure.poisson_ratio is None:
                    return TestPipeVolumeState(status='needs_data',issues=('Poisson ratio required for unconstrained axial volume response',),**common)
                axial_mechanical=-base.pressure.poisson_ratio*geometry.mechanical_hoop_strain
                axial_total=axial_mechanical+free
            if abs(axial_mechanical)>q.maximum_absolute_mechanical_axial_strain or abs(axial_total)>q.maximum_absolute_total_axial_strain:
                return TestPipeVolumeState(status='out_of_scope',issues=('reviewed axial mechanical/total strain support exceeded',),
                    geometry_receipt=geometry.model_dump(mode='json'),**common)
            fraction=geometry.linear_total_area_fraction+axial_total
        if geometry.status!='computed':
            return TestPipeVolumeState(status=geometry.status,issues=geometry.issues,
                geometry_receipt=geometry.model_dump(mode='json'),**common)
        volume=q.geometry_reference_volume_m3*(1+fraction)
        if not math.isfinite(volume) or volume<=0:raise ValueError('pipe volume exceeds positive finite support')
        return TestPipeVolumeState(status='computed',volume_m3=volume,
            geometry_receipt=geometry.model_dump(mode='json'),**common)


class ClosedLiquidCompatibilityRequest(Contract):
    asset_id: Name
    clock_id: Name
    reference_clock_id: Name
    reference_time_s: float
    target_time_s: float
    reference_pressure: TestPressureCoordinate
    pressure_bracket: tuple[TestPressureCoordinate,TestPressureCoordinate]
    reference_external_pressure_Pa_abs: Positive
    target_external_pressure_Pa_abs: Positive
    reference_water_temperature_K: Positive
    target_water_temperature_K: Positive
    reference_wall_temperature_K: Positive
    target_wall_temperature_K: Positive
    fluid_id: Name
    property_basis_id: Name
    geometry_basis_id: Name
    net_added_liquid_mass_kg: float = 0.
    absolute_pressure_tolerance_Pa: Positive = 1e-5
    relative_pressure_tolerance: Annotated[float,Field(ge=8.881784197001252e-16)] = 1e-12
    absolute_mass_tolerance_kg: Nonnegative = 1e-7
    relative_mass_tolerance: Nonnegative = 1e-10
    maximum_iterations: Annotated[int,Field(gt=0)] = 100
    uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed: bool = False
    continuous_strictly_increasing_mass_pressure_on_bracket_reviewed: bool = False

    @model_validator(mode='after')
    def context_defined(self):
        if self.clock_id!=self.reference_clock_id:raise ValueError('reference and target must share an explicitly reconciled clock')
        if self.pressure_bracket[0].absolute_Pa()>=self.pressure_bracket[1].absolute_Pa():
            raise ValueError('absolute pressure bracket must increase')
        return self


class ClosedLiquidCompatibilityResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','no_bracket','not_converged']
    asset_id: Name
    clock_id: Name
    reference_time_s: float
    target_time_s: float
    reference_mass_kg: Positive | None = None
    target_mass_kg: Positive | None = None
    pressure_Pa_abs: Positive | None = None
    pressure_increment_Pa: float | None = None
    differential_pressure_increment_Pa: float | None = None
    mass_budget_residual_kg: float | None = None
    reference_liquid_state: TestLiquidState | None = None
    target_liquid_state: TestLiquidState | None = None
    reference_pipe_state: TestPipeVolumeState | None = None
    target_pipe_state: TestPipeVolumeState | None = None
    iterations: int | None = None
    issues: tuple[str,...] = ()
    leak_or_test_acceptance_inferred: Literal[False] = False


class _Unavailable(Exception):
    def __init__(self,status,issues):self.status=status;self.issues=issues


def _resolved_inventory(q, p, water_t, wall_t, external, density_provider, volume_provider):
    liquid = density_provider.resolve_density(pressure_Pa_abs=p, water_temperature_K=water_t)
    pipe = volume_provider.resolve_volume(pressure_Pa_abs=p, external_pressure_Pa_abs=external,
                                          wall_temperature_K=wall_t)
    if not isinstance(liquid, TestLiquidState) or not isinstance(pipe, TestPipeVolumeState):
        raise TypeError('typed liquid and pipe provider states required')
    liquid = TestLiquidState.model_validate(liquid.model_dump(mode='python'))
    pipe = TestPipeVolumeState.model_validate(pipe.model_dump(mode='python'))
    if (liquid.fluid_id, liquid.property_basis_id) != (q.fluid_id, q.property_basis_id) or (pipe.asset_id, pipe.geometry_basis_id) != (q.asset_id, q.geometry_basis_id):
        raise ValueError('liquid identity/property basis or pipe asset/geometry basis mismatch')
    for actual, want in [(liquid.pressure_Pa_abs, p), (pipe.pressure_Pa_abs, p),
                         (liquid.water_temperature_K, water_t), (pipe.wall_temperature_K, wall_t),
                         (pipe.external_pressure_Pa_abs, external)]:
        if not math.isclose(actual, want, rel_tol=1e-12, abs_tol=1e-10):
            raise ValueError('provider changed the requested pressure/temperature coordinate')
    for state in (liquid, pipe):
        if state.status != 'computed':
            raise _Unavailable(state.status, state.issues)
    if liquid.phase != 'liquid':
        raise _Unavailable('out_of_scope', ('pure liquid phase required; gas/unknown phase withheld',))
    if liquid.density_kg_m3 is None or pipe.volume_m3 is None:
        raise _Unavailable('needs_data', ('computed provider state lacks density or volume',))
    mass = liquid.density_kg_m3 * pipe.volume_m3
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError('stored liquid mass exceeds positive finite support')
    return mass, liquid, pipe


def evaluate_closed_liquid_compatibility(q: ClosedLiquidCompatibilityRequest, *,
    density_provider: TestLiquidProvider | None = None,
    volume_provider: TestPipeVolumeProvider | None = None) -> ClosedLiquidCompatibilityResult:
    """Solve rho(P,Twater)*V(P,Twall,Pexternal)=reference mass+net added mass.

    Fluid and wall temperatures remain separate. Liquid compressibility, if
    reported, is the isothermal density derivative divided by density; wave-speed
    adiabatic modulus is never used here. A reviewed continuous monotone bracket
    and positive single-liquid states are required. There is no leak diagnosis,
    transient temperature field, trapped-gas compliance or design-pressure test.
    """
    if not isinstance(q,ClosedLiquidCompatibilityRequest):raise TypeError('ClosedLiquidCompatibilityRequest required')
    q=ClosedLiquidCompatibilityRequest.model_validate(q.model_dump(mode='python'))
    common=dict(asset_id=q.asset_id,clock_id=q.clock_id,reference_time_s=q.reference_time_s,target_time_s=q.target_time_s)
    if density_provider is None or volume_provider is None or not q.uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed or not q.continuous_strictly_increasing_mass_pressure_on_bracket_reviewed:
        return ClosedLiquidCompatibilityResult(status='needs_data',issues=('density/volume providers and uniform liquid/monotone bracket reviews required',),**common)
    def state_mass(p,water_t,wall_t,external):
        return _resolved_inventory(q,p,water_t,wall_t,external,density_provider,volume_provider)
    try:
        pref=q.reference_pressure.absolute_Pa()
        mref,ref_liquid,ref_pipe=state_mass(pref,q.reference_water_temperature_K,q.reference_wall_temperature_K,q.reference_external_pressure_Pa_abs)
        target=math.fsum((mref,q.net_added_liquid_mass_kg))
        if not math.isfinite(target) or target<=0:raise ValueError('target liquid mass must remain positive and finite')
        common.update(reference_mass_kg=mref,target_mass_kg=target,reference_liquid_state=ref_liquid,reference_pipe_state=ref_pipe)
        def residual(p):return state_mass(p,q.target_water_temperature_K,q.target_wall_temperature_K,q.target_external_pressure_Pa_abs)[0]-target
        lo,hi=(p.absolute_Pa() for p in q.pressure_bracket);flo,fhi=residual(lo),residual(hi)
        if flo==0:root=lo;iterations=0
        elif fhi==0:root=hi;iterations=0
        elif (flo>0)==(fhi>0):return ClosedLiquidCompatibilityResult(status='no_bracket',issues=('supplied interval does not bracket target mass; bounds are not expanded automatically',),**common)
        else:
            root,info=brentq(residual,lo,hi,xtol=q.absolute_pressure_tolerance_Pa,rtol=q.relative_pressure_tolerance,maxiter=q.maximum_iterations,full_output=True,disp=False)
            iterations=info.iterations
            if not info.converged:return ClosedLiquidCompatibilityResult(status='not_converged',iterations=iterations,issues=('pressure root did not converge',),**common)
        mass,liquid,pipe=state_mass(root,q.target_water_temperature_K,q.target_wall_temperature_K,q.target_external_pressure_Pa_abs)
        error=mass-target
        if abs(error)>q.absolute_mass_tolerance_kg+q.relative_mass_tolerance*target:
            return ClosedLiquidCompatibilityResult(status='not_converged',iterations=iterations,mass_budget_residual_kg=error,issues=('pressure root does not close the declared mass budget',),**common)
        increment=root-pref
        return ClosedLiquidCompatibilityResult(status='computed',pressure_Pa_abs=root,pressure_increment_Pa=increment,
            differential_pressure_increment_Pa=increment-(q.target_external_pressure_Pa_abs-q.reference_external_pressure_Pa_abs),
            mass_budget_residual_kg=error,target_liquid_state=liquid,target_pipe_state=pipe,iterations=iterations,**common)
    except _Unavailable as exc:
        return ClosedLiquidCompatibilityResult(status=exc.status,issues=tuple(exc.issues),**common)


class RequiredLiquidInjectionRequest(Contract):
    asset_id: Name
    clock_id: Name
    reference_clock_id: Name
    reference_time_s: float
    target_time_s: float
    reference_pressure: TestPressureCoordinate
    target_pressure: TestPressureCoordinate
    reference_external_pressure_Pa_abs: Positive
    target_external_pressure_Pa_abs: Positive
    reference_water_temperature_K: Positive
    target_water_temperature_K: Positive
    reference_wall_temperature_K: Positive
    target_wall_temperature_K: Positive
    fluid_id: Name
    property_basis_id: Name
    geometry_basis_id: Name
    uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed: bool = False

    @model_validator(mode='after')
    def common_clock(self):
        if self.clock_id != self.reference_clock_id:
            raise ValueError('reference and target clocks must be reconciled')
        return self


class RequiredLiquidInjectionResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    asset_id: Name
    clock_id: Name
    reference_time_s: float
    target_time_s: float
    required_added_mass_kg: float | None = None
    added_volume_at_reference_state_m3: float | None = None
    added_volume_at_target_state_m3: float | None = None
    reference_mass_kg: Positive | None = None
    target_mass_kg: Positive | None = None
    reference_liquid_state: TestLiquidState | None = None
    target_liquid_state: TestLiquidState | None = None
    reference_pipe_state: TestPipeVolumeState | None = None
    target_pipe_state: TestPipeVolumeState | None = None
    issues: tuple[str, ...] = ()
    leak_or_test_acceptance_inferred: Literal[False] = False


def evaluate_required_liquid_injection(request: RequiredLiquidInjectionRequest, *,
    density_provider: TestLiquidProvider | None = None,
    volume_provider: TestPipeVolumeProvider | None = None) -> RequiredLiquidInjectionResult:
    """Compute signed required mass addition between two specified states.

    Volume outputs state their density basis. A negative result is withdrawal;
    neither a meter reading nor actual leakage follows from this difference.
    """
    if not isinstance(request, RequiredLiquidInjectionRequest):
        raise TypeError('RequiredLiquidInjectionRequest required')
    q = RequiredLiquidInjectionRequest.model_validate(request.model_dump())
    common = dict(asset_id=q.asset_id, clock_id=q.clock_id,
                  reference_time_s=q.reference_time_s, target_time_s=q.target_time_s)
    if density_provider is None or volume_provider is None or not q.uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed:
        return RequiredLiquidInjectionResult(status='needs_data', issues=('reviewed liquid/volume providers and uniform pure-liquid assumptions required',), **common)
    try:
        initial, liquid0, pipe0 = _resolved_inventory(q, q.reference_pressure.absolute_Pa(),
            q.reference_water_temperature_K, q.reference_wall_temperature_K,
            q.reference_external_pressure_Pa_abs, density_provider, volume_provider)
        final, liquid1, pipe1 = _resolved_inventory(q, q.target_pressure.absolute_Pa(),
            q.target_water_temperature_K, q.target_wall_temperature_K,
            q.target_external_pressure_Pa_abs, density_provider, volume_provider)
    except _Unavailable as exc:
        return RequiredLiquidInjectionResult(status=exc.status, issues=tuple(exc.issues), **common)
    added = math.fsum((final, -initial))
    v0, v1 = added / liquid0.density_kg_m3, added / liquid1.density_kg_m3
    if not all(math.isfinite(v) for v in (added, v0, v1)):
        raise ValueError('required injection exceeds finite numerical support')
    return RequiredLiquidInjectionResult(status='computed', required_added_mass_kg=added,
        added_volume_at_reference_state_m3=v0, added_volume_at_target_state_m3=v1,
        reference_mass_kg=initial, target_mass_kg=final,
        reference_liquid_state=liquid0, target_liquid_state=liquid1,
        reference_pipe_state=pipe0, target_pipe_state=pipe1, **common)
