from __future__ import annotations

import sys

import pytest

from framecopy.engine.speech import _run_cancellable
from framecopy.errors import FramecopyError


def test_failed_speech_process_preserves_last_error_and_exit_code() -> None:
    command = [
        sys.executable,
        "-c",
        "import sys; print('first line', file=sys.stderr); print('actual failure', file=sys.stderr); sys.exit(7)",
    ]

    with pytest.raises(FramecopyError) as captured:
        _run_cancellable(
            command,
            lambda: False,
            timeout=10,
            failure_code="TRANSCRIPTION_FAILED",
        )

    assert captured.value.code == "TRANSCRIPTION_FAILED"
    assert "actual failure" in captured.value.public_message
    assert "退出码 7" in captured.value.public_message


def test_failed_speech_process_reports_signal_exit_without_stderr() -> None:
    command = [sys.executable, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"]

    with pytest.raises(FramecopyError) as captured:
        _run_cancellable(
            command,
            lambda: False,
            timeout=10,
            failure_code="TRANSCRIPTION_FAILED",
        )

    assert "没有返回错误详情" in captured.value.public_message
    assert "退出码" in captured.value.public_message
