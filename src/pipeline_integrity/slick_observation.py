"""Volume interval from a supplied appearance-to-thickness calibration."""
from pydantic import Field, model_validator
from .contracts import Contract, Name, Nonnegative, Positive, json_value


class SlickThicknessBand(Contract):
    appearance: Name
    minimum_thickness_m: Nonnegative
    maximum_thickness_m: Positive

    @model_validator(mode='after')
    def ordered(self):
        if self.minimum_thickness_m > self.maximum_thickness_m:
            raise ValueError('thickness interval is reversed')
        return self


class SlickObservationRequest(Contract):
    appearance: Name
    observed_area_m2: Nonnegative
    calibrated_bands: tuple[SlickThicknessBand, ...] = Field(min_length=1)
    calibration_id: Name
    environment_and_visual_method_reviewed: bool = False


def evaluate_slick_observation(q: SlickObservationRequest) -> dict:
    q = SlickObservationRequest.model_validate(q.model_dump(mode='python'))
    labels = [b.appearance for b in q.calibrated_bands]
    if len(set(labels)) != len(labels):
        raise ValueError('appearance categories must be unique')
    band = next((b for b in q.calibrated_bands if b.appearance == q.appearance), None)
    if band is None or not q.environment_and_visual_method_reviewed:
        return dict(status='needs_data', calibration_id=q.calibration_id)
    return json_value(dict(status='computed', minimum_thickness_m=band.minimum_thickness_m,
                maximum_thickness_m=band.maximum_thickness_m,
                minimum_volume_m3=q.observed_area_m2*band.minimum_thickness_m,
                maximum_volume_m3=q.observed_area_m2*band.maximum_thickness_m,
                calibration_id=q.calibration_id))
