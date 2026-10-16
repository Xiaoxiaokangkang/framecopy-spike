from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from framecopy.config import Settings


def load_provider_module():
    path = Path("scripts/wx_channel_provider.py").resolve()
    spec = importlib.util.spec_from_file_location("wx_channel_provider_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_download_uses_standalone_mcp_and_removes_cookie_config(tmp_path: Path) -> None:
    module = load_provider_module()
    module.settings = Settings(data_dir=tmp_path, database_path=tmp_path / "framecopy.db")
    fake = tmp_path / "fake-downloader"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "config = pathlib.Path(sys.argv[sys.argv.index('-c') + 1]).read_text()\n"
        "assert 'very-secret-cookie' in config\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('id') != 2: continue\n"
        "    arguments = request['params']['arguments']\n"
        "    target = pathlib.Path(arguments['download_dir']) / 'download.mp4'\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_bytes(b'video')\n"
        "    payload = {'completed': True}\n"
        "    result = {'content': [{'type': 'text', 'text': json.dumps(payload)}]}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': result}), flush=True)\n"
        "    break\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    args = argparse.Namespace(
        binary=str(fake),
        config=str(tmp_path / "unused.yaml"),
        api_base_url="http://127.0.0.1:9",
        url="https://weixin.qq.com/sph/example",
        timeout=10,
    )

    module.call_direct_download(args, "very-secret-cookie=1", output_dir)

    assert (output_dir / "download.mp4").is_file()
    assert not list((tmp_path / "secrets").glob("wx-direct-*.yaml"))
