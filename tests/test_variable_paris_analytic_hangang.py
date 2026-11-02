"""Independent exact references for ordered Paris crack-growth blocks.

Synthetic constant-geometry inputs isolate the Paris law and its depth event.
They do not establish a material calibration or field crack-growth prediction.
"""

import math
import runpy

import pytest

from pipeline_integrity.fracture_exchange import assess_crack_growth


GEOMETRY_FACTOR = 1.2
PARIS_C = 1.0e-28
PARIS_EXPONENT = 3.0
INITIAL_DEPTH_M = 1.0e-3
TERMINAL_DEPTH_M = 4.0e-3


def _base():
    module = runpy.run_path("examples/fracture_screen.py")
    return module["make_growth"](), module["context"]()


def _request(blocks):
    request, context = _base()
    calibration = request.calibration.model_copy(
        update={
            "coefficient_m_cycle_per_pa_sqrt_m_power": PARIS_C,
            "exponent": PARIS_EXPONENT,
        }
    )
    request = request.model_copy(
        update={
            "initial_depth_m": INITIAL_DEPTH_M,
            "terminal_depth_m": TERMINAL_DEPTH_M,
            "geometry_factor": (GEOMETRY_FACTOR, GEOMETRY_FACTOR),
            "blocks": tuple(
                request.blocks[0].model_copy(
                    update={
                        "id": f"load-{index}",
                        "minimum_opening_stress_pa": minimum,
                        "maximum_opening_stress_pa": maximum,
                        "cycles": cycles,
                    }
                )
                for index, (minimum, maximum, cycles) in enumerate(blocks)
            ),
            "calibration": calibration,
        }
    )
    return request, context


def _exact_depth_after_blocks(initial_depth_m, blocks):
    # For da/dN=C*(Y*DeltaSigma*sqrt(pi*a))^m, u=a**(1-m/2)
    # evolves linearly within each constant-range block.
    power = 1.0 - PARIS_EXPONENT / 2.0
    transformed = initial_depth_m**power
    for minimum, maximum, cycles in blocks:
        coefficient = PARIS_C * (
            GEOMETRY_FACTOR * (maximum - minimum) * math.sqrt(math.pi)
        ) ** PARIS_EXPONENT
        transformed += power * coefficient * cycles
    return transformed ** (1.0 / power)


def _native_values(record):
    assert record["status"] == "computed"
    return record["native"]["native"]["payload"]["values"]


def test_two_ordered_stress_blocks_match_exact_transformed_depth():
    blocks = ((10e6, 30e6, 100_000.0), (5e6, 35e6, 120_000.0))
    request, context = _request(blocks)

    record = assess_crack_growth(request, context)
    values = _native_values(record)
    expected_depth = _exact_depth_after_blocks(INITIAL_DEPTH_M, blocks)

    assert values["termination_reason"] == "requested_blocks_completed"
    assert values["requested_cycles"] == pytest.approx(sum(row[2] for row in blocks))
    assert values["cycles_evaluated"] == pytest.approx(sum(row[2] for row in blocks))
    assert values["final_depth_m"] == pytest.approx(expected_depth, rel=2e-7, abs=1e-12)
    assert [row["block_index"] for row in values["block_results"]] == [0, 1]
    assert [row["cycles_used"] for row in values["block_results"]] == pytest.approx(
        [row[2] for row in blocks], rel=2e-7
    )
    assert values["final_depth_m"] < TERMINAL_DEPTH_M
    assert record["cycles_converted_to_elapsed_life"] is False


def test_depth_limit_event_matches_exact_cycles_to_terminal_depth():
    block = (10e6, 30e6, 10_000_000.0)
    request, context = _request((block,))

    record = assess_crack_growth(request, context)
    values = _native_values(record)
    power = 1.0 - PARIS_EXPONENT / 2.0
    coefficient = PARIS_C * (
        GEOMETRY_FACTOR * (block[1] - block[0]) * math.sqrt(math.pi)
    ) ** PARIS_EXPONENT
    expected_cycles = (TERMINAL_DEPTH_M**power - INITIAL_DEPTH_M**power) / (
        power * coefficient
    )

    assert values["termination_reason"] == "declared_depth_limit"
    assert values["final_depth_m"] == pytest.approx(TERMINAL_DEPTH_M, rel=2e-10)
    assert values["cycles_evaluated"] == pytest.approx(expected_cycles, rel=2e-7)
    assert values["block_results"][0]["cycles_used"] == pytest.approx(expected_cycles, rel=2e-7)
    assert values["cycles_evaluated"] < values["requested_cycles"]
    assert record["cycles_converted_to_elapsed_life"] is False
