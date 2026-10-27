import sys
from pathlib import Path

import pytest

from framecopy.errors import FramecopyError
from framecopy.sources.wx_channel import WxChannelCloudProvider, extract_wx_channel_url


def test_extracts_link_from_share_text() -> None:
    text = "复制这段分享文字 https://weixin.qq.com/sph/abc123?foo=bar 一起看看"
    assert extract_wx_channel_url(text) == "https://weixin.qq.com/sph/abc123?foo=bar"


@pytest.mark.parametrize("value", [
    "https://example.com/video.mp4",
    "http://weixin.qq.com/sph/abc",
    "file:///etc/passwd",
    "没有链接",
])
def test_rejects_unsupported_urls(value: str) -> None:
    with pytest.raises(FramecopyError) as exc:
        extract_wx_channel_url(value)
    assert exc.value.code == "LINK_INVALID"


def test_provider_ignores_encrypted_link_payload(tmp_path: Path) -> None:
    helper = tmp_path / "provider.py"
    helper.write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('--check', action='store_true'); p.add_argument('--url'); p.add_argument('--output-dir')\n"
        "a=p.parse_args()\n"
        "if a.check: raise SystemExit(0)\n"
        "Path(a.output_dir).mkdir(parents=True, exist_ok=True)\n"
        "Path(a.output_dir, 'download.mp4').write_bytes(b'video')\n",
        encoding="utf-8",
    )
    job_dir = tmp_path / "job"
    input_dir = job_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "source.enc").write_bytes(b"encrypted-link")
    provider = WxChannelCloudProvider(f"{sys.executable} {helper}")
    result = provider.acquire(
        "https://weixin.qq.com/sph/test123",
        job_dir,
        lambda *_: None,
        lambda: False,
    )
    assert result.path == (input_dir / "download.mp4").resolve()
    assert result.kind == "video"

