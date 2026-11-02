"""Minimal authored calculations; no observations, credentials or external engines."""
from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext


def main():
    # Context names identify this example only. They do not assert that a real
    # asset was measured or that a calibration has been performed.
    context = RunContext(asset_id="example-pump", scenario_id="declared-duty",
                         clock_id="elapsed-seconds", origin="synthetic")
    # The model boundary uses SI: m3/s, kg/m3 and metres of head. Efficiencies
    # are fractions on distinct pump and motor conversion stages.
    inputs = dict(volume_flow_m3_s=0.02, density_kg_m3=998.0, head_m=50.0,
                  pump_efficiency=0.8, motor_efficiency=0.95)
    result = create_registry().evaluate("equipment.pump_power", inputs, context)
    # Inspect status and units before transferring a value to another model.
    # A missing/uncomputed value must not silently become zero demand.
    print("status:", result.status)
    print("values:", result.values)
    print("units:", result.output_units)
    return result


if __name__ == "__main__":
    main()
