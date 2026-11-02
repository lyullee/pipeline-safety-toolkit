"""The default remains withheld before inspecting an SDK source or importing CoolProp."""

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.pool_exchange import PoolRequest, evaluate_pool_request, reference_config

if __name__ == "__main__":
    context = RunContext(asset_id="ground", scenario_id="unreviewed", clock_id="elapsed", origin="synthetic")
    print(evaluate_pool_request(PoolRequest("UNRESOLVED_SOURCE", reference_config(), context)))
