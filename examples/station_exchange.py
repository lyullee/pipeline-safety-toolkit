"""Safe default: reviewed source and operating conditions have not been supplied."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.station_exchange import StationRequest, evaluate, reference_boundary, reference_config


def make_case(source_root="OWNER_SOURCE_ROOT", python_executable="STATION_PYTHON", source_pin=None):
    context = RunContext(
        asset_id="synthetic-station",
        scenario_id="authored-transfer",
        clock_id="authored-clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored-case",
                citation="Original synthetic verification conditions",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"station_boundary": reference_boundary("synthetic-station", "authored-clock")},
    )
    return StationRequest(
        source_root,
        python_executable,
        reference_config(),
        context,
        evidence=("authored-case",),
        source_pin=source_pin,
    )


if __name__ == "__main__":
    print(evaluate(make_case()))
