"""Explicit geometry assignment for a reviewed approximate ground-bed model.

An effective conductive envelope is supplied data. This adapter does not infer
coke conductivity or solve an equipotential finite metal/backfill boundary.
"""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from .contracts import Contract,Name,Positive
from .earth_electrode import EarthElectrodeRequest,EarthElectrodeResult,SlenderEarthElectrode,evaluate_earth_electrodes,_direction


class GroundbedEnvelopeRequest(Contract):
    effective_envelope_ground: EarthElectrodeRequest
    metal_core_geometries: tuple[SlenderEarthElectrode,...]
    core_to_envelope_id: dict[Name,Name]
    effective_geometry_basis_id: Name | None
    uniform_leakage_effective_envelope_approximation_selected: bool = False
    internal_backfill_model: Literal['effective_envelope_only','difference_of_homogeneous_self_estimates'] = 'effective_envelope_only'
    uniform_backfill_resistivity_ohm_m_by_core: dict[Name,Positive] | None = None
    backfill_difference_approximation_reviewed: bool = False


class GroundbedEnvelopeResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    effective_geometry_basis_id: Name | None
    core_to_envelope_id: dict[Name,Name]
    approximate_ground: EarthElectrodeResult | None = None
    internal_backfill_resistance_ohm_by_core: dict[Name,float] | None = None
    total_resistance_matrix_ohm: tuple[tuple[float,...],...] = ()
    signed_currents_A: tuple[float,...] = ()
    total_rod_average_potentials_V: tuple[float,...] = ()
    estimated_common_bus_resistance_ohm: Positive | None = None
    total_model_ohmic_power_W: float | None = None
    internal_model_ohmic_power_W: float | None = None
    issues: tuple[str,...] = ()
    backfill_conductivity_or_exact_metal_equipotential_solved: Literal[False] = False
    stratified_soil_or_field_calibration_qualified: Literal[False] = False


