from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import time
from pathlib import Path

from framecopy.engine.ocr.base import (
    CancelCheck,
    OCRProviderError,
    OCRProviderInfo,
    OCRProviderStatus,
    OCRResult,
)
from framecopy.models import OCRLine


SOURCE_PATH = Path(__file__).with_name("native") / "vision_ocr.swift"


class AppleVisionOCRProvider:
    def __init__(self, tools_dir: Path, *, timeout_seconds: int = 45,
                 languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en-US")) -> None:
        self.tools_dir = tools_dir
        self.timeout_seconds = timeout_seconds
        self.languages = languages
        self.binary_path = tools_dir / "apple-vision-ocr"
        self._info = OCRProviderInfo(
            name="apple_vision",
            version=platform.mac_ver()[0] or "unknown",
            model="VNRecognizeTextRequest.accurate",
            languages=languages,
        )

    @property
    def info(self) -> OCRProviderInfo:
        return self._info

    def check_environment(self) -> OCRProviderStatus:
        if platform.system() != "Darwin":
            return OCRProviderStatus(False, "Apple Vision 只支持 macOS")
        swiftc = shutil.which("swiftc")
        if not swiftc:
            return OCRProviderStatus(False, "未找到 Swift 编译器")
        if not SOURCE_PATH.is_file():
            return OCRProviderStatus(False, "Apple Vision Helper 源文件缺失")
        return OCRProviderStatus(True, "Apple Vision Accurate 可用", {
            "macos": self._info.version,
            "swiftc": swiftc,
        })

    def recognize(self, image_path: Path, cancelled: CancelCheck | None = None) -> OCRResult:
        self._ensure_helper(cancelled)
        started = time.monotonic()
        process = _run_cancelable(
            [str(self.binary_path), str(image_path), ",".join(self.languages)],
            timeout=self.timeout_seconds,
            cancelled=cancelled,
        )
        if process.returncode != 0:
            detail = (process.stderr or "Apple Vision Helper 返回错误").strip()[-500:]
            raise OCRProviderError(detail)
        try:
            payload = json.loads(process.stdout)
            info = OCRProviderInfo(
                name=str(payload.get("provider", "apple_vision")),
                version=str(payload.get("version", self._info.version)),
                model=str(payload.get("model", self._info.model)),
                revision=str(payload.get("revision", "")),
                languages=tuple(payload.get("languages") or self.languages),
            )
            lines = [
                OCRLine(
                    text=str(item["text"]),
                    confidence=float(item["confidence"]),
                    bbox=[max(0.0, min(1.0, float(value))) for value in item["bbox"][:4]],
                )
                for item in payload.get("lines", [])
                if str(item.get("text", "")).strip() and len(item.get("bbox", [])) >= 4
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OCRProviderError("Apple Vision Helper 输出格式无效") from exc
        self._info = info
        return OCRResult(
            lines=lines,
            provider=info,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )

    def _ensure_helper(self, cancelled: CancelCheck | None) -> None:
        status = self.check_environment()
        if not status.available:
            raise OCRProviderError(status.message)
        if self.binary_path.is_file() and self.binary_path.stat().st_mtime >= SOURCE_PATH.stat().st_mtime:
            return
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        cache_dir = self.tools_dir / "module-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["SWIFT_MODULECACHE_PATH"] = str(cache_dir)
        environment["CLANG_MODULE_CACHE_PATH"] = str(cache_dir)
        process = _run_cancelable(
            [shutil.which("swiftc") or "/usr/bin/swiftc", "-O", str(SOURCE_PATH), "-o", str(self.binary_path)],
            timeout=max(60, self.timeout_seconds),
            cancelled=cancelled,
            env=environment,
        )
        if process.returncode != 0:
            detail = (process.stderr or "Swift 编译失败").strip()[-1000:]
            raise OCRProviderError(f"Apple Vision Helper 构建失败：{detail}")


def _run_cancelable(command: list[str], *, timeout: int, cancelled: CancelCheck | None,
                    env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=env,
    )
    started = time.monotonic()
    while process.poll() is None:
        if cancelled and cancelled():
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
            raise InterruptedError("cancelled")
        if time.monotonic() - started > timeout:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            raise OCRProviderError(f"OCR 子进程超过 {timeout} 秒")
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
