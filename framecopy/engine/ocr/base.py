from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from framecopy.models import OCRLine


CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class OCRProviderInfo:
    name: str
    version: str
    model: str
    revision: str = ""
    languages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "revision": self.revision,
            "languages": list(self.languages),
        }


@dataclass(frozen=True)
class OCRResult:
    lines: list[OCRLine]
    provider: OCRProviderInfo
    duration_ms: float
    fallback_from: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class OCRProviderStatus:
    available: bool
    message: str
    details: dict[str, str] = field(default_factory=dict)


class OCRProviderError(RuntimeError):
    pass


class OCRProvider(Protocol):
    @property
    def info(self) -> OCRProviderInfo: ...

    def check_environment(self) -> OCRProviderStatus: ...

    def recognize(self, image_path: Path, cancelled: CancelCheck | None = None) -> OCRResult: ...
