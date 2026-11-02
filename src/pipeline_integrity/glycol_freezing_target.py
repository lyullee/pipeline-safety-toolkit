"""Concentration inversion of an explicitly selected aqueous-glycol curve."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative
from .volume_weighting import normalize_component_volumes


class GlycolFreezingTargetRequest(Contract):
    glycol_species: Literal['ethylene_glycol','propylene_glycol'] | None
    target_freezing_temperature_K: Positive | None
    mass_fraction_bracket: tuple[Nonnegative,Nonnegative]
    requested_concentration_basis: Literal['mass','charged_component_volume']
    reviewed_curve_and_single_decreasing_branch: bool = False
    curve_evidence_id: Name
    glycol_stock_density_kg_m3: Positive | None = None
    water_stock_density_kg_m3: Positive | None = None
    stock_reference_temperature_K: Positive | None = None
    stock_reference_pressure_Pa_abs: Positive | None = None
    same_state_pure_stock_density_basis_reviewed: bool = False
    maximum_root_iterations: int = Field(default=100,ge=1,le=1000)

    @model_validator(mode='after')
    def bracket(self):
        a,b=self.mass_fraction_bracket
        if not 0<=a<b<=1:raise ValueError('ordered mass fractions within0..1 required')
        return self


class GlycolFreezingTargetResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','not_bracketed','not_converged']
    glycol_species: str | None
    curve_evidence_id: Name
    required_glycol_mass_fraction: Nonnegative | None = None
    required_charged_glycol_volume_fraction: Nonnegative | None = None
    predicted_freezing_temperature_K: Positive | None = None
    target_residual_K: float | None = None
    target_state: Literal['at_lower_bound','interior_root'] | None = None
    provider_identity: Name | None = None
    provider_version: Name | None = None
    iterations: int | None = None
    issues: tuple[str,...] = ()
    actual_bulk_volume_additivity_or_pressure_dependence_inferred: Literal[False] = False
    field_freeze_protection_qualified: Literal[False] = False


class CoolPropGlycolFreezingCurve:
    """Use native mass-based MEG/MPG fits, without copying a property table.

    This is a reference freezing curve, not a pressure-dependent phase EOS.
    Supported mass fractions are those documented for native MEG/MPG0..0.6.
    Commercial inhibitor packages and unspecified 'glycol' are not substituted.
    """
    def __init__(self):
        from importlib.metadata import version
        from CoolProp.CoolProp import get_global_param_string
        self.provider_identity='CoolProp-INCOMP-MEG-MPG-mass-freezing-curves'
        self.provider_version=version('CoolProp')+'-'+get_global_param_string('gitrevision')
    def freezing_temperature_K(self,species,mass_fraction):
        from CoolProp.CoolProp import PropsSI
        if species not in ('ethylene_glycol','propylene_glycol') or not 0<=mass_fraction<=.6:
            raise ValueError('native MEG/MPG supports explicit species and mass fraction0..0.6')
        identifier='MEG' if species=='ethylene_glycol' else 'MPG'
        return float(PropsSI('T_freeze',f'INCOMP::{identifier}[{mass_fraction:.17g}]'))


def evaluate_glycol_freezing_target(q: GlycolFreezingTargetRequest, *, curve_provider=None) -> GlycolFreezingTargetResult:
    """Find the least concentration on a reviewed decreasing freezing branch.

    For volume output, pure-stock masses are converted using supplied densities
    at one explicit charging reference state. These are charged-component volume
    fractions, not actual mixture partial-volume fractions; contraction is not
    inferred. Missing conversion data preserves the computed mass fraction.
    """
    from scipy.optimize import brentq
    q=GlycolFreezingTargetRequest.model_validate(q.model_dump(mode='python'))
    base=dict(glycol_species=q.glycol_species,curve_evidence_id=q.curve_evidence_id)
    if q.glycol_species is None or q.target_freezing_temperature_K is None or not q.reviewed_curve_and_single_decreasing_branch:
        return GlycolFreezingTargetResult(status='needs_data',**base,issues=('explicit species, target temperature and reviewed property curve/branch required',))
    p=curve_provider if curve_provider is not None else CoolPropGlycolFreezingCurve();identity=(p.provider_identity,p.provider_version)
    def temperature(w):
        t=float(p.freezing_temperature_K(q.glycol_species,w))
        if (p.provider_identity,p.provider_version)!=identity:raise ValueError('freezing curve provider identity changed')
        if not math.isfinite(t) or t<=0:raise ValueError('positive finite freezing temperature required')
        return t
    lo,hi=q.mass_fraction_bracket
    try:
        grid=[temperature(lo+(hi-lo)*i/32) for i in range(33)]
    except (ValueError,RuntimeError) as exc:
        return GlycolFreezingTargetResult(status='out_of_scope',**base,issues=(str(exc),))
    if any(a<=b for a,b in zip(grid,grid[1:])):
        return GlycolFreezingTargetResult(status='out_of_scope',**base,issues=('supplied branch is not strictly decreasing; no global root or eutectic crossing is inferred',))
    target=q.target_freezing_temperature_K
    if grid[0]<=target:w=lo;state='at_lower_bound';iterations=0
    elif grid[-1]>target:
        return GlycolFreezingTargetResult(status='not_bracketed',**base,provider_identity=identity[0],provider_version=identity[1],issues=('requested protection lies beyond supplied concentration branch',))
    else:
        try:w,root=brentq(lambda x:temperature(x)-target,lo,hi,xtol=1e-13,rtol=1e-13,maxiter=q.maximum_root_iterations,full_output=True,disp=False)
        except (ValueError,RuntimeError) as exc:
            return GlycolFreezingTargetResult(status='not_converged',**base,issues=(str(exc),))
        if not root.converged:return GlycolFreezingTargetResult(status='not_converged',**base,iterations=root.iterations)
        state='interior_root';iterations=root.iterations
    t=temperature(w);volume=None;issues=[]
    if q.requested_concentration_basis=='charged_component_volume':
        if w==0:volume=0.
        elif (None in (q.glycol_stock_density_kg_m3,q.water_stock_density_kg_m3,q.stock_reference_temperature_K,q.stock_reference_pressure_Pa_abs)
            or not q.same_state_pure_stock_density_basis_reviewed):issues.append('mass fraction is known; charged-volume output needs both pure-stock densities at a reviewed common reference P/T')
        else:
            _,fractions=normalize_component_volumes({'glycol':w/q.glycol_stock_density_kg_m3,'water':(1-w)/q.water_stock_density_kg_m3})
            volume=fractions['glycol']
    return GlycolFreezingTargetResult(status='needs_data' if issues else 'computed',**base,required_glycol_mass_fraction=w,
        required_charged_glycol_volume_fraction=volume,predicted_freezing_temperature_K=t,target_residual_K=t-target,
        target_state=state,provider_identity=identity[0],provider_version=identity[1],iterations=iterations,issues=tuple(issues))
