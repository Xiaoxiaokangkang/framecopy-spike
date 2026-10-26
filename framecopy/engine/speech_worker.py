from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import wave
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--progress-file", required=True)
    parser.add_argument("--checkpoint-file", required=True)
    parser.add_argument("--result-file", required=True)
    parser.add_argument("--chunk-seconds", type=int, default=240)
    parser.add_argument("--overlap-seconds", type=int, default=2)
    args = parser.parse_args()

    import mlx_whisper
    import numpy as np

    input_path = Path(args.input)
    progress_path = Path(args.progress_file)
    checkpoint_path = Path(args.checkpoint_file)
    result_path = Path(args.result_file)
    chunk_seconds = max(30, args.chunk_seconds)
    overlap_seconds = max(0, min(args.overlap_seconds, chunk_seconds - 1))

    with wave.open(str(input_path), "rb") as audio:
        sample_rate = audio.getframerate()
        total_frames = audio.getnframes()
        channels = audio.getnchannels()
        sample_width = audio.getsampwidth()
        compression = audio.getcomptype()

    if channels != 1 or sample_width != 2 or compression != "NONE":
        raise RuntimeError("语音音轨不是预期的16位单声道 PCM 格式")

    ranges = _chunk_ranges(total_frames, sample_rate, chunk_seconds, overlap_seconds)
    signature = {
        "input_size": input_path.stat().st_size,
        "input_mtime_ns": input_path.stat().st_mtime_ns,
        "model": args.model,
        "chunk_seconds": chunk_seconds,
        "overlap_seconds": overlap_seconds,
        "total_frames": total_frames,
        "sample_rate": sample_rate,
    }
    checkpoint = _load_checkpoint(checkpoint_path, signature)
    completed = min(int(checkpoint.get("completed") or 0), len(ranges))
    segments = list(checkpoint.get("segments") or [])
    language = str(checkpoint.get("language") or "")

    for index, (start_frame, end_frame) in enumerate(ranges[completed:], completed + 1):
        _write_json_atomic(progress_path, {
            "state": "recognizing",
            "current": index,
            "total": len(ranges),
        })
        samples = _read_pcm_chunk(input_path, start_frame, end_frame, np)
        options: dict[str, Any] = {
            "path_or_hf_repo": args.model,
            "verbose": None,
            # Reset Whisper's rolling prompt inside each bounded chunk. This is
            # less prone to repetition loops and timestamp drift on long audio.
            "condition_on_previous_text": False,
        }
        if language:
            options["language"] = language
        result = mlx_whisper.transcribe(samples, **options)
        language = language or str(result.get("language") or "")
        chunk_start = start_frame / sample_rate
        commit_after = None if index == 1 else chunk_start + overlap_seconds
        chunk_segments = _offset_segments(
            result.get("segments", []),
            offset=chunk_start,
            commit_after=commit_after,
        )
        _append_without_text_overlap(segments, chunk_segments)
        completed = index
        _write_json_atomic(checkpoint_path, {
            "signature": signature,
            "completed": completed,
            "segments": segments,
            "language": language,
        })
        _write_json_atomic(progress_path, {
            "state": "completed",
            "current": completed,
            "total": len(ranges),
        })

    _write_json_atomic(result_path, {
        "provider": "mlx_whisper",
        "provider_version": importlib.metadata.version("mlx-whisper"),
        "language": language,
        "segments": segments,
        "chunks_total": len(ranges),
    })
    return 0


def _chunk_ranges(
    total_frames: int,
    sample_rate: int,
    chunk_seconds: int,
    overlap_seconds: int,
) -> list[tuple[int, int]]:
    if total_frames <= 0 or sample_rate <= 0:
        return []
    chunk_frames = max(1, sample_rate * max(1, chunk_seconds))
    overlap_frames = min(chunk_frames - 1, sample_rate * max(0, overlap_seconds))
    step_frames = max(1, chunk_frames - overlap_frames)
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_frames:
        end = min(total_frames, start + chunk_frames)
        ranges.append((start, end))
        if end >= total_frames:
            break
        start += step_frames
    return ranges


def _read_pcm_chunk(input_path: Path, start_frame: int, end_frame: int, np: Any) -> Any:
    with wave.open(str(input_path), "rb") as audio:
        audio.setpos(start_frame)
        raw = audio.readframes(max(0, end_frame - start_frame))
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _offset_segments(
    raw_segments: Any,
    *,
    offset: float,
    commit_after: float | None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if not isinstance(raw_segments, list):
        return segments
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        start = offset + max(0.0, float(item.get("start") or 0.0))
        end = offset + max(0.0, float(item.get("end") or 0.0))
        # The preceding chunk owns the overlap. Keep a boundary-spanning
        # segment so a sentence is not lost when the cut falls inside it.
        if commit_after is not None and end <= commit_after:
            continue
        avg_logprob = item.get("avg_logprob")
        confidence = math.exp(float(avg_logprob)) if avg_logprob is not None else 0.0
        segments.append({
            "start": start,
            "end": end,
            "text": item.get("text", ""),
            "confidence": max(0.0, min(1.0, confidence)),
        })
    return segments


def _append_without_text_overlap(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> None:
    if existing and incoming:
        previous = str(existing[-1].get("text") or "").rstrip()
        current = str(incoming[0].get("text") or "").lstrip()
        maximum = min(80, len(previous), len(current))
        for size in range(maximum, 1, -1):
            if previous[-size:] == current[:size]:
                trimmed = current[size:].lstrip()
                if trimmed:
                    incoming[0] = {**incoming[0], "text": trimmed}
                else:
                    incoming.pop(0)
                break
    existing.extend(incoming)


def _load_checkpoint(path: Path, signature: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("signature") != signature:
        return {}
    if not isinstance(payload.get("segments", []), list):
        return {}
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
