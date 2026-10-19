from __future__ import annotations

import sys
from pathlib import Path

from framecopy.config import Settings
from framecopy.engine.speech import MlxWhisperProvider, _same_python_runtime


def make_settings(tmp_path: Path, *, speech_python: str) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "framecopy.db",
        speech_python=speech_python,
    )


def test_same_runtime_uses_current_environment_without_subprocess(monkeypatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, speech_python=sys.executable)
    monkeypatch.setattr("framecopy.engine.speech.sys.platform", "darwin")
    monkeypatch.setattr("framecopy.engine.speech.platform.machine", lambda: "arm64")
    monkeypatch.setattr("framecopy.engine.speech.shutil.which", lambda _: "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setattr("framecopy.engine.speech.importlib.util.find_spec", lambda _: object())

    def unexpected_subprocess(*_args, **_kwargs):
        raise AssertionError("同一 Python 环境不应再启动子进程检查")

    monkeypatch.setattr("framecopy.engine.speech.subprocess.run", unexpected_subprocess)

    status = MlxWhisperProvider(settings).check_environment()

    assert status.available is True
    assert status.message == "MLX Whisper 可用"


def test_same_python_runtime_resolves_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "python"
    target.write_text("runtime", encoding="utf-8")
    alias = tmp_path / "python-alias"
    alias.symlink_to(target)

    assert _same_python_runtime(str(alias), str(target)) is True
