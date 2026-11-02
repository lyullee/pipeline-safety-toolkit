"""Uniform-temperature zero-axial-strain cylinder through existing material owners."""
import math
from pydantic import Field
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job

class RestrainedCylinderRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    inner_radius_m: Positive
    outer_radius_m: Positive
    internal_pressure_relative_zero_exterior_pa: Nonnegative | None = None
    elastic_modulus_pa: Positive
    poisson_ratio: float = Field(gt=-1,lt=.5)
    expansion_coefficient_per_k: Nonnegative
    uniform_temperature_change_k: float | None = None
    material_evidence_id: Name
    isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed: bool = False

def evaluate_fully_restrained_cylinder(request: RestrainedCylinderRequest,context: RunContext,*,client=None):
    """Use exact radial elasticity, preserving the zero-exterior stress datum.

    The pressure field is reused from the Lamé owner; only its radial/hoop
    stresses enter the axial compatibility relation. Closed-end free-cylinder
    axial stress is not reused as the constrained axial stress. Uniform free
    thermal strain/stress comes from the existing thermal-restraint owner.
    End-cap equilibrium is reported separately from constitutive wall stress.
    """
    q=RestrainedCylinderRequest.model_validate(request.model_dump());c=RunContext.model_validate(context.model_dump())
    if q.outer_radius_m<=q.inner_radius_m:raise ValueError('positive annular wall required')
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('cylinder/context identity differs')
    if q.material_evidence_id not in {e.id for e in c.evidence}:raise ValueError('material evidence absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),axial_strain_constraint=0.,stress_datum='zero exterior pressure')
    if not q.isotropic_elastic_uniform_temperature_zero_axial_strain_reviewed or q.uniform_temperature_change_k is None or q.internal_pressure_relative_zero_exterior_pa is None:
        return dict(status='needs_data',**base)
    client=MaterialClient() if client is None else client
    def owner(method,parameters):
        job=prepare_material_job(method,parameters,c,location_id=q.location_id,evidence_ids=(q.material_evidence_id,),client=client)
        return evaluate_material_job(job,c,client=client)
    pressure=owner('mechanics.lame_cylinder',dict(inner_radius_m=q.inner_radius_m,outer_radius_m=q.outer_radius_m,
        internal_pressure_Pa=q.internal_pressure_relative_zero_exterior_pa,external_pressure_Pa=0.,evaluation_radius_m=q.inner_radius_m,closed_ends=True))
    thermal=owner('mechanics.thermal_restraint',dict(young_modulus_Pa=q.elastic_modulus_pa,expansion_coefficient_per_K=q.expansion_coefficient_per_k,
        temperature_change_K=q.uniform_temperature_change_k,restraint_fraction=1.))
    if pressure.native.status!='computed' or thermal.native.status!='computed':return dict(status='solver_withheld',**base)
    p=pressure.native.payload['values'];t=thermal.native.payload['values']
    pressure_component=q.poisson_ratio*(p['hoop_stress_Pa']+p['radial_stress_Pa'])
    axial=pressure_component+t['restrained_uniaxial_stress_Pa']
    area=math.pi*(q.outer_radius_m**2-q.inner_radius_m**2);thrust=math.pi*q.inner_radius_m**2*q.internal_pressure_relative_zero_exterior_pa
    return dict(status='computed',**base,axial_stress_tension_positive_pa=axial,axial_stress_compression_positive_pa=-axial,
        pressure_poisson_tension_component_pa=pressure_component,thermal_tension_component_pa=t['restrained_uniaxial_stress_Pa'],
        material_axial_resultant_tension_n=axial*area,pressure_end_thrust_n=thrust,
        required_positive_end_restraint_force_n=axial*area-thrust,
        native_pressure_receipt=pressure.model_dump(mode='json'),native_thermal_receipt=thermal.model_dump(mode='json'),
        soil_friction_axial_slip_yield_or_end_effects_calculated=False)
