from __future__ import annotations

from pathlib import Path

from framecopy.sources.base import (
    AcquiredMedia,
    CancelCallback,
    ProgressCallback,
    ProviderStatus,
    VideoSourceProvider,
)


class UploadedFileProvider(VideoSourceProvider):
    def check_environment(self) -> ProviderStatus:
        return ProviderStatus(True, "本地上传可用")

    def acquire(
        self,
        source: str | Path,
        job_dir: Path,
        progress: ProgressCallback,
        cancelled: CancelCallback,
    ) -> AcquiredMedia:
        path = Path(source).resolve()
        expected = (job_dir / "input").resolve()
        if expected not in path.parents or not path.is_file():
            raise ValueError("uploaded file is outside the job input directory")
        progress("视频已接收", 10)
        return AcquiredMedia(path=path, kind="video")

