from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from framecopy.engine.probe import VideoInfo
from framecopy.models import OCRFrame, Segment
from framecopy.storage import atomic_json


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def generate_outputs(
    output_dir: Path,
    input_path: Path,
    info: VideoInfo,
    frames: list[OCRFrame],
    segments: list[Segment],
    *,
    ocr_summary: dict | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    readable_path = write_readable_markdown(
        output_dir,
        "视频画面文案",
        [segment.to_dict() for segment in segments],
    )
    remove_legacy_exports(output_dir)
    generated = [readable_path]
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "input_sha256": _sha256(input_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "0.4.0",
        "extraction_mode": "visual",
        "ocr": ocr_summary or _ocr_summary(frames),
        "video": {
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "codec": info.codec,
        },
        "metrics": {
            "candidate_frames": len(frames),
            "segments": len(segments),
            "clean_segments": sum(1 for item in segments if item.clean_eligible),
            "review_segments": sum(1 for item in segments if item.needs_review),
        },
        "source_cleanup": {"status": "pending", "deleted_at": None},
        "files": [
            {"name": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}
            for path in generated
        ],
    }
    atomic_json(manifest_path, manifest)
    return [*generated, manifest_path]


def regenerate_markdown(output_dir: Path, segments: list[dict]) -> None:
    write_readable_markdown(output_dir, "视频画面文案", segments)
    remove_legacy_exports(output_dir)


def write_readable_markdown(output_dir: Path, title: str, segments: list[dict]) -> Path:
    lines = [f"# {title}", ""]
    for segment in segments:
        text = str(
            segment.get("effective_text")
            or segment.get("clean_text")
            or segment.get("raw_text")
            or ""
        ).strip()
        if text:
            lines.extend([text, ""])
    path = output_dir / "可读文案.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def remove_legacy_exports(output_dir: Path) -> None:
    for name in (
        "视频文案-纯净版.md", "视频文案-带时间戳.md",
        "视频文案-核查说明.md", "原始OCR.jsonl",
        "语音文案-可读版.md", "语音文案-带时间戳.md", "语音字幕.srt",
        "原始语音识别.jsonl", "语音文案-核查说明.md",
    ):
        (output_dir / name).unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ocr_summary(frames: list[OCRFrame]) -> dict[str, object]:
    providers: dict[str, dict[str, object]] = {}
    fallbacks: list[dict[str, str]] = []
    for frame in frames:
        key = f"{frame.provider}:{frame.provider_revision or frame.model}"
        entry = providers.setdefault(key, {
            "name": frame.provider,
            "version": frame.provider_version,
            "model": frame.model,
            "revision": frame.provider_revision,
            "languages": frame.languages,
            "frames": 0,
            "duration_ms": 0.0,
        })
        entry["frames"] = int(entry["frames"]) + 1
        entry["duration_ms"] = round(float(entry["duration_ms"]) + frame.duration_ms, 2)
        if frame.fallback_from:
            event = {
                "from": frame.fallback_from,
                "to": frame.provider,
                "reason": frame.fallback_reason or "unknown",
            }
            if event not in fallbacks:
                fallbacks.append(event)
    return {"providers": list(providers.values()), "fallbacks": fallbacks}

