"""Conditional charge-depletion observation through the material owner's budget.

The material engine integrates exposed-area/current histories and usable
capacity. This module locates its first charge-exhaustion time on the supplied
history without copying that integration or extending the history in time.
"""
from __future__ import annotations

from bisect import bisect_right

from pydantic import Field

from .contracts import Contract, Positive, RunContext
from .electrochemical_exchange import ElectrochemicalRequest, evaluate_electrochemical


class AnodeLifetimeRequest(Contract):
    budget: ElectrochemicalRequest
    time_absolute_tolerance_s: Positive = 1e-6
    maximum_iterations: int = Field(default=100, ge=1, le=256)


def evaluate_anode_lifetime(q: AnodeLifetimeRequest, context: RunContext, *, client=None) -> dict:
    q = AnodeLifetimeRequest.model_validate(q.model_dump(mode='python'))
    if q.budget.method != 'protection.cathodic_budget':
        raise ValueError('anode observation requires the existing cathodic charge budget')
    full = evaluate_electrochemical(q.budget, context, client=client)
    if full['status'] != 'computed':
        return dict(status=full['status'], issues=full['issues'], owner_budget=full)
    values = full['values']
    charge = values['required_charge_C']
    parameters = q.budget.parameters
    times = parameters['time_s']
    zero_demand = dict(parameters)
    for key in ('bare_surface_fraction', 'bare_surface_current_density_A_m2', 'additional_current_drain_A'):
        zero_demand[key] = [0.]*len(times)
    capacity_record = evaluate_electrochemical(q.budget.model_copy(update={'parameters': zero_demand}), context, client=client)
    if capacity_record['status'] != 'computed':
        return dict(status=capacity_record['status'], issues=capacity_record['issues'], owner_budget=full)
    # Ask the owner for zero-demand installed capacity rather than cancelling
    # a very large demand against its almost-opposite negative margin.
    capacity = capacity_record['values']['installed_charge_margin_C']
    base = dict(owner_budget=full, demand_charge_C=charge,
                consumed_charge_C=min(charge, capacity),
                remaining_usable_charge_C=max(0., capacity-charge),
                charge_deficit_C=max(0., charge-capacity),
                required_equivalent_anode_mass_kg=values['required_continuous_anode_mass_kg'],
                consumed_installed_mass_equivalent_kg=min(values['required_continuous_anode_mass_kg'],
                                                          parameters['installed_anodes']*parameters['single_anode_mass_kg']),
                consumed_usable_anode_mass_kg=min(values['required_continuous_anode_mass_kg'],
                                                 parameters['installed_anodes']*parameters['single_anode_mass_kg'])*parameters['anode_utilization_fraction'],
                owner_capacity_observation=capacity_record,
                installed_peak_current_margin_A=values['installed_peak_current_margin_A'],
                current_delivery_sufficient=values['installed_peak_current_margin_A'] >= 0,
                future_extrapolation_performed=False)
    if capacity <= 0:
        return dict(status='computed', depletion='at_start', depletion_clock_time_s=times[0],
                    time_from_history_start_to_depletion_s=0., **base)
    if charge < capacity:
        return dict(status='computed', depletion='not_reached_in_supplied_history',
                    depletion_clock_time_s=None, time_from_history_start_to_depletion_s=None, **base)

    def prefix_charge(end):
        index = min(len(times)-2, bisect_right(times, end)-1)
        fraction = (end-times[index])/(times[index+1]-times[index])
        p = dict(parameters)
        at_knot = end == times[index]
        p['time_s'] = list(times[:index+1]) if at_knot else list(times[:index+1])+[end]
        for key in ('bare_surface_fraction', 'bare_surface_current_density_A_m2', 'additional_current_drain_A'):
            data = parameters[key]
            p[key] = list(data[:index+1]) if at_knot else list(data[:index+1])+[data[index]+fraction*(data[index+1]-data[index])]
        request = q.budget.model_copy(update={'parameters': p})
        record = evaluate_electrochemical(request, context, client=client)
        if record['status'] != 'computed':
            raise ValueError('owner could not evaluate a prefix of its accepted history')
        return record['values']['required_charge_C']

    lo, hi = times[0], times[-1]
    iterations = 0
    while hi-lo > q.time_absolute_tolerance_s and iterations < q.maximum_iterations:
        mid = lo+(hi-lo)/2
        if mid == lo or mid == hi:
            break
        if prefix_charge(mid) >= capacity:
            hi = mid
        else:
            lo = mid
        iterations += 1
    return dict(status='computed' if hi-lo <= q.time_absolute_tolerance_s else 'not_converged',
                depletion='within_supplied_history', depletion_clock_time_s=hi,
                time_from_history_start_to_depletion_s=hi-times[0],
                depletion_time_bracket_s=(lo, hi), iterations=iterations, **base)
