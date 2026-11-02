"""Prescribed open-port pressure/momentum contributions and optional steady CV force."""
import math
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative,Calculation
Vector=tuple[float,float,float]

class MomentumPort(Contract):
    id: Name
    outward_unit_normal: Vector
    area_m2: Positive
    pressure_Pa_abs: Positive | None
    outward_mass_flow_kg_s: float | None
    fluid_velocity_m_s: Vector | None

class MomentumLoadRequest(Contract):
    coordinate_frame_id: Name
    ambient_pressure_Pa_abs: Positive | None
    ports: tuple[MomentumPort,...] = Field(min_length=1,max_length=256)
    uniform_port_states_and_common_frame_reviewed: bool = False
    normal_direction_tolerance_m_s: Nonnegative = 0.
    complete_steady_control_volume_reviewed: bool = False
    body_force_on_fluid_N: Vector | None = None
    absolute_mass_balance_tolerance_kg_s: Nonnegative | None = None
    @model_validator(mode='after')
    def unique(self):
        if len({p.id for p in self.ports})!=len(self.ports):raise ValueError('momentum-port identifiers must be unique')
        return self

def evaluate_port_momentum_load(q: MomentumLoadRequest) -> Calculation:
    """Return -(gauge pressure*area*normal + outward mass flux*fluid velocity).

    Port-only discharge contributions do not identify a whole valve/nozzle load.
    A complete steady fluid control volume additionally needs balanced mass and
    explicit body force. The remaining fluid-on-hardware force is body force plus
    the summed open-port contributions. Pressure, area and velocity states are
    supplied observations/flow-model outputs, not predictions of this ledger.
    """
    q=MomentumLoadRequest.model_validate(q.model_dump(mode='python'))
    for p in q.ports:
        if not math.isclose(math.hypot(*p.outward_unit_normal),1.,rel_tol=0.,abs_tol=1e-12):raise ValueError('each port normal must be a unit vector in the common frame')
    if not q.uniform_port_states_and_common_frame_reviewed or q.ambient_pressure_Pa_abs is None or any(p.pressure_Pa_abs is None or p.outward_mass_flow_kg_s is None or p.fluid_velocity_m_s is None for p in q.ports):
        return Calculation(values={},status='needs_data',notes=('complete uniform port states, explicit ambient and frame review required',))
    rows=[]
    for p in q.ports:
        un=math.fsum(a*b for a,b in zip(p.outward_unit_normal,p.fluid_velocity_m_s));m=p.outward_mass_flow_kg_s;tol=q.normal_direction_tolerance_m_s
        if (m>0 and un<=0) or (m<0 and un>=0) or (m==0 and abs(un)>tol):raise ValueError('signed outward mass flux and uniform normal velocity are inconsistent')
        pressure=tuple(-(p.pressure_Pa_abs-q.ambient_pressure_Pa_abs)*p.area_m2*x for x in p.outward_unit_normal)
        momentum=tuple(-m*x for x in p.fluid_velocity_m_s);contribution=tuple(math.fsum((a,b)) for a,b in zip(pressure,momentum))
        if not all(math.isfinite(x) for x in (*pressure,*momentum,*contribution)):raise ValueError('port force exceeds finite support')
        rows.append(dict(port_id=p.id,pressure_contribution_N=pressure,momentum_contribution_N=momentum,combined_contribution_N=contribution))
    combined=tuple(math.fsum(row['combined_contribution_N'][i] for row in rows) for i in range(3));balance=math.fsum(p.outward_mass_flow_kg_s for p in q.ports)
    vals=dict(coordinate_frame_id=q.coordinate_frame_id,port_contributions=rows,summed_open_port_contribution_N=combined,signed_outward_mass_balance_kg_s=balance,whole_hardware_force_identified=False)
    if not all(math.isfinite(x) for x in (*combined,balance)):raise ValueError('summed force or mass budget exceeds finite support')
    if q.complete_steady_control_volume_reviewed:
        if q.body_force_on_fluid_N is None or q.absolute_mass_balance_tolerance_kg_s is None:
            return Calculation(values=vals,status='needs_data',notes=('complete steady CV requires known body force and explicit mass-balance tolerance',))
        if abs(balance)>q.absolute_mass_balance_tolerance_kg_s:
            return Calculation(values=vals,status='out_of_scope',notes=('selected boundaries do not close the declared steady mass budget',))
        force=tuple(math.fsum((a,b)) for a,b in zip(combined,q.body_force_on_fluid_N))
        if not all(math.isfinite(x) for x in force):raise ValueError('whole hardware force exceeds finite support')
        vals.update(fluid_force_on_hardware_N=force,whole_hardware_force_identified=True)
    return Calculation(values=vals,notes=('No port thermodynamics/choking, flow history, impact or mounting stress is inferred; outlet contributions and whole-CV force are distinct.',))
