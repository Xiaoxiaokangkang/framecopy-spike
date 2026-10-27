from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from framecopy.errors import error
from framecopy.sources.base import (
    AcquiredMedia,
    CancelCallback,
    ProgressCallback,
    ProviderStatus,
    VideoSourceProvider,
)


WX_URL_PATTERN = re.compile(r"https://weixin\.qq\.com/sph/[A-Za-z0-9_?=&%./+-]+")
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


def _video_candidates(output_dir: Path) -> list[Path]:
    return [
        path.resolve()
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]


def extract_wx_channel_url(text: str) -> str:
    match = WX_URL_PATTERN.search(text.strip())
    if not match:
        raise error("LINK_INVALID")
    url = match.group(0).rstrip("。；;，,）)]}>\"'")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "weixin.qq.com" or not parsed.path.startswith("/sph/"):
        raise error("LINK_INVALID")
    return url


class WxChannelCloudProvider(VideoSourceProvider):
    """外部解析器的隔离适配层。

    command 是运维者明确配置的可执行程序，接收 `--url` 和 `--output-dir`。
    首版默认关闭，避免在未验证许可证、会话和失败语义前偷偷启用下载器。
    """

    def __init__(self, command: str):
        self.command = command.strip()

    def check_environment(self) -> ProviderStatus:
        if not self.command:
            return ProviderStatus(False, "视频号解析尚未配置，请使用本地上传")
        executable = shlex.split(self.command)[0]
        if not (Path(executable).is_file() or shutil.which(executable)):
            return ProviderStatus(False, "视频号解析程序不存在")
        try:
            check = subprocess.run(
                [*shlex.split(self.command), "--check"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ProviderStatus(False, "无法连接视频号解析服务")
        if check.returncode != 0:
            message = check.stderr.strip().splitlines()[-1] if check.stderr.strip() else "视频号解析服务未连接"
            return ProviderStatus(False, message[-160:])
        try:
            payload = json.loads(check.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return ProviderStatus(True, "视频号链接服务已连接")
        return ProviderStatus(
            bool(payload.get("available", True)),
            str(payload.get("message") or "视频号链接服务已连接")[:240],
            str(payload.get("mode") or "") or None,
        )

    def acquire(
        self,
        source: str | Path,
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        status = self.check_environment()
        if not status.available:
            raise error("LINK_ENVIRONMENT_UNAVAILABLE")
        url = extract_wx_channel_url(str(source))
        output_dir = (job_dir / "input").resolve()
        existing = _video_candidates(output_dir)
        if len(existing) == 1:
            progress("视频获取完成", 10)
            return AcquiredMedia(path=existing[0], kind="video")
        progress("正在获取视频", 6)
        args = [*shlex.split(self.command), "--url", url, "--output-dir", str(output_dir)]
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=960)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise error("LINK_DOWNLOAD_FAILED", detail="解析超时")
        if cancelled():
            raise InterruptedError("cancelled")
        if process.returncode != 0:
            raise error("LINK_DOWNLOAD_FAILED", detail=(stderr.strip()[-240:] or "未知错误"))
        candidates = _video_candidates(output_dir)
        candidates = [path for path in candidates if output_dir in path.parents]
        if len(candidates) != 1:
            raise error("LINK_DOWNLOAD_FAILED", detail="解析器未返回唯一视频文件")
        progress("视频获取完成", 10)
        return AcquiredMedia(path=candidates[0], kind="video")

