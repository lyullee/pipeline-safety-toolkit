"""Mode-I linear-elastic intensity to energy-release coordinate."""
from typing import Literal
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative

class ElasticFractureEnergyRequest(Contract):
    mode_i_intensity_pa_sqrt_m: Nonnegative | None = None
    elastic_modulus_pa: Positive | None = None
    poisson_ratio: float | None = Field(default=None,gt=-1,lt=.5)
    plane_state: Literal['plane_stress','plane_strain']
    isotropic_mode_i_linear_elastic_plane_state_reviewed: bool = False

def evaluate_elastic_fracture_energy(request: ElasticFractureEnergyRequest):
    """Evaluate elastic J=G, never infer a measured ductile J resistance.

    The K input may be applied intensity or a reviewed elastic toughness
    coordinate. The conversion alone does not qualify crack-tip constraint,
    large-scale yielding, a J-R curve or a material initiation criterion.
    """
    q=ElasticFractureEnergyRequest.model_validate(request.model_dump())
    if not q.isotropic_mode_i_linear_elastic_plane_state_reviewed or any(v is None for v in (q.mode_i_intensity_pa_sqrt_m,q.elastic_modulus_pa,q.poisson_ratio)):
        return dict(status='needs_data',plane_state=q.plane_state)
    modulus=q.elastic_modulus_pa if q.plane_state=='plane_stress' else q.elastic_modulus_pa/(1-q.poisson_ratio**2)
    return dict(status='computed',plane_state=q.plane_state,effective_fracture_modulus_pa=modulus,
        elastic_energy_release_j_m2=q.mode_i_intensity_pa_sqrt_m**2/modulus,
        ductile_j_resistance_or_fracture_acceptance_inferred=False)
