from __future__ import annotations

import platform
from pathlib import Path

from framecopy.config import Settings
from framecopy.engine.ocr.apple_vision import AppleVisionOCRProvider
from framecopy.engine.ocr.base import (
    CancelCheck,
    OCRProvider,
    OCRProviderError,
    OCRProviderInfo,
    OCRProviderStatus,
    OCRResult,
)
from framecopy.engine.ocr.paddle import PaddleOCRProvider, _normalize_results, _reading_order


class FallbackOCRProvider:
    """Use one primary provider and visibly degrade to one fallback provider."""

    def __init__(self, primary: OCRProvider, fallback: OCRProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self._active = primary
        self._fallback_from: str | None = None
        self._fallback_reason: str | None = None

    @property
    def info(self) -> OCRProviderInfo:
        return self._active.info

    @property
    def fallback_event(self) -> dict[str, str] | None:
        if not self._fallback_from:
            return None
        return {
            "from": self._fallback_from,
            "to": self._active.info.name,
            "reason": self._fallback_reason or "unknown",
        }

    def check_environment(self) -> OCRProviderStatus:
        primary = self.primary.check_environment()
        if primary.available:
            return primary
        if not self.fallback:
            return primary
        fallback = self.fallback.check_environment()
        if fallback.available:
            return OCRProviderStatus(True, f"{primary.message}；将回退到 {self.fallback.info.name}")
        return OCRProviderStatus(False, f"{primary.message}；{fallback.message}")

    def recognize(self, image_path: Path, cancelled: CancelCheck | None = None) -> OCRResult:
        if self._active is not self.primary:
            result = self._active.recognize(image_path, cancelled)
            return _with_fallback(result, self._fallback_from, self._fallback_reason)
        try:
            return self.primary.recognize(image_path, cancelled)
        except OCRProviderError as exc:
            if not self.fallback:
                raise
            status = self.fallback.check_environment()
            if not status.available:
                raise OCRProviderError(f"{exc}；回退引擎不可用：{status.message}") from exc
            self._fallback_from = self.primary.info.name
            self._fallback_reason = str(exc)
            self._active = self.fallback
            result = self._active.recognize(image_path, cancelled)
            return _with_fallback(result, self._fallback_from, self._fallback_reason)


def _with_fallback(result: OCRResult, source: str | None, reason: str | None) -> OCRResult:
    return OCRResult(
        lines=result.lines,
        provider=result.provider,
        duration_ms=result.duration_ms,
        fallback_from=source,
        fallback_reason=reason,
    )


def create_ocr_provider(settings: Settings) -> FallbackOCRProvider:
    requested = settings.ocr_provider
    paddle = PaddleOCRProvider()
    apple = AppleVisionOCRProvider(
        settings.data_dir / "tools",
        timeout_seconds=settings.ocr_timeout_seconds,
        languages=settings.apple_vision_languages,
    )
    if requested == "auto":
        primary: OCRProvider = apple if platform.system() == "Darwin" else paddle
    elif requested in {"apple", "apple_vision", "vision"}:
        primary = apple
    elif requested in {"paddle", "paddleocr"}:
        primary = paddle
    else:
        raise OCRProviderError(f"未知 OCR Provider：{requested}")
    fallback: OCRProvider | None = None
    if settings.ocr_fallback_enabled and primary.info.name != "paddleocr":
        fallback = paddle
    return FallbackOCRProvider(primary, fallback)


__all__ = [
    "AppleVisionOCRProvider",
    "FallbackOCRProvider",
    "OCRProvider",
    "OCRProviderError",
    "OCRProviderInfo",
    "OCRProviderStatus",
    "OCRResult",
    "PaddleOCRProvider",
    "create_ocr_provider",
    "_normalize_results",
    "_reading_order",
]
