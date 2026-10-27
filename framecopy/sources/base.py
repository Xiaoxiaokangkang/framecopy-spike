from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from framecopy.models import SpeechSegment


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    message: str
    mode: str | None = None


@dataclass(frozen=True)
class AcquiredMedia:
    path: Path
    kind: str = "video"
    title: str | None = None
    duration: float | None = None
    codec: str = "unknown"
    transcript_segments: tuple[SpeechSegment, ...] = ()
    transcript_provider: str = ""
    transcript_provider_version: str = ""
    transcript_model: str = ""
    transcript_language: str = ""
    language_hint: str = ""


ProgressCallback = Callable[[str, int], None]
CancelCallback = Callable[[], bool]


class VideoSourceProvider(ABC):
    @abstractmethod
    def check_environment(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def acquire(
        self,
        source: str | Path | dict[str, str],
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        raise NotImplementedError

