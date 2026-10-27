from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from framecopy.config import Settings
from framecopy.engine.probe import probe_media
from framecopy.errors import FramecopyError


def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")


def test_speech_mode_accepts_audio_only_media(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"audio")
    monkeypatch.setattr("framecopy.engine.probe.shutil.which", lambda _: "/opt/homebrew/bin/ffprobe")
    monkeypatch.setattr(
        "framecopy.engine.probe.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({
                "streams": [{"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "120", "size": str(media.stat().st_size)},
            }), "",
        ),
    )

    info = probe_media(
        media, settings(tmp_path), require_video=False, require_audio=True
    )

    assert info.codec == "aac"
    assert info.width == 0
    assert info.duration == 120


def test_visual_mode_rejects_audio_only_media(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "audio.m4a"
    media.write_bytes(b"audio")
    monkeypatch.setattr("framecopy.engine.probe.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "framecopy.engine.probe.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({
                "streams": [{"codec_type": "audio", "codec_name": "aac"}],
                "format": {"duration": "10", "size": "5"},
            }), "",
        ),
    )

    with pytest.raises(FramecopyError) as captured:
        probe_media(media, settings(tmp_path), require_video=True, require_audio=False)

    assert captured.value.code == "UNSUPPORTED_MEDIA"
