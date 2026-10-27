from __future__ import annotations

import html
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from framecopy.config import Settings
from framecopy.errors import FramecopyError, error
from framecopy.models import SpeechSegment
from framecopy.sources.base import (
    AcquiredMedia,
    CancelCallback,
    ProgressCallback,
    ProviderStatus,
    VideoSourceProvider,
)


BILIBILI_URL_PATTERN = re.compile(
    r"https://(?:(?:www|m)\.)?(?:bilibili\.com/video/[A-Za-z0-9]+[^\s<>\"']*|b23\.tv/[A-Za-z0-9]+[^\s<>\"']*)",
    re.IGNORECASE,
)
AUDIO_SUFFIXES = {".m4a", ".m4s", ".mp3", ".aac", ".opus", ".ogg", ".webm", ".mp4"}
SUBTITLE_SUFFIXES = {".vtt", ".srt", ".json", ".json3"}


@dataclass(frozen=True)
class BilibiliPart:
    index: int
    title: str
    url: str


@dataclass(frozen=True)
class BilibiliInspection:
    url: str
    title: str
    duration: float | None
    parts: tuple[BilibiliPart, ...] = ()
    subtitle_languages: tuple[str, ...] = ()

    @property
    def requires_part_selection(self) -> bool:
        return len(self.parts) > 1

    @property
    def has_subtitles(self) -> bool:
        return bool(self.subtitle_languages)

    def select_subtitle_language(self, requested: str) -> str:
        return _select_subtitle_language(self.subtitle_languages, requested)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "duration": self.duration,
            "requires_part_selection": self.requires_part_selection,
            "has_subtitles": self.has_subtitles,
            "subtitle_languages": list(self.subtitle_languages),
            "parts": [
                {"index": item.index, "title": item.title, "url": item.url}
                for item in self.parts
            ],
        }


def extract_bilibili_url(text: str) -> str:
    match = BILIBILI_URL_PATTERN.search(text.strip())
    if not match:
        raise error("BILIBILI_INVALID_LINK")
    url = match.group(0).rstrip("。；;，,）)]}>\"'")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in {
        "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
    }:
        raise error("BILIBILI_INVALID_LINK")
    if host.endswith("bilibili.com") and not parsed.path.startswith("/video/"):
        raise error("BILIBILI_INVALID_LINK")
    return url


