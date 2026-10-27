from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from framecopy.config import Settings
from framecopy.errors import error


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    size: int


def _fraction(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator or 1)
    return float(value)


def probe_video(path: Path, settings: Settings) -> VideoInfo:
    return probe_media(path, settings, require_video=True, require_audio=False)


def probe_media(
    path: Path,
    settings: Settings,
    *,
    require_video: bool,
    require_audio: bool,
) -> VideoInfo:
    if not shutil.which("ffprobe"):
        raise error("UNSUPPORTED_MEDIA", detail="本机尚未安装 FFmpeg")
    process = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration,size",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        raise error("MEDIA_CORRUPTED")
    try:
        payload = json.loads(process.stdout)
        streams = payload["streams"]
        video_stream = next(
            (item for item in streams if item.get("codec_type") == "video"), None
        )
        audio_stream = next(
            (item for item in streams if item.get("codec_type") == "audio"), None
        )
        if require_video and video_stream is None:
            raise KeyError("video stream missing")
        if require_audio and audio_stream is None:
            raise KeyError("audio stream missing")
        stream = video_stream or audio_stream or streams[0]
        fmt = payload["format"]
        info = VideoInfo(
            duration=float(fmt["duration"]),
            width=int((video_stream or {}).get("width") or 0),
            height=int((video_stream or {}).get("height") or 0),
            fps=_fraction((video_stream or {}).get("avg_frame_rate", "0")),
            codec=str(stream.get("codec_name", "unknown")),
            size=int(fmt.get("size", path.stat().st_size)),
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise error("UNSUPPORTED_MEDIA")
    if info.size > settings.max_upload_bytes:
        raise error("FILE_TOO_LARGE")
    if info.duration > settings.max_duration_seconds:
        raise error("DURATION_EXCEEDED")
    if info.width and info.height and info.width * info.height > settings.max_resolution_pixels:
        raise error("UNSUPPORTED_MEDIA", detail="视频分辨率超过 4K")
    return info

