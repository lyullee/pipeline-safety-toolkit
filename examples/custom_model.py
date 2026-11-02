"""Register a caller-owned calculation without modifying the distributed package."""

from pydantic import Field

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import Calculation, Contract, ModelInfo, RunContext
from pipeline_integrity.registry import ModelSpec


class Geometry(Contract):
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)


def area(inputs: Geometry):
    return Calculation(values={"area_m2": inputs.width_m * inputs.height_m})


def main():
    engine = create_registry()
    engine.register(
        ModelSpec(
            ModelInfo(
                id="site.rectangle",
                version="1.0",
                title="Caller-owned rectangular area",
                category="site",
                input_units={"width_m": "m", "height_m": "m"},
                output_units={"area_m2": "m2"},
                sources=("caller-defined geometry",),
                assumptions=("Planar rectangle with perpendicular edges.",),
                implementation="user_model",
                qualification="user_model",
            ),
            Geometry,
            area,
        )
    )
    context = RunContext(asset_id="demo", scenario_id="geometry", clock_id="elapsed", origin="synthetic")
    result = engine.evaluate("site.rectangle", {"width_m": 2.0, "height_m": 3.0}, context)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
