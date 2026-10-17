from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from framecopy.config import Settings
from framecopy.errors import error
from framecopy.models import SpeechSegment


CancelCallback = Callable[[], bool]
ProgressCallback = Callable[[str, int], None]


@dataclass(frozen=True)
class SpeechProviderStatus:
    available: bool
    message: str
    provider: str
    model: str


@dataclass(frozen=True)
class SpeechResult:
    segments: list[SpeechSegment]
    provider: str
    provider_version: str
    model: str
    language: str
    duration_ms: float


class SpeechProviderError(RuntimeError):
    pass


class MlxWhisperProvider:
    name = "mlx_whisper"

    def __init__(self, settings: Settings):
        self.settings = settings

    def check_environment(self) -> SpeechProviderStatus:
        public_model = _public_model_name(self.settings.speech_model)
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            return SpeechProviderStatus(False, "MLX Whisper 只支持 Apple Silicon macOS", self.name, public_model)
        if shutil.which("ffmpeg") is None:
            return SpeechProviderStatus(False, "FFmpeg 尚未安装", self.name, public_model)
        if not self.settings.speech_model:
            return SpeechProviderStatus(False, "尚未配置本地语音模型", self.name, "")
        python = _speech_python(self.settings)
        if not Path(python).is_file():
            return SpeechProviderStatus(False, "MLX Whisper 独立运行环境不存在", self.name, public_model)
        try:
            check = subprocess.run(
                [python, "-c", "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('mlx_whisper') else 1)"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return SpeechProviderStatus(False, "MLX Whisper 运行环境无法启动", self.name, public_model)
        if check.returncode != 0:
            return SpeechProviderStatus(False, "MLX Whisper 尚未安装", self.name, public_model)
        return SpeechProviderStatus(True, "MLX Whisper 可用", self.name, public_model)

    def transcribe(
        self,
        video_path: Path,
        work_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> SpeechResult:
        status = self.check_environment()
        if not status.available:
            raise error("SPEECH_ENVIRONMENT_UNAVAILABLE", detail=status.message)
        work_dir.mkdir(parents=True, exist_ok=True)
        audio_path = work_dir / "speech-input.wav"
        progress("正在提取音轨", 28)
        _run_cancellable(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(video_path),
                "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(audio_path),
            ],
            cancelled,
            timeout=min(300, self.settings.speech_timeout_seconds),
            failure_code="AUDIO_EXTRACTION_FAILED",
        )
        if not audio_path.is_file() or audio_path.stat().st_size <= 44:
            raise error("AUDIO_TRACK_MISSING")
        progress("正在识别语音", 42)
        started = time.monotonic()
        process = _run_cancellable(
            [
                _speech_python(self.settings), "-m", "framecopy.engine.speech_worker",
                "--input", str(audio_path), "--model", self.settings.speech_model,
            ],
            cancelled,
            timeout=self.settings.speech_timeout_seconds,
            failure_code="TRANSCRIPTION_FAILED",
        )
        try:
            payload = json.loads(process.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SpeechProviderError("语音识别器返回了无效结果") from exc
        raw_segments = payload.get("segments") if isinstance(payload, dict) else None
        if not isinstance(raw_segments, list):
            raise SpeechProviderError("语音识别结果缺少分段")
        language = str(payload.get("language") or "")
        segments: list[SpeechSegment] = []
        for index, item in enumerate(raw_segments, 1):
            if not isinstance(item, dict):
                continue
            raw_text = str(item.get("text") or "").strip()
            if not raw_text:
                continue
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            segments.append(SpeechSegment(
                sequence=index,
                start_time=max(0.0, float(item.get("start") or 0.0)),
                end_time=max(0.0, float(item.get("end") or 0.0)),
                raw_text=raw_text,
                clean_text=_clean_transcript_text(raw_text),
                confidence=confidence,
                language=language,
                needs_review=confidence < 0.45,
            ))
        if not segments:
            raise error("NO_SPEECH_DETECTED")
        return SpeechResult(
            segments=segments,
            provider=self.name,
            provider_version=str(payload.get("provider_version") or "unknown"),
            model=_public_model_name(self.settings.speech_model),
            language=language,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )


def create_speech_provider(settings: Settings) -> MlxWhisperProvider:
    if settings.speech_provider != "mlx_whisper":
        raise ValueError(f"unsupported speech provider: {settings.speech_provider}")
    return MlxWhisperProvider(settings)


def _clean_transcript_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _public_model_name(value: str) -> str:
    path = Path(value).expanduser()
    return path.name if value.startswith("~") or path.is_absolute() else value


def _speech_python(settings: Settings) -> str:
    if settings.speech_python:
        return str(Path(settings.speech_python).expanduser().resolve())
    project_runtime = Path(__file__).resolve().parents[2] / ".venv-speech" / "bin" / "python"
    return str(project_runtime) if project_runtime.is_file() else sys.executable


def _run_cancellable(
    command: list[str],
    cancelled: CancelCallback,
    *,
    timeout: int,
    failure_code: str,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if cancelled():
            _terminate_process_group(process)
            raise InterruptedError("cancelled")
        if time.monotonic() >= deadline:
            _terminate_process_group(process)
            raise error("PROCESSING_TIMEOUT")
        time.sleep(0.2)
    stdout, stderr = process.communicate()
    completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if process.returncode != 0:
        detail = (stderr or "本地子进程退出").strip().splitlines()[-1][-240:]
        lowered = detail.lower()
        if failure_code == "AUDIO_EXTRACTION_FAILED" and (
            "matches no streams" in lowered or "stream map" in lowered
        ):
            raise error("AUDIO_TRACK_MISSING")
        raise error(failure_code)
    return completed


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
