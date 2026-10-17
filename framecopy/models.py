from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    RESOLVING_LINK = "resolving_link"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    PREPARING = "preparing"
    ANALYZING = "analyzing"
    EXTRACTING_FRAMES = "extracting_frames"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    RECOGNIZING = "recognizing"
    MERGING = "merging"
    GENERATING = "generating"
    CLEANING_SOURCE = "cleaning_source"
    READY = "ready"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class ExtractionMode(str, Enum):
    VISUAL = "visual"
    SPEECH = "speech"


@dataclass
class OCRLine:
    text: str
    confidence: float
    bbox: list[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OCRFrame:
    timestamp: float
    image_path: str
    lines: list[OCRLine] = field(default_factory=list)
    sharpness: float = 0.0
    image_hash: str = ""
    provider: str = "unknown"
    provider_version: str = "unknown"
    model: str = "unknown"
    provider_revision: str = ""
    languages: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    fallback_from: str | None = None
    fallback_reason: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(line.text.strip() for line in self.lines if line.text.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.timestamp,
            "image": self.image_path,
            "sharpness": self.sharpness,
            "image_hash": self.image_hash,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "model": self.model,
            "provider_revision": self.provider_revision,
            "languages": self.languages,
            "duration_ms": self.duration_ms,
            "fallback_from": self.fallback_from,
            "fallback_reason": self.fallback_reason,
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass
class Segment:
    sequence: int
    start_time: float
    end_time: float
    raw_text: str
    consensus_text: str
    clean_text: str
    confidence: float
    evidence_frame: str
    needs_review: bool = False
    clean_eligible: bool = True
    user_text: str | None = None

    @property
    def effective_text(self) -> str:
        return self.user_text if self.user_text is not None else self.clean_text

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["effective_text"] = self.effective_text
        return data


@dataclass
class SpeechSegment:
    sequence: int
    start_time: float
    end_time: float
    raw_text: str
    clean_text: str
    confidence: float = 0.0
    language: str = ""
    needs_review: bool = False
    user_text: str | None = None

    @property
    def effective_text(self) -> str:
        return self.user_text if self.user_text is not None else self.clean_text

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["effective_text"] = self.effective_text
        return data

