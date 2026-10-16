from __future__ import annotations

import hashlib
import json
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
    clean = ["# 视频画面文案（纯净版）", ""]
    timed = ["# 视频画面文案（带时间戳版）", ""]
    review = ["# 视频画面文案核查说明", "", "低置信度段落已标记，请结合关键帧核对。", ""]
    for segment in segments:
        if segment.clean_eligible:
            clean.extend([segment.effective_text, ""])
        timed_text = segment.user_text if segment.user_text is not None else segment.consensus_text
        timed.extend([f"## {format_timestamp(segment.start_time)}", "", timed_text, ""])
        if segment.needs_review or not segment.clean_eligible:
            reason = "未进入纯净版" if not segment.clean_eligible else "建议核查"
            review.extend([
                f"- {format_timestamp(segment.start_time)} · {reason} · 质量分 {segment.confidence:.0%}",
                f"  - `{Path(segment.evidence_frame).name}`",
            ])
    clean_path = output_dir / "视频文案-纯净版.md"
    timed_path = output_dir / "视频文案-带时间戳.md"
    review_path = output_dir / "视频文案-核查说明.md"
    raw_path = output_dir / "原始OCR.jsonl"
    clean_path.write_text("\n".join(clean).rstrip() + "\n", encoding="utf-8")
    timed_path.write_text("\n".join(timed).rstrip() + "\n", encoding="utf-8")
    review_path.write_text("\n".join(review).rstrip() + "\n", encoding="utf-8")
    raw_path.write_text(
        "\n".join(json.dumps(frame.to_dict(), ensure_ascii=False) for frame in frames) + "\n",
        encoding="utf-8",
    )
    generated = [clean_path, timed_path, review_path, raw_path]
    manifest_path = output_dir / "manifest.json"
    manifest = {
        "input_sha256": _sha256(input_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "0.2.0",
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
        "files": [
            {"name": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}
            for path in generated
        ],
    }
    atomic_json(manifest_path, manifest)
    return [*generated, manifest_path]


def regenerate_markdown(output_dir: Path, segments: list[dict]) -> None:
    clean = ["# 视频画面文案（纯净版）", ""]
    timed = ["# 视频画面文案（带时间戳版）", ""]
    for segment in segments:
        text = segment["effective_text"]
        if segment.get("clean_eligible", 1):
            clean.extend([text, ""])
        timed_text = segment.get("user_text")
        if timed_text is None:
            timed_text = segment.get("consensus_text", text)
        timed.extend([f"## {format_timestamp(segment['start_time'])}", "", timed_text, ""])
    (output_dir / "视频文案-纯净版.md").write_text("\n".join(clean).rstrip() + "\n", encoding="utf-8")
    (output_dir / "视频文案-带时间戳.md").write_text("\n".join(timed).rstrip() + "\n", encoding="utf-8")


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

