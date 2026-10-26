from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from framecopy.engine.markdown import remove_legacy_exports, write_readable_markdown
from framecopy.engine.probe import VideoInfo
from framecopy.engine.speech import SpeechResult
from framecopy.models import SpeechSegment
from framecopy.storage import atomic_json


def generate_speech_outputs(
    output_dir: Path,
    input_path: Path,
    info: VideoInfo,
    result: SpeechResult,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    readable = write_readable_markdown(
        output_dir,
        "语音文案",
        [item.to_dict() for item in result.segments],
    )
    remove_legacy_exports(output_dir)
    generated = [readable]
    manifest = {
        "extraction_mode": "speech",
        "input_sha256": _sha256(input_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "0.5.0",
        "speech": {
            "provider": result.provider,
            "provider_version": result.provider_version,
            "model": result.model,
            "language": result.language,
            "duration_ms": result.duration_ms,
        },
        "video": {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "codec": info.codec,
        },
        "metrics": {"segments": len(result.segments)},
        "source_cleanup": {"status": "pending", "deleted_at": None},
        "files": [
            {"name": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}
            for path in generated
        ],
    }
    manifest_path = output_dir / "manifest.json"
    atomic_json(manifest_path, manifest)
    return [*generated, manifest_path]


def regenerate_speech_outputs(output_dir: Path, segments: list[dict]) -> None:
    write_readable_markdown(output_dir, "语音文案", segments)
    remove_legacy_exports(output_dir)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
