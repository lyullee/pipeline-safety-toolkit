"""Finite gas-valve capacity by inverting the existing native Kv sizing leaf.

The factory supplies actual trial P1/P2 and normal-flow gas sizing requests.
Its physical properties and geometry must not change with the requested flow
at a fixed pressure pair. Maximum Kv is an effective flow coefficient, not
mechanical stem travel. The explicit normal gas density converts native
273.15 K/101325 Pa volume flow to kg/s.
"""
from __future__ import annotations
import math
from typing import Callable,Literal
from pydantic import Field
from .contracts import Contract,Name,Positive
from .models.library_models import GasControlValve,gas_control_valve
from .gas_regulator_boundary import GasValveCapacityPoint


class GasCvCapacityConfiguration(Contract):
    maximum_effective_Kv: Positive
    normal_gas_density_kg_m3: Positive
    normal_reference_temperature_K: Literal[273.15] = 273.15
    normal_reference_pressure_pa_abs: Literal[101325.] = 101325.
    upper_normal_volume_flow_support_m3_s: Positive
    fluid_and_mass_basis_id: Name
    characteristic_basis_id: Name
    actual_factory_gas_properties_geometry_and_normal_state_reviewed: bool = False
    sizing_applicability_and_monotone_capacity_over_flow_support_reviewed: bool = False
    normal_flow_root_absolute_tolerance_m3_s: Positive = 1e-12
    maximum_root_iterations: int = Field(default=100,ge=1,le=10000)


class NativeCvGasCapacityProvider:
    """Adapt existing actual-Q sizing to a finite maximum-Kv flow boundary."""
    def __init__(self,configuration:GasCvCapacityConfiguration,request_factory:Callable[[float,float,float],GasControlValve]):
        self.configuration=configuration
        self.request_factory=request_factory
        self.native_sizing_call_count=0

    def _sized(self,upstream,downstream,normal_flow,templates):
        p=self.request_factory(upstream,downstream,normal_flow)
        if not isinstance(p,GasControlValve):raise TypeError('capacity factory must return the existing GasControlValve request')
        p=GasControlValve.model_validate(p.model_dump())
        for actual,wanted in [(p.inlet_pressure_pa_abs,upstream),(p.outlet_pressure_pa_abs,downstream),(p.standard_volume_flow_m3_s,normal_flow)]:
            if not math.isclose(actual,wanted,rel_tol=1e-12,abs_tol=1e-14):raise ValueError('valve factory changed actual pressure or requested normal-flow coordinate')
        template=p.model_dump(exclude={'standard_volume_flow_m3_s'})
        if templates and templates[0]!=template:raise ValueError('actual gas properties/geometry changed with requested flow at fixed pressures')
        if not templates:templates.append(template)
        self.native_sizing_call_count+=1
        result=gas_control_valve(p)
        kv=result.values['kv']
        if result.status!='computed' or not math.isfinite(kv) or kv<=0:raise ValueError('native gas sizing does not give finite positive effective Kv')
        return kv

    def __call__(self,upstream_pressure_pa_abs,downstream_pressure_pa_abs):
        c=self.configuration
        base=dict(upstream_pressure_pa_abs=upstream_pressure_pa_abs,downstream_pressure_pa_abs=downstream_pressure_pa_abs,fluid_and_mass_basis_id=c.fluid_and_mass_basis_id,characteristic_basis_id=c.characteristic_basis_id)
        if not (c.actual_factory_gas_properties_geometry_and_normal_state_reviewed and c.sizing_applicability_and_monotone_capacity_over_flow_support_reviewed):
            return GasValveCapacityPoint(maximum_mass_flow_kg_s=None,**base)
        if not upstream_pressure_pa_abs>downstream_pressure_pa_abs:raise ValueError('native directed gas capacity requires positive actual pressure drop')
        templates=[]
        def residual(flow):
            # The reviewed physical zero-flow/zero-effective-Kv limit avoids
            # inventing a positive request for an actual zero-flow boundary.
            if flow==0:return -c.maximum_effective_Kv
            return self._sized(upstream_pressure_pa_abs,downstream_pressure_pa_abs,flow,templates)-c.maximum_effective_Kv
        hi=c.upper_normal_volume_flow_support_m3_s
        if residual(hi)<0:raise ValueError('supplied normal-flow support does not bracket maximum effective valve capacity')
        from scipy.optimize import brentq
        flow=brentq(residual,0.,hi,xtol=c.normal_flow_root_absolute_tolerance_m3_s,rtol=4*math.ulp(1.),maxiter=c.maximum_root_iterations)
        check=residual(flow)
        if abs(check)>1e-9*max(c.maximum_effective_Kv,1e-6):raise ValueError('native maximum-Kv flow inverse exceeds capacity residual budget')
        mass=flow*c.normal_gas_density_kg_m3
        if not math.isfinite(mass) or mass<=0:raise ValueError('native capacity mass normalization is not representable positive')
        return GasValveCapacityPoint(maximum_mass_flow_kg_s=mass,**base)

    def required_effective_Kv(self,upstream_pressure_pa_abs,downstream_pressure_pa_abs,mass_flow_kg_s):
        """Native actual-flow setting; not a linearized opening fraction."""
        c=self.configuration
        if not (c.actual_factory_gas_properties_geometry_and_normal_state_reviewed and c.sizing_applicability_and_monotone_capacity_over_flow_support_reviewed):
            raise ValueError('native setting needs reviewed actual gas, normal-state and flow-support applicability')
        if not math.isfinite(mass_flow_kg_s) or mass_flow_kg_s<0:raise ValueError('requested actual mass flow must be finite nonnegative')
        if mass_flow_kg_s==0:return 0.
        normal=mass_flow_kg_s/c.normal_gas_density_kg_m3
        if normal>c.upper_normal_volume_flow_support_m3_s:raise ValueError('requested native setting leaves reviewed normal-flow support')
        kv=self._sized(upstream_pressure_pa_abs,downstream_pressure_pa_abs,normal,[])
        if kv>c.maximum_effective_Kv*(1+1e-8):raise ValueError('required native setting exceeds supplied maximum Kv')
        return kv
