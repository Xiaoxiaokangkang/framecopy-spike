#!/usr/bin/env python3
"""Build the Apple Vision helper during installation instead of the first job."""

from __future__ import annotations

from framecopy.config import settings
from framecopy.engine.ocr.apple_vision import AppleVisionOCRProvider


def main() -> int:
    provider = AppleVisionOCRProvider(
        settings.data_dir / "tools",
        timeout_seconds=max(120, settings.ocr_timeout_seconds),
        languages=settings.apple_vision_languages,
    )
    status = provider.check_environment()
    if not status.available:
        raise RuntimeError(status.message)
    provider._ensure_helper(None)
    print(f"Apple Vision 画面识别组件已就绪：{provider.binary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
