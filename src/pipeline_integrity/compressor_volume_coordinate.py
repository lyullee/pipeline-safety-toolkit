"""Supplied-coefficient compressor surge and acoustic-terminal volume coordinates."""
import math
from .contracts import Contract,Positive,Nonnegative,Name

class CompressorSurgeVolumeRequest(Contract):
    net_displaced_volume_per_revolution_m3: Positive
    isentropic_exponent: Positive
    suction_temperature_k: Positive
    molar_mass_kg_mol: Positive
    temperature_reference_k: Positive
    molar_mass_reference_kg_mol: Positive
    stage_pressure_ratio: Positive
    supplied_suction_multiplier: Positive | None = None
    supplied_discharge_multiplier: Positive | None = None
    supplied_minimum_volume_m3: Nonnegative | None = None
    coefficient_gas_state_unit_and_applicability_basis_id: Name
    coefficient_reference_scales_and_gas_state_reviewed: bool = False

def evaluate_compressor_surge_volume(request: CompressorSurgeVolumeRequest):
    """Calculate screening volumes with explicit gas-coordinate reference scales.

    A multiplier fitted to Rankine and a molecular-weight number must not be
    reused with unnormalized kelvin and kg/mol. Caller references make that
    numerical calibration explicit. No current standard minimum, pressure pulse
    reduction, vessel design or compressor/acoustic coupling is inferred.
    """
    q=CompressorSurgeVolumeRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.coefficient_gas_state_unit_and_applicability_basis_id,acoustic_attenuation_or_vessel_acceptance_qualified=False)
    if not q.coefficient_reference_scales_and_gas_state_reviewed or any(v is None for v in (q.supplied_suction_multiplier,q.supplied_discharge_multiplier,q.supplied_minimum_volume_m3)):
        return dict(status='needs_data',**base)
    if q.stage_pressure_ratio<1:
        return dict(status='out_of_scope',**base,issues=('stage pressure ratio is discharge/suction and must not represent expansion',))
    try:
        gas=q.isentropic_exponent*(q.suction_temperature_k/q.temperature_reference_k)/(q.molar_mass_kg_mol/q.molar_mass_reference_kg_mol)
        if not math.isfinite(gas) or gas<=0:raise ArithmeticError
        suction=max(q.supplied_minimum_volume_m3,q.supplied_suction_multiplier*q.net_displaced_volume_per_revolution_m3*gas**.25)
        discharge=q.supplied_discharge_multiplier*suction/q.stage_pressure_ratio**.25
        if not all(math.isfinite(v) and v>0 for v in (suction,discharge)):raise ArithmeticError
        discharge=max(q.supplied_minimum_volume_m3,min(suction,discharge))
    except ArithmeticError:
        return dict(status='out_of_scope',**base,issues=('positive surge-volume arithmetic is outside finite support',))
    return dict(status='computed',**base,suction_surge_volume_coordinate_m3=suction,discharge_surge_volume_coordinate_m3=discharge)

class AcousticTerminalVolumeRequest(Contract):
    cylinder_displaced_volume_per_revolution_m3: Positive
    molar_mass_kg_mol: Positive
    molar_mass_reference_kg_mol: Positive
    supplied_exponential_multiplier: Nonnegative | None = None
    supplied_exponential_decay_coefficient: Nonnegative | None = None
    supplied_linear_multiplier: Nonnegative | None = None
    coefficient_unit_and_acoustic_applicability_basis_id: Name
    coefficient_and_terminal_correlation_applicability_reviewed: bool = False

def evaluate_acoustic_terminal_volume(request: AcousticTerminalVolumeRequest):
    """Return an empirical volume coordinate, without a perfect-reflector claim."""
    q=AcousticTerminalVolumeRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.coefficient_unit_and_acoustic_applicability_basis_id,terminal_reflection_or_attenuation_qualified=False)
    if not q.coefficient_and_terminal_correlation_applicability_reviewed or any(v is None for v in (q.supplied_exponential_multiplier,q.supplied_exponential_decay_coefficient,q.supplied_linear_multiplier)):
        return dict(status='needs_data',**base)
    try:
        m=q.molar_mass_kg_mol/q.molar_mass_reference_kg_mol
        if not math.isfinite(m) or m<=0:raise ArithmeticError
        factor=q.supplied_exponential_multiplier*math.exp(-q.supplied_exponential_decay_coefficient*m)+q.supplied_linear_multiplier*m
        volume=q.cylinder_displaced_volume_per_revolution_m3*factor
        if not math.isfinite(factor) or not math.isfinite(volume) or (factor>0 and volume<=0):raise ArithmeticError
        return dict(status='computed',**base,terminal_volume_coordinate_m3=volume)
    except ArithmeticError:
        return dict(status='out_of_scope',**base,issues=('terminal-volume arithmetic is outside finite support',))
