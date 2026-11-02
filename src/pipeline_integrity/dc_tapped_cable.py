"""Steady tapped DC feed cable and explicitly supplied rectifier energy budget."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Nonnegative,Positive


class DCOhmicDriveRequest(Contract):
    net_driving_potential_difference_V: float | None = None
    complete_path_resistance_ohm: Positive | None = None
    potential_reference_id: Name
    resistance_basis_id: Name
    common_reference_and_steady_complete_passive_resistive_path_reviewed: bool = False


class DCOhmicDriveResult(Contract):
    status: Literal['computed','needs_data']
    potential_reference_id: Name
    resistance_basis_id: Name
    signed_current_A: float | None = None
    dissipated_power_W: Nonnegative | None = None
    issues: tuple[str,...] = ()


def observe_dc_ohmic_drive(q:DCOhmicDriveRequest)->DCOhmicDriveResult:
    """Observe the supplied net driving voltage and complete resistance only.

    Electrode offsets, polarization drops and earth geometry must already be
    reconciled. They are not guessed from a material label or soil resistivity.
    """
    q=DCOhmicDriveRequest.model_validate(q.model_dump(mode='python'))
    base=dict(potential_reference_id=q.potential_reference_id,resistance_basis_id=q.resistance_basis_id)
    if q.net_driving_potential_difference_V is None or q.complete_path_resistance_ohm is None or not q.common_reference_and_steady_complete_passive_resistive_path_reviewed:
        return DCOhmicDriveResult(status='needs_data',issues=('net driving voltage, complete resistance and common-reference review required',),**base)
    current=q.net_driving_potential_difference_V/q.complete_path_resistance_ohm
    return DCOhmicDriveResult(status='computed',signed_current_A=current,
        dissipated_power_W=current*q.net_driving_potential_difference_V,**base)


class DCCollectedCurrentSegment(Contract):
    start_positive_x_axial_current_A: float | None = None
    end_positive_x_axial_current_A: float | None = None
    segment_length_m: Positive
    coordinate_frame_id: Name
    steady_same_oriented_current_coordinate_reviewed: bool = False


class DCCollectedCurrentResult(Contract):
    status: Literal['computed','needs_data']
    coordinate_frame_id: Name
    signed_current_collected_into_pipe_A: float | None = None
    mean_current_collected_into_pipe_A_m: float | None = None


def observe_dc_collected_current(q:DCCollectedCurrentSegment)->DCCollectedCurrentResult:
    """Signed steady segment pickup from two same-oriented current observations.

    This is a segment-average charge balance; no representativeness for another
    line, protective potential, local current density or corrosion rate follows.
    """
    q=DCCollectedCurrentSegment.model_validate(q.model_dump(mode='python'))
    if q.start_positive_x_axial_current_A is None or q.end_positive_x_axial_current_A is None or not q.steady_same_oriented_current_coordinate_reviewed:
        return DCCollectedCurrentResult(status='needs_data',coordinate_frame_id=q.coordinate_frame_id)
    pickup=q.end_positive_x_axial_current_A-q.start_positive_x_axial_current_A
    return DCCollectedCurrentResult(status='computed',coordinate_frame_id=q.coordinate_frame_id,
        signed_current_collected_into_pipe_A=pickup,mean_current_collected_into_pipe_A_m=pickup/q.segment_length_m)


class DCCableTap(Contract):
    id: Name
    position_m: Nonnegative
    current_draw_A: Nonnegative | None = None


class DCTappedCableRequest(Contract):
    taps: tuple[DCCableTap,...] = Field(min_length=1,max_length=4096)
    series_path_resistance_ohm_m: Nonnegative | None = None
    source_voltage_to_common_return_V: Nonnegative | None = None
    rectifier_input_requested: bool = False
    supplied_dc_real_output_over_ac_real_input_efficiency: Positive | None = Field(default=None,le=1)
    rectifier_efficiency_basis_id: Name | None = None
    unidirectional_rectifier_proportional_power_basis_reviewed: bool = False
    steady_currents_common_return_and_series_path_basis_reviewed: bool = False
    asset_id: Name
    clock_id: Name
    coordinate_frame_id: Name
    potential_reference_id: Name
    cable_resistance_basis_id: Name

    @model_validator(mode='after')
    def directed_chain(self):
        if len({t.id for t in self.taps})!=len(self.taps):raise ValueError('tap identities must be unique')
        if any(b.position_m<=a.position_m for a,b in zip(self.taps,self.taps[1:])):
            raise ValueError('tap chain positions must strictly increase without silent reordering')
        return self


class DCCableSegmentObservation(Contract):
    downstream_tap_id: Name
    start_position_m: Nonnegative
    end_position_m: Nonnegative
    series_current_A: Nonnegative
    segment_resistance_ohm: Nonnegative
    segment_drop_V: Nonnegative
    cumulative_drop_V: Nonnegative
    joule_power_W: Nonnegative
    tap_voltage_to_common_return_V: float | None = None
    tap_delivered_power_W: float | None = None


class DCTappedCableResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    cable_status: Literal['computed','needs_data','out_of_scope']
    rectifier_status: Literal['not_requested','computed','needs_data','out_of_scope']
    asset_id: Name
    clock_id: Name
    coordinate_frame_id: Name
    potential_reference_id: Name
    cable_resistance_basis_id: Name
    segments: tuple[DCCableSegmentObservation,...] = ()
    source_current_A: Nonnegative | None = None
    last_tap_cumulative_drop_V: Nonnegative | None = None
    cable_joule_power_W: Nonnegative | None = None
    source_dc_real_power_W: Nonnegative | None = None
    total_tap_delivered_power_W: float | None = None
    dc_power_balance_residual_W: float | None = None
    required_ac_real_input_power_W: Nonnegative | None = None
    rectifier_loss_W: Nonnegative | None = None
    issues: tuple[str,...] = ()
    tap_current_distribution_or_earth_resistance_inferred: Literal[False] = False


def observe_dc_tapped_cable(q:DCTappedCableRequest)->DCTappedCableResult:
    """Apply KCL cuts and Ohm/Joule law to a supplied one-way tapped conductor.

    Each series-path resistance is supplied per metre of chainage; no second
    return conductor or earth path is silently added. The return is a declared
    common potential reference. Tap currents are data, not predicted by coating,
    anode polarization or soil geometry. Efficiency relates DC real output to AC
    real input and does not establish power factor, apparent VA or idle losses.
    """
    q=DCTappedCableRequest.model_validate(q.model_dump(mode='python'))
    common={key:getattr(q,key) for key in ('asset_id','clock_id','coordinate_frame_id','potential_reference_id','cable_resistance_basis_id')}
    requested='needs_data' if q.rectifier_input_requested else 'not_requested'
    if q.series_path_resistance_ohm_m is None or any(t.current_draw_A is None for t in q.taps) or not q.steady_currents_common_return_and_series_path_basis_reviewed:
        return DCTappedCableResult(status='needs_data',cable_status='needs_data',rectifier_status=requested,
            issues=('all tap currents, series path resistance and common-reference review required',),**common)
    currents=[0.]*len(q.taps);current=0.
    for i in range(len(q.taps)-1,-1,-1):
        current=math.fsum((current,q.taps[i].current_draw_A));currents[i]=current
    position=drop=heat=0.;segments=[]
    for tap,current in zip(q.taps,currents):
        resistance=(tap.position_m-position)*q.series_path_resistance_ohm_m
        dv=resistance*current;drop=math.fsum((drop,dv));power=resistance*current**2;heat=math.fsum((heat,power))
        voltage=q.source_voltage_to_common_return_V-drop if q.source_voltage_to_common_return_V is not None else None
        segments.append(DCCableSegmentObservation(downstream_tap_id=tap.id,start_position_m=position,end_position_m=tap.position_m,
            series_current_A=current,segment_resistance_ohm=resistance,segment_drop_V=dv,cumulative_drop_V=drop,
            joule_power_W=power,tap_voltage_to_common_return_V=voltage,
            tap_delivered_power_W=voltage*tap.current_draw_A if voltage is not None else None))
        position=tap.position_m
    values=dict(segments=tuple(segments),source_current_A=currents[0],last_tap_cumulative_drop_V=drop,cable_joule_power_W=heat)
    if q.source_voltage_to_common_return_V is not None:
        source_power=q.source_voltage_to_common_return_V*currents[0]
        delivered=math.fsum(s.tap_delivered_power_W for s in segments)
        residual=source_power-delivered-heat
        values.update(source_dc_real_power_W=source_power,total_tap_delivered_power_W=delivered,dc_power_balance_residual_W=residual)
        if any(s.tap_voltage_to_common_return_V<0 for s in segments):
            return DCTappedCableResult(status='out_of_scope',cable_status='out_of_scope',rectifier_status='out_of_scope' if q.rectifier_input_requested else 'not_requested',
                issues=('prescribed current/voltage would require a negative tap potential outside passive one-way load support',),**values,**common)
        if abs(residual)>1e-10+1e-12*max(source_power,heat):
            raise ValueError('DC source/tap/Joule power ledger exceeds finite arithmetic support')
    if not q.rectifier_input_requested:
        return DCTappedCableResult(status='computed',cable_status='computed',rectifier_status='not_requested',**values,**common)
    eta=q.supplied_dc_real_output_over_ac_real_input_efficiency
    if q.source_voltage_to_common_return_V is None or eta is None or q.rectifier_efficiency_basis_id is None or not q.unidirectional_rectifier_proportional_power_basis_reviewed:
        return DCTappedCableResult(status='needs_data',cable_status='computed',rectifier_status='needs_data',
            issues=('rectifier input needs source voltage, reviewed DC/AC real-power efficiency and its basis',),**values,**common)
    ac=values['source_dc_real_power_W']/eta
    return DCTappedCableResult(status='computed',cable_status='computed',rectifier_status='computed',
        required_ac_real_input_power_W=ac,rectifier_loss_W=ac-values['source_dc_real_power_W'],**values,**common)
