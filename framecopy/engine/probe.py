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
    if not shutil.which("ffprobe"):
        raise error("UNSUPPORTED_MEDIA", detail="本机尚未安装 FFmpeg")
    process = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate:format=duration,size",
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
        stream = payload["streams"][0]
        fmt = payload["format"]
        info = VideoInfo(
            duration=float(fmt["duration"]),
            width=int(stream["width"]),
            height=int(stream["height"]),
            fps=_fraction(stream.get("avg_frame_rate", "0")),
            codec=str(stream.get("codec_name", "unknown")),
            size=int(fmt.get("size", path.stat().st_size)),
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise error("UNSUPPORTED_MEDIA")
    if info.size > settings.max_upload_bytes:
        raise error("FILE_TOO_LARGE")
    if info.duration > settings.max_duration_seconds:
        raise error("DURATION_EXCEEDED")
    if info.width * info.height > settings.max_resolution_pixels:
        raise error("UNSUPPORTED_MEDIA", detail="视频分辨率超过 4K")
    return info

