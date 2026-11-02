"""Linear volumetric-elastic compression work for a caller-specified liquid state."""
from dataclasses import dataclass
import math
from numbers import Real


def _number(name: str, value: float, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite real SI value')
    value = float(value)
    if (positive and value <= 0.) or (not positive and value < 0.):
        raise ValueError(f'{name} must be {"positive" if positive else "nonnegative"}')
    return value


@dataclass(frozen=True)
class LinearLiquidCompression:
    """Small-strain linear volumetric elasticity with a constant supplied modulus.

    Pressure is excess over the uncompressed reference state, not absolute
    pressure. Reference volume is the uncompressed liquid inventory. Callers
    must establish that their strain is small enough for constant linear
    compliance. Container strain and temperature effects are not included.
    """
    pressure_excess_pa: float
    reference_volume_m3: float
    bulk_modulus_pa: float

    def __post_init__(self) -> None:
        for name in ('pressure_excess_pa', 'reference_volume_m3', 'bulk_modulus_pa'):
            object.__setattr__(self, name, _number(name, getattr(self, name), positive=name!='pressure_excess_pa'))
        if self.pressure_excess_pa / self.bulk_modulus_pa >= 1.:
            raise ValueError('linear compression would remove the entire reference volume')


@dataclass(frozen=True)
class LinearLiquidCompressionResult:
    volumetric_compression_strain: float
    volume_reduction_m3: float
    stored_elastic_energy_j: float


def evaluate_linear_liquid_compression(request: LinearLiquidCompression) -> LinearLiquidCompressionResult:
    """Return linearized strain, displaced volume and stored elastic work in SI.

    This is stored work relative to the declared reference pressure. It is
    neither a real-fluid thermodynamic path nor available blast/release energy.
    """
    if not isinstance(request, LinearLiquidCompression):
        raise TypeError('request must be LinearLiquidCompression')
    strain = request.pressure_excess_pa/request.bulk_modulus_pa
    reduction = request.reference_volume_m3*strain
    energy = 0.5*request.pressure_excess_pa*reduction
    if not all(math.isfinite(x) for x in (strain, reduction, energy)):
        raise ValueError('compression results exceed finite floating-point range')
    return LinearLiquidCompressionResult(strain, reduction, energy)