def evaluate_groundbed_effective_envelope(q:GroundbedEnvelopeRequest)->GroundbedEnvelopeResult:
    """Assign explicit outer geometry to the unchanged rod-average estimator.

Every core must fit coaxially inside its assigned finite envelope. The caller
must separately justify this approximate effective geometry, including any
conductive backfill. Currents belong to the effective-envelope request; the
cores here carry geometry only. No averaging of finite backfill/soil material
properties, zero contact resistance or true equipotential metal is inferred.
"""
    q=GroundbedEnvelopeRequest.model_validate(q.model_dump(mode='python'))
    common=dict(effective_geometry_basis_id=q.effective_geometry_basis_id,core_to_envelope_id=q.core_to_envelope_id)
    if q.effective_geometry_basis_id is None or not q.uniform_leakage_effective_envelope_approximation_selected:
        return GroundbedEnvelopeResult(status='needs_data',**common,issues=('Caller-reviewed effective geometry and approximate uniform-leakage assignment required; backfill properties are not inferred.',))
    cores={r.id:r for r in q.metal_core_geometries};envelopes={r.id:r for r in q.effective_envelope_ground.electrodes}
    if len(cores)!=len(q.metal_core_geometries) or len(envelopes)!=len(q.effective_envelope_ground.electrodes):raise ValueError('Unique core/envelope identities required.')
    if set(q.core_to_envelope_id)!=set(cores) or set(q.core_to_envelope_id.values())!=set(envelopes) or len(set(q.core_to_envelope_id.values()))!=len(cores):
        raise ValueError('Every physical core and effective envelope require an explicit one-to-one assignment.')
    for identity,core in cores.items():
        if core.signed_current_into_ground_A is not None:raise ValueError('Core entries describe geometry only; prescribe current on the effective ground model.')
        envelope=envelopes[q.core_to_envelope_id[identity]];u=_direction(envelope);v=_direction(core)
        if not math.isclose(abs(float(u@v)),1.,rel_tol=0,abs_tol=1e-12):raise ValueError('Core and effective envelope must be coaxial.')
        origin=np.array(envelope.start_xyz_depth_m);start=np.array(core.start_xyz_depth_m)
        for point in (start,start+core.length_m*v):
            delta=point-origin;axial=float(delta@u);radial=float(np.linalg.norm(delta-axial*u))
            if axial < -1e-12 or axial>envelope.length_m+1e-12 or radial+core.radius_m>envelope.radius_m*(1+1e-12):
                raise ValueError('Physical core must lie inside the complete assigned effective envelope.')
    if q.internal_backfill_model=='difference_of_homogeneous_self_estimates':
        if not q.backfill_difference_approximation_reviewed or q.uniform_backfill_resistivity_ohm_m_by_core is None or set(q.uniform_backfill_resistivity_ohm_m_by_core)!=set(cores):
            return GroundbedEnvelopeResult(status='needs_data',**common,issues=('Every core needs explicitly identified uniform backfill resistivity and a reviewed self-estimate difference approximation.',))
    result=evaluate_earth_electrodes(q.effective_envelope_ground)
    if result.status!='computed':return GroundbedEnvelopeResult(status=result.status,**common,approximate_ground=result,issues=result.issues)
    internal={identity:0. for identity in cores}
    if q.internal_backfill_model=='difference_of_homogeneous_self_estimates':
        for identity,core in cores.items():
            envelope=envelopes[q.core_to_envelope_id[identity]];rho=q.uniform_backfill_resistivity_ohm_m_by_core[identity]
            def self_resistance(geometry):
                control=EarthElectrodeRequest(uniform_resistivity_ohm_m=rho,electrodes=(geometry.model_copy(update={'signed_current_into_ground_A':None}),),
                    mode='rod_average_common_bus',total_signed_current_A=1.,coordinate_frame_id=q.effective_envelope_ground.coordinate_frame_id,
                    uniform_leakage_slender_line_and_homogeneous_soil_reviewed=True,current_return_at_infinity_reviewed=True,
                    quadrature_relative_tolerance=q.effective_envelope_ground.quadrature_relative_tolerance,
                    maximum_quadrature_order=q.effective_envelope_ground.maximum_quadrature_order)
                observed=evaluate_earth_electrodes(control)
                if observed.status!='computed':raise ValueError('Internal homogeneous self estimate is unresolved: '+str(observed.issues))
                return observed.common_bus_resistance_ohm
            internal[identity]=self_resistance(core)-self_resistance(envelope)
            if internal[identity]<0:
                return GroundbedEnvelopeResult(status='out_of_scope',**common,approximate_ground=result,internal_backfill_resistance_ohm_by_core=internal,
                    issues=('Self-estimate difference is negative for supplied geometry; no unphysical series resistance clipped.',))
    ordered=q.effective_envelope_ground.electrodes
    core_by_envelope={v:k for k,v in q.core_to_envelope_id.items()}
    diagonal=np.array([internal[core_by_envelope[g.id]] for g in ordered])
    resistance=np.array(result.resistance_matrix_ohm)+np.diag(diagonal)
    if np.min(np.linalg.eigvalsh(resistance))<=0 or np.linalg.cond(resistance)>1e12:
        return GroundbedEnvelopeResult(status='out_of_scope',**common,issues=('Augmented resistance matrix is not numerically resolved positive definite.',))
    if q.effective_envelope_ground.mode=='rod_average_common_bus':
        conductance=np.linalg.solve(resistance,np.ones(len(ordered)));bus=1/float(np.sum(conductance))
        currents=q.effective_envelope_ground.total_signed_current_A*bus*conductance
    else:currents=np.array(result.signed_currents_A);bus=None
    # Reuse the same exterior field owner with the corrected electrode currents.
    # Series voltage drops belong to the approximate core response, not to soil
    # observation fields; no local backfill field is silently constructed.
    if q.internal_backfill_model!='effective_envelope_only':
        exterior=q.effective_envelope_ground.model_copy(update={'mode':'prescribed_currents','total_signed_current_A':None,
            'electrodes':tuple(g.model_copy(update={'signed_current_into_ground_A':float(i)}) for g,i in zip(ordered,currents))})
        result=evaluate_earth_electrodes(exterior)
        if result.status!='computed':return GroundbedEnvelopeResult(status=result.status,**common,approximate_ground=result,issues=result.issues)
    potential=resistance@currents;internal_power=float(np.sum(currents*currents*diagonal))
    return GroundbedEnvelopeResult(status='computed',**common,approximate_ground=result,internal_backfill_resistance_ohm_by_core=internal,
        total_resistance_matrix_ohm=tuple(map(tuple,resistance)),signed_currents_A=tuple(currents),
        total_rod_average_potentials_V=tuple(potential),estimated_common_bus_resistance_ohm=bus,
        total_model_ohmic_power_W=float(currents@potential),internal_model_ohmic_power_W=internal_power,issues=result.issues)