class BilibiliProvider(VideoSourceProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.executable = _resolve_executable(settings.bilibili_downloader)

    def check_environment(self) -> ProviderStatus:
        if not self.executable:
            return ProviderStatus(False, "B站解析器未安装，请重新运行安装程序")
        try:
            check = subprocess.run(
                [self.executable, "--version"], capture_output=True, text=True,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ProviderStatus(False, "B站解析器无法启动")
        version = check.stdout.strip().splitlines()[0] if check.returncode == 0 else ""
        return ProviderStatus(
            check.returncode == 0,
            f"B站公开视频解析可用（yt-dlp {version}）" if version else "B站解析器无法启动",
            version or None,
        )

    def inspect(self, text: str) -> BilibiliInspection:
        status = self.check_environment()
        if not status.available:
            raise error("BILIBILI_ENVIRONMENT_UNAVAILABLE")
        url = extract_bilibili_url(text)
        parsed = urlparse(url)
        has_part = bool(parse_qs(parsed.query).get("p"))
        if not has_part:
            flat = self._metadata(url, flat_playlist=True)
            entries = flat.get("entries") if isinstance(flat, dict) else None
            if isinstance(entries, list) and len(entries) > 1:
                parts = tuple(
                    _part_from_entry(url, item, index)
                    for index, item in enumerate(entries, 1)
                    if isinstance(item, dict)
                )
                return BilibiliInspection(
                    url=url,
                    title=str(flat.get("title") or "B站多P视频"),
                    duration=_optional_float(flat.get("duration")),
                    parts=parts,
                )
        payload = self._metadata(url, flat_playlist=False)
        final_url = str(payload.get("webpage_url") or payload.get("original_url") or url)
        _validate_final_url(final_url)
        subtitles = payload.get("subtitles") if isinstance(payload.get("subtitles"), dict) else {}
        automatic = (
            payload.get("automatic_captions")
            if isinstance(payload.get("automatic_captions"), dict)
            else {}
        )
        languages = _subtitle_languages(subtitles, automatic)
        return BilibiliInspection(
            url=final_url,
            title=str(payload.get("title") or "B站视频"),
            duration=_optional_float(payload.get("duration")),
            subtitle_languages=languages,
        )

    def acquire(
        self,
        source: str | Path | dict[str, str],
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        if not isinstance(source, dict):
            raise error("BILIBILI_INVALID_LINK")
        inspection = self.inspect(source.get("url", ""))
        if inspection.requires_part_selection:
            raise error("BILIBILI_PART_REQUIRED")
        if inspection.duration and inspection.duration > self.settings.max_duration_seconds:
            raise error("DURATION_EXCEEDED")
        transcript_source = source.get("transcript_source", "asr")
        language = source.get("language", "auto")
        if transcript_source == "subtitle":
            return self._acquire_subtitle(inspection, language, job_dir, progress, cancelled)
        return self._acquire_audio(inspection, language, job_dir, progress, cancelled)

    def _metadata(self, url: str, *, flat_playlist: bool) -> dict[str, Any]:
        command = [self.executable, "--dump-single-json", "--skip-download", "--no-warnings"]
        command.append("--flat-playlist" if flat_playlist else "--no-playlist")
        command.append(url)
        try:
            process = subprocess.run(
                command, capture_output=True, text=True, timeout=90, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise error("BILIBILI_DOWNLOAD_FAILED", detail="读取视频信息超时") from exc
        if process.returncode != 0:
            raise _bilibili_failure(process.stderr or process.stdout)
        for line in reversed(process.stdout.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise error("BILIBILI_DOWNLOAD_FAILED", detail="解析器未返回视频信息")

    def _acquire_audio(
        self,
        inspection: BilibiliInspection,
        language: str,
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        output_dir = (job_dir / "input").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        progress("正在获取B站音轨", 5)
        command = [
            self.executable,
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--format", "bestaudio",
            "--output", str(output_dir / "bilibili-audio.%(ext)s"),
            "--progress-template", "download:PROGRESS:%(progress._percent_str)s",
            "--print", "after_move:RESULT:%(filepath)s",
            inspection.url,
        ]
        lines = _run_streaming(command, cancelled, progress)
        result_paths = [Path(line[7:]).resolve() for line in lines if line.startswith("RESULT:")]
        candidates = [
            path for path in output_dir.glob("bilibili-audio.*")
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ]
        path = result_paths[-1] if result_paths else (candidates[-1].resolve() if candidates else None)
        if path is None or not path.is_file() or output_dir not in path.parents:
            raise error("BILIBILI_AUDIO_UNAVAILABLE")
        progress("B站音轨获取完成", 10)
        return AcquiredMedia(
            path=path,
            kind="audio",
            title=inspection.title,
            duration=inspection.duration,
            language_hint="" if language == "auto" else language,
        )

    def _acquire_subtitle(
        self,
        inspection: BilibiliInspection,
        language: str,
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        selected = inspection.select_subtitle_language(language)
        if not selected:
            raise error("BILIBILI_SUBTITLE_UNAVAILABLE")
        output_dir = (job_dir / "input").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        progress("正在获取B站发布字幕", 6)
        command = [
            self.executable,
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", selected,
            "--sub-format", "vtt/srt/best",
            "--output", str(output_dir / "bilibili-subtitle.%(ext)s"),
            inspection.url,
        ]
        _run_streaming(command, cancelled, progress)
        candidates = sorted(
            (path for path in output_dir.glob("bilibili-subtitle.*") if path.suffix.lower() in SUBTITLE_SUFFIXES),
            key=lambda item: item.stat().st_mtime,
        )
        if not candidates:
            raise error("BILIBILI_SUBTITLE_UNAVAILABLE")
        path = candidates[-1].resolve()
        segments = tuple(_parse_subtitle(path, selected))
        if not segments:
            raise error("BILIBILI_SUBTITLE_UNAVAILABLE")
        progress("B站发布字幕获取完成", 18)
        return AcquiredMedia(
            path=path,
            kind="subtitle",
            title=inspection.title,
            duration=inspection.duration,
            transcript_segments=segments,
            transcript_provider="bilibili_subtitle",
            transcript_provider_version=self.check_environment().mode or "unknown",
            transcript_model="published-subtitle",
            transcript_language=selected,
        )


def _resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    resolved = shutil.which(value)
    if resolved:
        return resolved
    homebrew = Path("/opt/homebrew/bin") / value
    return str(homebrew) if homebrew.is_file() else ""


def _part_from_entry(original_url: str, entry: dict[str, Any], index: int) -> BilibiliPart:
    url = str(entry.get("url") or "")
    if not url.startswith("https://"):
        bvid = _extract_bvid(original_url) or str(entry.get("id") or "")
        url = f"https://www.bilibili.com/video/{bvid}?p={index}"
    _validate_final_url(url)
    return BilibiliPart(index, str(entry.get("title") or f"P{index}"), url)


def _extract_bvid(value: str) -> str:
    match = re.search(r"/video/(BV[A-Za-z0-9]+)", value, re.IGNORECASE)
    return match.group(1) if match else ""


def _validate_final_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
        "bilibili.com", "www.bilibili.com", "m.bilibili.com",
    } or not re.fullmatch(r"/video/(?:BV[A-Za-z0-9]+|av\d+)/?", parsed.path, re.IGNORECASE):
        raise error("BILIBILI_INVALID_LINK")


def _subtitle_languages(*groups: dict[str, Any]) -> tuple[str, ...]:
    ignored = {"danmaku", "comments", "live_chat", "live-chat", "chat"}
    result: list[str] = []
    for group in groups:
        for language, formats in group.items():
            normalized = str(language).strip()
            if not normalized or normalized.lower() in ignored:
                continue
            if not isinstance(formats, list) or not formats:
                continue
            if normalized not in result:
                result.append(normalized)
    return tuple(result)


def _run_streaming(
    command: list[str],
    cancelled: CancelCallback,
    progress: ProgressCallback,
    timeout: int = 20 * 60,
) -> list[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        bufsize=1,
    )
    messages: queue.Queue[str] = queue.Queue()

    def read_output() -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            messages.put(line.rstrip())

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while process.poll() is None:
        if cancelled():
            _terminate(process)
            raise InterruptedError("cancelled")
        if time.monotonic() >= deadline:
            _terminate(process)
            raise error("BILIBILI_DOWNLOAD_FAILED", detail="获取超时")
        try:
            line = messages.get(timeout=0.2)
        except queue.Empty:
            continue
        lines.append(line)
        match = re.search(r"PROGRESS:\s*([0-9.]+)%", line)
        if match:
            percentage = max(0.0, min(100.0, float(match.group(1))))
            progress(f"正在获取B站音轨 {percentage:.0f}%", 5 + int(5 * percentage / 100))
    reader.join(timeout=1)
    while not messages.empty():
        lines.append(messages.get_nowait())
    if process.returncode != 0:
        raise _bilibili_failure("\n".join(lines[-12:]))
    return lines


def _terminate(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _bilibili_failure(detail: str) -> FramecopyError:
    lowered = detail.lower()
    if any(token in lowered for token in ("login", "premium", "supporter-only", "member", "会员", "充电")):
        return error("BILIBILI_LOGIN_REQUIRED")
    if any(token in lowered for token in ("private", "unavailable", "geo", "not available", "deleted")):
        return error("BILIBILI_CONTENT_UNAVAILABLE")
    if "requested format is not available" in lowered or "no video formats" in lowered:
        return error("BILIBILI_AUDIO_UNAVAILABLE")
    last_line = next((line.strip() for line in reversed(detail.splitlines()) if line.strip()), "未知错误")
    return error("BILIBILI_DOWNLOAD_FAILED", detail=last_line[-240:])


def _select_subtitle_language(available: tuple[str, ...], requested: str) -> str:
    if not available:
        return ""
    preferences = {
        "zh": ("zh-Hans", "zh-CN", "zh", "ai-zh", "zh-Hant", "zh-TW"),
        "en": ("en", "en-US", "en-GB", "ai-en"),
        "ja": ("ja", "ja-JP", "ai-ja"),
        "auto": ("zh-Hans", "zh-CN", "zh", "ai-zh", "en", "ja"),
    }.get(requested, ())
    lowered = {item.lower(): item for item in available}
    for candidate in preferences:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return available[0] if requested == "auto" else ""


def _parse_subtitle(path: Path, language: str) -> list[SpeechSegment]:
    if path.suffix.lower() in {".json", ".json3"}:
        return _parse_json_subtitle(path, language)
    return _parse_text_subtitle(path, language)


def _parse_json_subtitle(path: Path, language: str) -> list[SpeechSegment]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(rows, list) and isinstance(payload, dict):
        rows = payload.get("events")
    segments: list[SpeechSegment] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("content") or "")
        if not text and isinstance(row.get("segs"), list):
            text = "".join(str(item.get("utf8") or "") for item in row["segs"] if isinstance(item, dict))
        text = _clean_subtitle_text(text)
        if not text:
            continue
        start = _optional_float(row.get("from"))
        end = _optional_float(row.get("to"))
        if start is None:
            start = float(row.get("tStartMs") or 0) / 1000
        if end is None:
            end = start + float(row.get("dDurationMs") or 0) / 1000
        segments.append(SpeechSegment(
            len(segments) + 1, start, max(start, end), text, text, 1.0, language, False
        ))
    return segments


def _parse_text_subtitle(path: Path, language: str) -> list[SpeechSegment]:
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n"))
    segments: list[SpeechSegment] = []
    timestamp = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})"
    )
    for block in blocks:
        match = timestamp.search(block)
        if not match:
            continue
        lines = block[match.end():].strip().splitlines()
        text = _clean_subtitle_text(" ".join(lines))
        if not text:
            continue
        start = _timestamp_seconds(match.group("start"))
        end = _timestamp_seconds(match.group("end"))
        if segments and segments[-1].clean_text == text:
            segments[-1].end_time = max(segments[-1].end_time, end)
            continue
        segments.append(SpeechSegment(
            len(segments) + 1, start, end, text, text, 1.0, language, False
        ))
    return segments


def _clean_subtitle_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", html.unescape(value))
    return " ".join(value.split()).strip()


def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
