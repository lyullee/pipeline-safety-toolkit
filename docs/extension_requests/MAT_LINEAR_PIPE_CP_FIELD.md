# Uniform pipe/coating DC field provider

The material owner currently exposes a supplied-current anode charge budget and
two-electrode galvanic models. A caller also needs the spatial potential/current
field of a uniformly conducting pipe with a uniform passive coating shunt. A
charge budget or isolated earth electrode does not provide this field.

The current implementation is `pipeline_integrity.distributed_pipe_cp`, with
`UniformCPLineRequest` and `evaluate_uniform_cp_line`. Its declared equations are
`dV/dx = -r I` and `dI/dx = -g V`. The receiving owner should provide a compatible
typed endpoint, or expose a provider protocol that this adapter can call. Keep one
canonical numerical implementation when delegating to the receiving owner.

Required inputs:

- Asset, coordinate frame, coefficient basis and potential-reference identities.
- Positive longitudinal resistance in ohm/metre and nonnegative transverse
  conductance in siemens/metre. Both are supplied, not inferred from condition
  labels or a vendor table.
- Finite length and one potential or inward current at each endpoint; alternatively
  a known potential and insulated axial current at the far end.
- A decaying semi-infinite arm with a drain potential or one-arm inward current.
  A symmetric two-arm total is a separate reviewed topology.
- Explicit sample positions and numerical error budgets.

The state `V` is a signed potential displacement about the declared zero-drain
reference. Axial conventional current is positive toward increasing chainage;
endpoint currents are positive into the segment. The shunt current `g V` is
positive outward. The response must retain these signs and return endpoint and
sample states, integrated shunt current, longitudinal/shunt Joule power and
separate charge/power residuals and quadrature errors.

A zero-leakage line with two prescribed currents has no identifiable absolute
potential unless an independent reference is added. Incompatible net current,
unknown coefficients, unsupported numerical decay and failed balance budgets must
remain explicit states. They must not be replaced by a default voltage or clipped
current. The adapter currently supports decay exponents up to 500.

Acceptance should retain the independently solved resistor-ladder refinement,
Kirchhoff boundary-value comparisons, insulated-end reverse integration, current
and energy budgets, polarity and coefficient/length similarity, and perfect-coating
and semi-infinite limits in `tests/test_distributed_pipe_cp_individual.py`.

This request does not add electrode polarization kinetics, an electrochemical
baseline, corrosion-rate prediction, defect inversion, AC induction, heterogeneous
soil, finite return-electrode geometry or protection acceptance criteria. Those
require separate constitutive and observation contracts. The receiving owner must
not label a returned passive line field as a complete protection design.
