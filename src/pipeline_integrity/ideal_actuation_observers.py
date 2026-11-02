"""Ideal pressure-actuation and supplied uniform-shear load observers in SI.

These are prescribed-state work/traction coordinates. They do not predict seal
friction, motion, control response, efficiency, cutting fracture or rated capacity.
"""
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Name, Positive, Nonnegative, digest


def uniform_pressure_face_force(inside_pressure_pa_abs: float, outside_pressure_pa_abs: float, area_m2: float) -> float:
    """Uniform pressure traction integrated over an actual planar face area."""
    if not all(math.isfinite(v) for v in (inside_pressure_pa_abs,outside_pressure_pa_abs,area_m2)) or min(inside_pressure_pa_abs,outside_pressure_pa_abs)<0 or area_m2<=0:
        raise ValueError('finite absolute pressures and positive actual area required')
    force=(inside_pressure_pa_abs-outside_pressure_pa_abs)*area_m2
    if not math.isfinite(force):raise ValueError('traction exceeds finite support')
    return force


def _result(q,**values):
    if any(isinstance(v,(int,float)) and not math.isfinite(v) for v in values.values()):raise ValueError('observation exceeds finite support')
    return dict(status='computed',request_digest=digest(q),values=values,field_or_rating_qualified=False)


class IdealPistonRequest(Contract):
    id: Name
    bore_diameter_m: Positive
    rod_diameter_m: Nonnegative
    cap_pressure_pa_abs: Nonnegative
    rod_chamber_pressure_pa_abs: Nonnegative
    exterior_pressure_pa_abs: Nonnegative
    signed_extension_m: float
    measured_chamber: Literal['cap','rod']
    signed_volume_inflow_m3_s: float
    positive_extension_axis: tuple[float,float,float]
    load_observation_direction: tuple[float,float,float]
    uniform_pressure_rigid_piston_incompressible_kinematics_reviewed: bool = False

    @model_validator(mode='after')
    def geometry(self):
        if self.rod_diameter_m>=self.bore_diameter_m:raise ValueError('rod must fit inside bore')
        for v in (self.positive_extension_axis,self.load_observation_direction):
            if not math.isclose(math.hypot(*v),1.,rel_tol=0,abs_tol=1e-12):raise ValueError('explicit unit directions required')
        return self


def evaluate_ideal_piston(q: IdealPistonRequest):
    q=IdealPistonRequest.model_validate(q.model_dump())
    if not q.uniform_pressure_rigid_piston_incompressible_kinematics_reviewed:return dict(status='needs_data',request_digest=digest(q),field_or_rating_qualified=False)
    bore=math.pi*q.bore_diameter_m**2/4
    annulus=math.pi*(q.bore_diameter_m-q.rod_diameter_m)*(q.bore_diameter_m+q.rod_diameter_m)/4
    cap=uniform_pressure_face_force(q.cap_pressure_pa_abs,q.exterior_pressure_pa_abs,bore)
    rod=uniform_pressure_face_force(q.rod_chamber_pressure_pa_abs,q.exterior_pressure_pa_abs,annulus)
    force=cap-rod
    area=bore if q.measured_chamber=='cap' else annulus
    velocity=q.signed_volume_inflow_m3_s/area*(1 if q.measured_chamber=='cap' else -1)
    projection=sum(a*b for a,b in zip(q.positive_extension_axis,q.load_observation_direction))
    return _result(q,cap_area_m2=bore,rod_chamber_area_m2=annulus,cap_extension_force_n=cap,rod_retraction_force_n=rod,
        net_extension_force_n=force,load_direction_force_n=force*projection,extension_velocity_m_s=velocity,
        net_pressure_work_j=force*q.signed_extension_m,net_pressure_power_w=force*velocity,
        cap_swept_volume_m3=bore*q.signed_extension_m,rod_swept_volume_m3=-annulus*q.signed_extension_m)


class IdealMotorRequest(Contract):
    id: Name
    signed_pressure_drop_pa: float
    displacement_m3_per_positive_revolution: Positive
    signed_revolutions_per_s: float
    ideal_incompressible_pressure_work_reviewed: bool = False


def evaluate_ideal_motor(q: IdealMotorRequest):
    q=IdealMotorRequest.model_validate(q.model_dump())
    if not q.ideal_incompressible_pressure_work_reviewed:return dict(status='needs_data',request_digest=digest(q),field_or_rating_qualified=False)
    work=q.signed_pressure_drop_pa*q.displacement_m3_per_positive_revolution
    torque=work/math.tau
    return _result(q,pressure_work_per_positive_revolution_j=work,positive_rotation_torque_nm=torque,
        signed_volume_flow_m3_s=q.displacement_m3_per_positive_revolution*q.signed_revolutions_per_s,
        signed_angular_velocity_rad_s=math.tau*q.signed_revolutions_per_s,transferred_mechanical_power_w=work*q.signed_revolutions_per_s)


class UniformShearCutRequest(Contract):
    id: Name
    actual_cut_perimeter_m: Positive
    uniform_sheet_thickness_m: Positive
    supplied_uniform_shear_strength_pa: Positive
    material_basis_id: Name
    uniform_simultaneous_shear_area_basis_reviewed: bool = False


def evaluate_uniform_shear_cut(q: UniformShearCutRequest):
    q=UniformShearCutRequest.model_validate(q.model_dump())
    if not q.uniform_simultaneous_shear_area_basis_reviewed:return dict(status='needs_data',request_digest=digest(q),field_or_rating_qualified=False)
    area=q.actual_cut_perimeter_m*q.uniform_sheet_thickness_m
    return _result(q,actual_nominal_shear_area_m2=area,nominal_uniform_shear_force_n=q.supplied_uniform_shear_strength_pa*area)
