from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCalibration:
    review_threshold: float
    clean_single_frame_threshold: float
    clean_isolated_threshold: float


CALIBRATIONS: dict[str, ProviderCalibration] = {
    "apple_vision": ProviderCalibration(
        review_threshold=0.72,
        clean_single_frame_threshold=0.78,
        clean_isolated_threshold=0.90,
    ),
    "paddleocr": ProviderCalibration(
        review_threshold=0.82,
        clean_single_frame_threshold=0.88,
        clean_isolated_threshold=0.96,
    ),
}

DEFAULT_CALIBRATION = ProviderCalibration(
    review_threshold=0.82,
    clean_single_frame_threshold=0.88,
    clean_isolated_threshold=0.96,
)


def calibration_for(provider: str) -> ProviderCalibration:
    return CALIBRATIONS.get(provider, DEFAULT_CALIBRATION)


def calibrated_confidence(provider: str, raw: float) -> float:
    """Map provider-local confidence into a review-oriented score.

    This deliberately avoids comparing raw Apple Vision and PaddleOCR scores.
    The output is only used within the provider's calibrated thresholds.
    """
    calibration = calibration_for(provider)
    threshold = calibration.review_threshold
    value = max(0.0, min(1.0, raw))
    if value >= threshold:
        return 0.5 + 0.5 * (value - threshold) / max(1e-6, 1.0 - threshold)
    return 0.5 * value / max(1e-6, threshold)
