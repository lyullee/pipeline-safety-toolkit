"""Individual finite-window summaries and supplied-hypothesis signal statistics.

These are mathematical design values under an explicitly reviewed sampling model.
They do not fit sensor data or establish observed leak-detection performance.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive

SmallProbability = Annotated[float, Field(gt=0, lt=0.5)]
SampleCount = Annotated[int, Field(gt=0)]


class GaussianFixedSampleRequest(Contract):
    noise_standard_deviation: Positive | None
    sample_count: SampleCount | None
    false_alarm_probability: SmallProbability | None
    miss_probability: SmallProbability | None
    signal_unit: Name
    null_mean: float = 0.0
    iid_gaussian_known_scale_reviewed: bool = False


class GaussianFixedSampleResult(Contract):
    status: Literal["computed", "needs_data"]
    signal_unit: Name
    mean_standard_error: Positive | None = None
    alarm_threshold: float | None = None
    detectable_positive_mean_shift: Positive | None = None
    false_alarm_normal_quantile: Positive | None = None
    miss_normal_quantile: Positive | None = None
    issues: tuple[str, ...] = ()
    noise_parameters_fitted: Literal[False] = False
    observed_detection_performance_established: Literal[False] = False


def design_gaussian_fixed_sample_test(request: GaussianFixedSampleRequest) -> GaussianFixedSampleResult:
    """Design a one-sided mean-shift test for n IID normal observations.

    Noise standard deviation is the known single-observation scale shared by both
    hypotheses. The upper threshold is based on the sample mean. Detectable shift
    is the alternative-minus-null mean whose modeled miss probability is supplied.
    Requested error probabilities are restricted below one half for a positive
    upper-tail discrimination target. No standard-deviation fit or t-test is used.
    """
    if not isinstance(request, GaussianFixedSampleRequest):
        raise TypeError("GaussianFixedSampleRequest required")
    q=GaussianFixedSampleRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    if not q.iid_gaussian_known_scale_reviewed:
        issues.append("IID Gaussian equal known noise-scale model requires review")
    for name in ("noise_standard_deviation", "sample_count", "false_alarm_probability", "miss_probability"):
        if getattr(q,name) is None:
            issues.append("missing " + name)
    if issues:
        return GaussianFixedSampleResult(status="needs_data",signal_unit=q.signal_unit,issues=tuple(issues))
    try:
        standard_error=q.noise_standard_deviation/math.sqrt(q.sample_count)
        # Lower-tail inversion avoids rounding 1-p to one for small error rates.
        za=-NormalDist().inv_cdf(q.false_alarm_probability)
        zb=-NormalDist().inv_cdf(q.miss_probability)
        shift=(za+zb)*standard_error
        threshold=q.null_mean+za*standard_error
    except (OverflowError,ValueError) as exc:
        raise ValueError("Gaussian test design exceeds finite numerical support") from exc
    if not all(math.isfinite(x) for x in (standard_error,za,zb,shift,threshold)) or standard_error<=0 or shift<=0:
        raise ValueError("Gaussian test design exceeds positive finite numerical support")
    return GaussianFixedSampleResult(status="computed",signal_unit=q.signal_unit,
        mean_standard_error=standard_error,alarm_threshold=threshold,
        detectable_positive_mean_shift=shift,false_alarm_normal_quantile=za,
        miss_normal_quantile=zb)


class FiniteWindowMeanRequest(Contract):
    samples: tuple[float | None, ...] = Field(min_length=1)
    weights: tuple[Nonnegative | None, ...] | None = None
    signal_unit: Name
    clock_id: Name
    window_start_s: float
    window_finish_s: float
    support_and_weight_meaning_reviewed: bool = False

    @model_validator(mode="after")
    def shape(self):
        if self.window_finish_s <= self.window_start_s:
            raise ValueError("positive selected observation window required")
        if not math.isfinite(self.window_finish_s-self.window_start_s):
            raise ValueError("selected observation window exceeds finite numerical support")
        if self.weights is not None and len(self.weights) != len(self.samples):
            raise ValueError("one declared weight per sample required")
        return self


class FiniteWindowMeanResult(Contract):
    status: Literal["computed", "needs_data"]
    mean: float | None = None
    signal_unit: Name
    clock_id: Name
    sample_count: int
    variance_reduction_inferred: Literal[False] = False


def evaluate_finite_window_mean(request: FiniteWindowMeanRequest) -> FiniteWindowMeanResult:
    """Use equal weights by definition, or the supplied finite nonnegative weights.

    Missing observations or weights withhold the mean, even at zero weight. This
    operation does not infer independence or assign recency weights automatically.
    """
    if not isinstance(request,FiniteWindowMeanRequest):
        raise TypeError("FiniteWindowMeanRequest required")
    q=FiniteWindowMeanRequest.model_validate(request.model_dump(mode="python"))
    base=dict(signal_unit=q.signal_unit,clock_id=q.clock_id,sample_count=len(q.samples))
    weights=q.weights if q.weights is not None else (1.0,)*len(q.samples)
    if not q.support_and_weight_meaning_reviewed or None in q.samples or None in weights:
        return FiniteWindowMeanResult(status="needs_data",**base)
    scale=max(weights)
    if scale == 0:
        raise ValueError("at least one positive weight required")
    normalized=tuple(w/scale for w in weights)
    if any(w>0 and n==0 for w,n in zip(weights,normalized)):
        raise ValueError("positive weight is below numerical support")
    total=math.fsum(normalized)
    mean=math.fsum(x*(w/total) for x,w in zip(q.samples,normalized))
    if not math.isfinite(mean):
        raise ValueError("mean exceeds finite numerical support")
    return FiniteWindowMeanResult(status="computed",mean=mean,**base)


class SpatialIncidentRateRequest(Contract):
    segment_lengths_m: tuple[Nonnegative, ...] = Field(min_length=1)
    segment_intensities_per_m_year: tuple[Nonnegative | None, ...]
    piecewise_constant_intensity_and_exposure_reviewed: bool = False

    @model_validator(mode="after")
    def shape(self):
        if len(self.segment_lengths_m) != len(self.segment_intensities_per_m_year):
            raise ValueError("one supplied intensity per segment required")
        return self


class SpatialIncidentRateResult(Contract):
    status: Literal["computed", "needs_data"]
    incident_rate_per_year: Nonnegative | None = None
    occurrence_probability_inferred: Literal[False] = False
    intensity_fitted: Literal[False] = False


def integrate_spatial_incident_rate(request: SpatialIncidentRateRequest) -> SpatialIncidentRateResult:
    """Integrate a supplied piecewise constant per-length yearly intensity.

    No incident database, population exposure, temporal process or probability is
    fitted. A missing intensity is not converted to a zero incident rate.
    """
    if not isinstance(request,SpatialIncidentRateRequest):
        raise TypeError("SpatialIncidentRateRequest required")
    q=SpatialIncidentRateRequest.model_validate(request.model_dump(mode="python"))
    if not q.piecewise_constant_intensity_and_exposure_reviewed or None in q.segment_intensities_per_m_year:
        return SpatialIncidentRateResult(status="needs_data")
    try:
        rate=math.fsum(length*intensity for length,intensity in
            zip(q.segment_lengths_m,q.segment_intensities_per_m_year))
    except OverflowError as exc:
        raise ValueError("incident rate exceeds finite numerical support") from exc
    if not math.isfinite(rate):
        raise ValueError("incident rate exceeds finite numerical support")
    return SpatialIncidentRateResult(status="computed",incident_rate_per_year=rate)


class GaussianSequenceRequest(Contract):
    clock_id: Name
    window_start_s: float
    window_finish_s: float
    sample_times_s: tuple[float, ...] = Field(min_length=1, max_length=1000000)
    samples: tuple[float | None, ...] = Field(max_length=1000000)
    signal_unit: Name
    null_mean: float
    alternative_mean: float | None
    noise_standard_deviation: Positive | None
    iid_gaussian_known_scale_reviewed: bool = False

    @model_validator(mode="after")
    def shape(self):
        if len(self.samples)!=len(self.sample_times_s):
            raise ValueError("sample values and clock coordinates must align")
        if self.window_finish_s<=self.window_start_s:
            raise ValueError("positive selected observation window required")
        if not math.isfinite(self.window_finish_s-self.window_start_s):
            raise ValueError("selected observation window exceeds finite numerical support")
        if any(b<=a for a,b in zip(self.sample_times_s,self.sample_times_s[1:])):
            raise ValueError("sample times must increase strictly without sorting")
        if self.sample_times_s[0]<self.window_start_s or self.sample_times_s[-1]>self.window_finish_s:
            raise ValueError("samples must lie within the selected observation window")
        if self.alternative_mean is not None and self.alternative_mean<=self.null_mean:
            raise ValueError("positive alternative mean shift required")
        return self


class GaussianSPRTRequest(GaussianSequenceRequest):
    false_alarm_probability: SmallProbability | None
    miss_probability: SmallProbability | None


class GaussianOnsetRequest(GaussianSequenceRequest):
    tie_policy: Literal["earliest", "latest"]


class GaussianSequenceResult(Contract):
    status: Literal["accepted_null", "accepted_alternative", "continue", "computed", "needs_data"]
    clock_id: Name
    consumed_samples: int = 0
    unconsumed_samples: int = 0
    score_history: tuple[float, ...] = ()
    lower_boundary: float | None = None
    upper_boundary: float | None = None
    stopping_index_zero_based: int | None = None
    stopping_time_s: float | None = None
    onset_index_zero_based: int | None = None
    onset_time_s: float | None = None
    maximizer_indices_zero_based: tuple[int, ...] = ()
    candidate_suffix_scores: tuple[float, ...] = ()
    hypothesis_parameters_fitted: Literal[False] = False
    field_leak_presence_established: Literal[False] = False


def _gaussian_log_ratio(value, null_mean, alternative_mean, sigma):
    delta=(alternative_mean-null_mean)/sigma
    score=delta*((value-null_mean)/sigma-0.5*delta)
    if not math.isfinite(score):
        raise ValueError("Gaussian log-likelihood exceeds finite numerical support")
    return score


def _compensated_step(total, correction, value):
    updated=total+value
    correction += (total-updated)+value if abs(total)>=abs(value) else (value-updated)+total
    corrected=updated+correction
    if not all(math.isfinite(x) for x in (updated,correction,corrected)):
        raise ValueError("log-likelihood accumulation exceeds finite numerical support")
    return updated,correction,corrected


def evaluate_gaussian_sprt(request: GaussianSPRTRequest) -> GaussianSequenceResult:
    """One pass through ordered observations; first strict boundary crossing stops.

    Boundaries are the usual supplied-error Wald approximations. Samples after a
    stopping decision remain explicitly unconsumed. A missing observation before
    stopping withholds the decision; it is never skipped or imputed.
    """
    if not isinstance(request,GaussianSPRTRequest):
        raise TypeError("GaussianSPRTRequest required")
    q=GaussianSPRTRequest.model_validate(request.model_dump(mode="python"))
    n=len(q.samples)
    if not q.iid_gaussian_known_scale_reviewed or any(x is None for x in
        (q.alternative_mean,q.noise_standard_deviation,q.false_alarm_probability,q.miss_probability)):
        return GaussianSequenceResult(status="needs_data",clock_id=q.clock_id,unconsumed_samples=n)
    lower=math.log(q.miss_probability)-math.log1p(-q.false_alarm_probability)
    upper=math.log1p(-q.miss_probability)-math.log(q.false_alarm_probability)
    total=correction=0.0
    history=[]
    for index,value in enumerate(q.samples):
        if value is None:
            return GaussianSequenceResult(status="needs_data",clock_id=q.clock_id,
                consumed_samples=index,unconsumed_samples=n-index,score_history=tuple(history),
                lower_boundary=lower,upper_boundary=upper)
        increment=_gaussian_log_ratio(value,q.null_mean,q.alternative_mean,q.noise_standard_deviation)
        total,correction,score=_compensated_step(total,correction,increment)
        history.append(score)
        if score>upper or score<lower:
            return GaussianSequenceResult(status="accepted_alternative" if score>upper else "accepted_null",
                clock_id=q.clock_id,consumed_samples=index+1,unconsumed_samples=n-index-1,
                score_history=tuple(history),lower_boundary=lower,upper_boundary=upper,
                stopping_index_zero_based=index,stopping_time_s=q.sample_times_s[index])
    return GaussianSequenceResult(status="continue",clock_id=q.clock_id,consumed_samples=n,
        score_history=tuple(history),lower_boundary=lower,upper_boundary=upper)


def estimate_gaussian_onset(request: GaussianOnsetRequest) -> GaussianSequenceResult:
    """Conditional onset candidate from maximum suffix log-likelihood ratio.

    This scans the complete selected window and always conditions on a change
    model; it does not decide whether a leak exists. Exact ties retain every
    maximizer and use the caller's explicit earliest/latest selection policy.
    """
    if not isinstance(request,GaussianOnsetRequest):
        raise TypeError("GaussianOnsetRequest required")
    q=GaussianOnsetRequest.model_validate(request.model_dump(mode="python"))
    n=len(q.samples)
    if not q.iid_gaussian_known_scale_reviewed or q.alternative_mean is None or q.noise_standard_deviation is None or None in q.samples:
        return GaussianSequenceResult(status="needs_data",clock_id=q.clock_id,unconsumed_samples=n)
    scores=[0.0]*n
    total=correction=0.0
    for index in range(n-1,-1,-1):
        increment=_gaussian_log_ratio(q.samples[index],q.null_mean,q.alternative_mean,q.noise_standard_deviation)
        total,correction,scores[index]=_compensated_step(total,correction,increment)
    scores=tuple(scores)
    best=max(scores);indices=tuple(i for i,value in enumerate(scores) if value==best)
    index=indices[0] if q.tie_policy=="earliest" else indices[-1]
    return GaussianSequenceResult(status="computed",clock_id=q.clock_id,consumed_samples=n,
        onset_index_zero_based=index,onset_time_s=q.sample_times_s[index],
        maximizer_indices_zero_based=indices,candidate_suffix_scores=scores)


class SymmetricGaussianWaldRequest(Contract):
    positive_mean_shift: Positive | None
    noise_standard_deviation: Positive | None
    symmetric_error_probability: SmallProbability | None
    iid_gaussian_known_scale_and_symmetric_errors_reviewed: bool = False


class SymmetricGaussianWaldResult(Contract):
    status: Literal["computed", "needs_data"]
    approximate_expected_observations: Positive | None = None
    below_one_observation: bool | None = None
    exact_stopping_time_claimed: Literal[False] = False


def approximate_symmetric_gaussian_wald_count(request: SymmetricGaussianWaldRequest) -> SymmetricGaussianWaldResult:
    """Return the symmetric-error IID Gaussian Wald count approximation.

    Boundary overshoot is omitted. The real-valued approximation is not rounded
    or clamped to a realizable integer stopping time, even when it falls below one.
    """
    if not isinstance(request,SymmetricGaussianWaldRequest):
        raise TypeError("SymmetricGaussianWaldRequest required")
    q=SymmetricGaussianWaldRequest.model_validate(request.model_dump(mode="python"))
    if not q.iid_gaussian_known_scale_and_symmetric_errors_reviewed or any(x is None for x in
        (q.positive_mean_shift,q.noise_standard_deviation,q.symmetric_error_probability)):
        return SymmetricGaussianWaldResult(status="needs_data")
    alpha=q.symmetric_error_probability
    scale=q.noise_standard_deviation/q.positive_mean_shift
    try:value=2*scale*scale*(1-2*alpha)*(math.log1p(-alpha)-math.log(alpha))
    except OverflowError as exc:raise ValueError("Wald approximation exceeds numerical support") from exc
    if not math.isfinite(value) or value<=0:
        raise ValueError("Wald approximation exceeds positive finite numerical support")
    return SymmetricGaussianWaldResult(status="computed",approximate_expected_observations=value,
        below_one_observation=value<1)
