"""Reviewed axisymmetric semi-infinite cylindrical edge-moment coordinate."""
import math
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Name

class CylindricalEdgeMomentRequest(Contract):
    edge_moment_per_circumference_n: float | None = None
    inverse_decay_length_per_m: Positive | None = None
    distance_from_edge_m: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    shell_decay_provider_basis_id: Name
    uniform_edge_moment_zero_edge_shear_semi_infinite_shell_reviewed: bool = False

def evaluate_cylindrical_edge_moment(request: CylindricalEdgeMomentRequest):
    """Use a supplied shell decay parameter without guessing radius conventions.

    The response branch has prescribed edge moment, zero edge shear and decay
    at infinity. The caller supplies a reviewed shell decay parameter; its
    diameter/radius, material and shell-theory calibration are not inferred.
    Returned moment is per unit circumferential length, not a full-ring torque.
    """
    q=CylindricalEdgeMomentRequest.model_validate(request.model_dump())
    if not q.uniform_edge_moment_zero_edge_shear_semi_infinite_shell_reviewed or q.edge_moment_per_circumference_n is None or q.inverse_decay_length_per_m is None:
        return dict(status='needs_data',shell_decay_provider_basis_id=q.shell_decay_provider_basis_id)
    if not math.isfinite(q.edge_moment_per_circumference_n):raise ValueError('finite edge moment required')
    moments=[];shears=[]
    for x in q.distance_from_edge_m:
        z=q.inverse_decay_length_per_m*x
        moments.append(q.edge_moment_per_circumference_n*math.exp(-z)*(math.cos(z)+math.sin(z)))
        shears.append(-2*q.inverse_decay_length_per_m*q.edge_moment_per_circumference_n*math.exp(-z)*math.sin(z))
    return dict(status='computed',distance_from_edge_m=q.distance_from_edge_m,moment_per_circumference_n=tuple(moments),
        moment_gradient_n_m=tuple(shears),shell_decay_provider_basis_id=q.shell_decay_provider_basis_id,
        local_strength_or_reinforcement_acceptance_inferred=False)
