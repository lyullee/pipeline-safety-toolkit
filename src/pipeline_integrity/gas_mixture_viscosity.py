"""Declared low-pressure single-gas viscosity mixing from supplied species data.

No pure-component correlation, material table, EOS or component property fitting
is included. Wilke is the default interaction rule; the simpler mass-root-weight
rule remains explicitly selectable for a caller-reviewed approximation.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Fraction


class ViscosityComponent(Contract):
    id: Name
    fraction: Fraction
    molar_mass_kg_mol: Positive
    dynamic_viscosity_Pa_s: Positive | None


class GasMixtureViscosityRequest(Contract):
    components: tuple[ViscosityComponent,...] = Field(min_length=1,max_length=256)
    fraction_basis: Literal['mole','mass']
    method: Literal['wilke','herning_zipperer'] = 'wilke'
    common_temperature_K: Positive
    common_pressure_Pa_abs: Positive
    low_pressure_single_gas_common_state_and_mixing_accuracy_reviewed: bool = False

    @model_validator(mode='after')
    def aligned(self):
        if len({c.id for c in self.components})!=len(self.components):raise ValueError('component identifiers must be unique')
        if abs(math.fsum(c.fraction for c in self.components)-1)>1e-12:
            raise ValueError('fractions must sum to one; composition is not silently normalized')
        return self


class GasMixtureViscosityResult(Contract):
    status: Literal['computed','needs_data']
    method: Literal['wilke','herning_zipperer']
    component_ids: tuple[str,...] = ()
    mole_fractions: tuple[Fraction,...] = ()
    mean_molar_mass_kg_mol: Positive | None = None
    dynamic_viscosity_Pa_s: Positive | None = None
    issues: tuple[str,...] = ()
    material_accuracy_established: Literal[False] = False


def evaluate_gas_mixture_viscosity(request: GasMixtureViscosityRequest) -> GasMixtureViscosityResult:
    """Apply one explicit mixing rule at the declared common gas state.

    A mass-basis composition is first converted by its supplied molar masses.
    Temperature/pressure identify the common component-data state; they do not
    imply a dense-fluid correction, phase calculation or a universal low-pressure
    threshold. Missing zero-fraction component viscosity is harmless; missing
    active component data is withheld. Hydrogen-rich approximation accuracy must
    be reviewed rather than assumed from positive numerical output.
    """
    q=GasMixtureViscosityRequest.model_validate(request.model_dump(mode='python'))
    issues=[]
    if not q.low_pressure_single_gas_common_state_and_mixing_accuracy_reviewed:
        issues.append('single gas, low-pressure/common-state component data and selected mixing accuracy require review')
    if any(c.fraction>0 and c.dynamic_viscosity_Pa_s is None for c in q.components):issues.append('active component viscosity missing')
    if issues:return GasMixtureViscosityResult(status='needs_data',method=q.method,issues=tuple(issues))
    fractions=[c.fraction if q.fraction_basis=='mole' else c.fraction/c.molar_mass_kg_mol for c in q.components]
    normalization=math.fsum(fractions);y=[x/normalization for x in fractions]
    active=[i for i,x in enumerate(y) if x>0]
    M=[c.molar_mass_kg_mol for c in q.components];mu=[c.dynamic_viscosity_Pa_s for c in q.components]
    if q.method=='herning_zipperer':
        weights=[y[i]*math.sqrt(M[i]) for i in active]
        value=math.fsum(w*mu[i] for w,i in zip(weights,active))/math.fsum(weights)
    else:
        contributions=[]
        for i in active:
            denominator=math.fsum(y[j]*(1+math.sqrt(mu[i]/mu[j])*(M[j]/M[i])**.25)**2/
                math.sqrt(8*(1+M[i]/M[j])) for j in active)
            contributions.append(y[i]*mu[i]/denominator)
        value=math.fsum(contributions)
    mean=math.fsum(x*m for x,m in zip(y,M))
    if not all(math.isfinite(x) and x>0 for x in (value,mean)):raise ValueError('mixture response exceeds finite positive support')
    return GasMixtureViscosityResult(status='computed',method=q.method,component_ids=tuple(c.id for c in q.components),
        mole_fractions=tuple(y),mean_molar_mass_kg_mol=mean,dynamic_viscosity_Pa_s=value)
