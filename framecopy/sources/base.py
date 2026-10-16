from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    message: str
    mode: str | None = None


ProgressCallback = Callable[[str, int], None]
CancelCallback = Callable[[], bool]


class VideoSourceProvider(ABC):
    @abstractmethod
    def check_environment(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def acquire(
        self,
        source: str | Path,
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> Path:
        raise NotImplementedError

