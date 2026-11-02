"""Explicit DC/steady sinusoidal RMS power and supplied circuit impedance budgets."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Calculation,Contract,Positive,Nonnegative,Fraction

class ElectricalCircuitRequest(Contract):
    system: Literal['dc','ac_single_phase','ac_three_phase_balanced']
    load_real_power_W: Nonnegative | None
    line_current_A: Nonnegative | None = None
    receiving_line_voltage_V: Positive | None
    load_power_factor: Positive | None = Field(le=1)
    load_reactive_direction: Literal['lagging','leading','none']
    path_resistance_ohm: Nonnegative | None
    path_reactance_ohm: float | None
    impedance_basis: Literal['complete_loop','per_phase_line']
    supplied_conversion_efficiency: Fraction | None = None
    steady_state_and_impedance_basis_reviewed: bool = False

def evaluate_electrical_circuit(q: ElectricalCircuitRequest) -> Calculation:
    """DC and single-phase impedance is the complete loop; balanced3 uses one phase.

    AC voltages and currents are fundamental sinusoidal RMS observations. The
    three-phase voltage is line-to-line and phase branches are symmetric. Supplied
    impedance includes the actual temperature/frequency state; no cable table,
    skin effect, harmonics, machine map or fault/earthing model is inferred.
    """
    from fluids.pump import current_ideal
    q=ElectricalCircuitRequest.model_validate(q.model_dump(mode='python'))
    if (not q.steady_state_and_impedance_basis_reviewed or None in (q.receiving_line_voltage_V,q.load_power_factor,q.path_resistance_ohm,q.path_reactance_ohm) or (q.load_real_power_W is None and q.line_current_A is None)):
        return Calculation(values={},status='needs_data',notes=('complete receiving state, impedance and explicit steady-state/basis review required',))
    three=q.system=='ac_three_phase_balanced';dc=q.system=='dc'
    if q.impedance_basis!=('per_phase_line' if three else 'complete_loop'):raise ValueError('impedance reference does not match circuit phases')
    if dc and (q.load_power_factor!=1 or q.load_reactive_direction!='none' or q.path_reactance_ohm!=0):raise ValueError('DC requires unity factor, no reactive direction and zero reactance')
    if q.load_reactive_direction=='none' and q.load_power_factor!=1:raise ValueError('nonunity factor requires signed reactive direction')
    phases=3 if three else 1;line_factor=math.sqrt(3.) if three else 1.
    if q.line_current_A is None:
        current=current_ideal(q.load_real_power_W,q.receiving_line_voltage_V,phase=phases,PF=q.load_power_factor)
        if q.load_real_power_W>0 and current==0:raise ValueError('nonzero power current is below numerical support')
    else:
        current=q.line_current_A
        observed_power=line_factor*q.receiving_line_voltage_V*current*q.load_power_factor
        if current>0 and observed_power==0:raise ValueError('nonzero current power is below numerical support')
        if q.load_real_power_W is not None and not math.isclose(q.load_real_power_W,observed_power,rel_tol=1e-12,abs_tol=0.):
            raise ValueError('supplied current and active power contradict the receiving state')
    reactive_sign=-1 if q.load_reactive_direction=='leading' else 1
    sinus=reactive_sign*math.sqrt((1-q.load_power_factor)*(1+q.load_power_factor))
    i=complex(current*q.load_power_factor,-current*sinus);vr=q.receiving_line_voltage_V/line_factor
    drop=i*complex(q.path_resistance_ohm,q.path_reactance_ohm);vs=vr+drop
    load_s=phases*vr*i.conjugate();supply_s=phases*vs*i.conjugate();joule=phases*current*current*q.path_resistance_ohm
    vals=dict(line_current_A=current,receiving_phase_voltage_V=vr,source_line_voltage_V=abs(vs)*line_factor,
        source_phase_voltage_real_V=vs.real,source_phase_voltage_imag_V=vs.imag,
        phase_drop_real_V=drop.real,phase_drop_imag_V=drop.imag,
        load_real_power_W=load_s.real,load_reactive_power_VAr=load_s.imag,
        source_real_power_W=supply_s.real,source_reactive_power_VAr=supply_s.imag,
        line_joule_loss_W=joule,line_reactive_demand_VAr=phases*current*current*q.path_reactance_ohm,
        real_power_budget_residual_W=math.fsum((supply_s.real,-load_s.real,-joule)))
    if q.supplied_conversion_efficiency is not None:
        useful=load_s.real*q.supplied_conversion_efficiency
        vals.update(useful_conversion_power_W=useful,conversion_loss_W=load_s.real-useful)
    if not all(math.isfinite(x) for x in vals.values()):raise ValueError('circuit budget exceeds finite numerical support')
    return Calculation(values=vals,notes=('Supplied impedance and factor only; electrical material/installation accuracy and machine efficiency are not estimated.',))

class ReactiveCompensationRequest(Contract):
    load_real_power_W: Nonnegative | None
    initial_power_factor: Positive | None = Field(le=1)
    target_power_factor: Positive | None = Field(le=1)
    initial_direction: Literal['lagging','leading']
    target_direction: Literal['lagging','leading']
    sinusoidal_same_real_power_reviewed: bool = False

def evaluate_reactive_compensation(q: ReactiveCompensationRequest) -> Calculation:
    """Positive shunt capacitive VAr removes inductive reactive demand; negative needs inductive compensation."""
    q=ReactiveCompensationRequest.model_validate(q.model_dump(mode='python'))
    if not q.sinusoidal_same_real_power_reviewed or None in (q.load_real_power_W,q.initial_power_factor,q.target_power_factor):
        return Calculation(values={},status='needs_data',notes=('complete power/factors and sinusoidal equal-active-power review required',))
    def reactive(pf,direction):return (-1 if direction=='leading' else 1)*q.load_real_power_W*math.sqrt((1-pf)*(1+pf))/pf
    before=reactive(q.initial_power_factor,q.initial_direction);after=reactive(q.target_power_factor,q.target_direction)
    vals=dict(initial_reactive_power_VAr=before,target_reactive_power_VAr=after,
        required_shunt_capacitive_VAr=before-after,initial_apparent_power_VA=q.load_real_power_W/q.initial_power_factor,
        target_apparent_power_VA=q.load_real_power_W/q.target_power_factor)
    if not all(math.isfinite(x) for x in vals.values()):raise ValueError('reactive budget exceeds finite numerical support')
    return Calculation(values=vals,notes=('No capacitor connection/voltage/frequency sizing, harmonics, resonance, switching or grid-stability qualification.',))
