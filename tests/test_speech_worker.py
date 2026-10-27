from __future__ import annotations

import json
import math
import sys
import types
import wave

from framecopy.engine.speech_worker import (
    _append_without_text_overlap,
    _chunk_ranges,
    _load_checkpoint,
    main,
    _offset_segments,
    _write_json_atomic,
)


def test_long_audio_is_split_with_overlap() -> None:
    ranges = _chunk_ranges(
        total_frames=15 * 60 * 16_000,
        sample_rate=16_000,
        chunk_seconds=240,
        overlap_seconds=2,
    )

    assert len(ranges) == 4
    assert ranges[0] == (0, 240 * 16_000)
    assert ranges[1][0] == 238 * 16_000
    assert ranges[-1][1] == 15 * 60 * 16_000


def test_short_audio_stays_in_one_chunk() -> None:
    assert _chunk_ranges(30 * 16_000, 16_000, 240, 2) == [(0, 30 * 16_000)]


def test_overlap_owned_by_previous_chunk_but_boundary_sentence_is_kept() -> None:
    result = _offset_segments(
        [
            {"start": 0.0, "end": 1.5, "text": "重复内容", "avg_logprob": -0.1},
            {"start": 1.0, "end": 3.5, "text": "跨边界的句子", "avg_logprob": -0.2},
            {"start": 3.5, "end": 5.0, "text": "新内容", "avg_logprob": -0.3},
        ],
        offset=238.0,
        commit_after=240.0,
    )

    assert [item["text"] for item in result] == ["跨边界的句子", "新内容"]
    assert result[0]["start"] == 239.0
    assert math.isclose(result[0]["confidence"], math.exp(-0.2))


def test_exact_boundary_text_overlap_is_removed() -> None:
    existing = [{"text": "今天我们来讲人工智能"}]
    incoming = [
        {"text": "人工智能的发展"},
        {"text": "下一句"},
    ]

    _append_without_text_overlap(existing, incoming)

    assert [item["text"] for item in existing] == [
        "今天我们来讲人工智能",
        "的发展",
        "下一句",
    ]


def test_checkpoint_is_loaded_only_for_the_same_audio_and_settings(tmp_path) -> None:
    path = tmp_path / "checkpoint.json"
    signature = {"input_size": 12, "model": "local-model"}
    _write_json_atomic(path, {
        "signature": signature,
        "completed": 2,
        "segments": [{"text": "已完成"}],
        "language": "zh",
    })

    assert _load_checkpoint(path, signature)["completed"] == 2
    assert _load_checkpoint(path, {"input_size": 13, "model": "local-model"}) == {}
    assert json.loads(path.read_text(encoding="utf-8"))["language"] == "zh"


def test_worker_writes_large_result_to_file_and_reports_chunk_progress(
    monkeypatch, tmp_path
) -> None:
    audio_path = tmp_path / "speech.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\0\0" * (65 * 16_000))

    calls: list[dict] = []

    def fake_transcribe(samples, **options):
        calls.append(options)
        return {
            "language": "zh",
            "segments": [{
                "start": 0,
                "end": len(samples) / 16_000,
                "text": f"第{len(calls)}块",
                "avg_logprob": -0.1,
            }],
        }

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake_transcribe))
    monkeypatch.setattr("framecopy.engine.speech_worker.importlib.metadata.version", lambda _: "test")
    progress_path = tmp_path / "progress.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", [
        "speech-worker",
        "--input", str(audio_path),
        "--model", "local-model",
        "--progress-file", str(progress_path),
        "--checkpoint-file", str(checkpoint_path),
        "--result-file", str(result_path),
        "--chunk-seconds", "30",
        "--overlap-seconds", "2",
    ])

    assert main() == 0

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["chunks_total"] == 3
    assert len(calls) == 3
    assert calls[0]["condition_on_previous_text"] is False
    assert "language" not in calls[0]
    assert "language" not in calls[1]
    assert result["language"] == "zh"
    assert json.loads(progress_path.read_text(encoding="utf-8")) == {
        "state": "completed",
        "current": 3,
        "total": 3,
    }
