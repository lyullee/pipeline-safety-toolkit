"""Conditional density observation from independent isentropic modulus and fluid sound speed."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive


class AcousticDensityRequest(Contract):
    isentropic_bulk_modulus_Pa: Positive | None
    acoustic_speed_m_s: Positive | None
    modulus_basis: Literal['isentropic','isothermal','unknown']
    speed_basis: Literal['unbounded_fluid','elastic_pipe_wave','effective_beam','unknown']
    bulk_state_id: Name | None
    speed_state_id: Name | None
    same_state_composition_single_phase_nonrelaxing_acoustics_reviewed: bool = False


class AcousticDensityResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    density_kg_m3: Positive | None = None
    common_state_id: Name | None = None
    issues: tuple[str,...] = ()
    density_identified_from_speed_alone: Literal[False] = False


def observe_acoustic_density(q: AcousticDensityRequest) -> AcousticDensityResult:
    """Invert the linear bulk-fluid acoustic constitutive relation on one state.

    The independent modulus is isentropic. Isothermal compressibility, a flexible
    pipe's wave speed and a reciprocal-time effective beam speed are different
    observables and are not silently converted. The caller supplies the modulus;
    this inverse cannot identify both density and modulus from travel times.
    """
    if not isinstance(q,AcousticDensityRequest):raise TypeError('AcousticDensityRequest required')
    q=AcousticDensityRequest.model_validate(q.model_dump(mode='python'))
    if (q.isentropic_bulk_modulus_Pa is None or q.acoustic_speed_m_s is None
        or q.bulk_state_id is None or q.speed_state_id is None
        or q.modulus_basis=='unknown' or q.speed_basis=='unknown'
        or not q.same_state_composition_single_phase_nonrelaxing_acoustics_reviewed):
        return AcousticDensityResult(status='needs_data',issues=('independent same-state modulus and reviewed fluid acoustic speed required',))
    if q.bulk_state_id!=q.speed_state_id:raise ValueError('modulus and acoustic speed state identities differ')
    if q.modulus_basis!='isentropic' or q.speed_basis!='unbounded_fluid':
        return AcousticDensityResult(status='out_of_scope',common_state_id=q.bulk_state_id,
            issues=('only isentropic bulk modulus and unbounded-fluid acoustic speed identify this density observation',))
    density=(q.isentropic_bulk_modulus_Pa/q.acoustic_speed_m_s)/q.acoustic_speed_m_s
    if not math.isfinite(density) or density<=0:raise ValueError('positive acoustic density exceeds finite numerical support')
    return AcousticDensityResult(status='computed',density_kg_m3=density,common_state_id=q.bulk_state_id)
