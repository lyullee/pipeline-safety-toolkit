# Calibrated conductance signals for liquid-valve transients

`pipeline_integrity.calibrated_valve_boundary` connects the existing calibration application
receipt to the existing `transient.LiquidValve` boundary. It does not duplicate calibration or
the liquid characteristic solver.

The selected calibrated channel must represent effective valve conductance `CdA` in square
metres. The source instrument may use another unit; the calibration receipt binds that input
unit and emits `m2`. A caller reviewed channel-to-component mapping must be present in both the
calibration evidence and network context. Calibration and network share the same scenario,
clock, and observation origin. The trace must begin at time zero, be strictly ordered, contain a
valid nonnegative value for every row, and cover the calibration application inputs exactly.

```python
from pipeline_integrity.calibrated_valve_boundary import bind_calibrated_valve_boundary

binding = bind_calibrated_valve_boundary(
    calibration_application, source_frames, observation_ids,
    calibrated_channel="position_signal", source_stream_id="ZT-101",
    valve_component_id="XV-101", start_node="upstream", end_node="downstream",
    initial_volume_flow_m3_s=0.01, required_duration_s=30.0,
    parameter_source_id="reviewed-CdA-curve",
    network_context=network_context, source_evidence_id="source-record",
    application_evidence_id="calibration-application",
    mapping_evidence_id="instrument-valve-crosswalk",
)
liquid_valve = binding.valve  # supply to an existing LiquidTransient
binding.check_integrity()
```

The adapter retains calibration/source/history/network digests and row IDs, then constructs the
owner `LiquidValve` with an explicit effective-area history. It does not execute a transient,
propagate calibration or input uncertainty, correct clocks, interpolate gaps, or establish a
field-qualified CdA. Missing and invalid conductance observations are rejected. A zero area is
allowed and represents a closed boundary sample.
