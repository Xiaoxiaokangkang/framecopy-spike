#!/usr/bin/env python3
"""Framecopy adapter for automatic and desktop-assisted Channels downloads."""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from framecopy.config import settings
from framecopy.secret_store import LocalSecretStore, WX_YUANBAO_COOKIE


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts"}


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--binary",
        default=str(project_root / ".tools" / "wx_video_download" / "wx_video_download"),
    )
    parser.add_argument(
        "--config",
        default=str(project_root / "config" / "wx-provider-proxy.yaml"),
    )
    parser.add_argument("--api-base-url", default="http://127.0.0.1:2022")
    parser.add_argument("--timeout", type=int, default=900)
    return parser.parse_args()


def api_json(base_url: str, path: str) -> object:
    request = urllib.request.Request(base_url.rstrip("/") + path)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("msg") or "下载器 API 返回错误")
    return payload.get("data")


def page_available(base_url: str) -> bool:
    try:
        status = api_json(base_url, "/api/channels/status")
    except (OSError, RuntimeError, urllib.error.URLError, ValueError):
        return False
    return isinstance(status, dict) and bool(status.get("available"))


def _mcp_messages(tool_name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "framecopy", "version": "1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    ]


def call_mcp_tool(
    args: argparse.Namespace,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    standalone_config: Path | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    command = [args.binary, "mcp"]
    if standalone_config is not None:
        command.append("--standalone")
        config = standalone_config
    else:
        config = Path(args.config)
        command.extend(["--api-base-url", args.api_base_url])
    command.extend(["-c", str(config)])
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    for item in _mcp_messages(tool_name, arguments):
        process.stdin.write(json.dumps(item, ensure_ascii=False) + "\n")
    process.stdin.flush()

    response: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if candidate.get("id") == 2:
                response = candidate
                break
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if not response:
        stderr = process.stderr.read().strip() if process.stderr else ""
        raise RuntimeError(stderr or "MCP 下载命令没有返回结果")
    if "error" in response:
        raise RuntimeError(response["error"].get("message", "MCP 下载失败"))
    result = response.get("result", {})
    if result.get("isError"):
        structured = result.get("structuredContent") or {}
        message = structured.get("error")
        if not message:
            message = next(
                (item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"),
                "",
            )
        raise RuntimeError(message or "MCP 下载失败")
    text = next(
        (item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"),
        "",
    )
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("无法解析 MCP 下载结果") from exc
    return payload if isinstance(payload, dict) else {"data": payload}


def _yaml_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def create_direct_config(cookie: str, output_dir: Path) -> Path:
    runtime_dir = (settings.data_dir / "wx-direct-provider").resolve()
    secrets_dir = (settings.data_dir / "secrets").resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    config_text = f"""workdir: {_yaml_string(runtime_dir)}

download:
  dir: {_yaml_string(output_dir)}
  playDoneAudio: false
  maxRunning: 1
  resourceConcurrency: 8
  segmentConcurrency: 4
  connectionConcurrency: 16

db:
  type: "sqlite"
  filepath: {_yaml_string(runtime_dir / 'data.db')}

proxy:
  enabled: false
  system: false

cloudflare:
  sphCookie: {_yaml_string(cookie)}
"""
    descriptor, filename = tempfile.mkstemp(prefix="wx-direct-", suffix=".yaml", dir=secrets_dir)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(config_text)
    return Path(filename)


def call_direct_download(args: argparse.Namespace, cookie: str, output_dir: Path) -> None:
    temporary_config = create_direct_config(cookie, output_dir)
    try:
        call_mcp_tool(
            args,
            "download_content",
            {
                "url": args.url,
                "download_dir": str(output_dir),
                "existing_action": "duplicate",
                "wait_for_completion": True,
                "timeout_seconds": args.timeout,
            },
            standalone_config=temporary_config,
            timeout=args.timeout + 30,
        )
    except Exception as exc:
        message = str(exc).replace(cookie, "[凭证已隐藏]")
        lowered = message.lower()
        if any(token in lowered for token in ("cookie", "unauthorized", "forbidden", "wx_export_id")):
            raise RuntimeError("自动解析凭证无效或已过期，请重新配置元宝 Cookie") from exc
        raise RuntimeError(f"自动解析失败：{message[-240:]}") from exc
    finally:
        temporary_config.unlink(missing_ok=True)


def call_desktop_download(args: argparse.Namespace, output_dir: Path) -> int:
    payload = call_mcp_tool(
        args,
        "download_wxchannels_video",
        {
            "url": args.url,
            "download_dir": str(output_dir),
            "existing_action": "duplicate",
        },
        timeout=60,
    )
    ids = payload.get("ids") or payload.get("data", {}).get("ids") or []
    if not ids:
        task = payload.get("task") or payload.get("data", {}).get("task") or {}
        if task.get("id"):
            ids = [task["id"]]
    if not ids:
        raise RuntimeError("MCP 下载结果缺少任务编号")
    return int(ids[0])


def wait_for_download(args: argparse.Namespace, task_id: int) -> None:
    deadline = time.monotonic() + args.timeout
    query = urllib.parse.urlencode({"task_id": task_id})
    while time.monotonic() < deadline:
        data = api_json(args.api_base_url, f"/api/v1/download_task/list?{query}")
        if isinstance(data, dict) and data.get("id"):
            task = data
        else:
            tasks = data.get("list", []) if isinstance(data, dict) else []
            task = next((item for item in tasks if int(item.get("id", 0)) == task_id), None)
        if not task:
            raise RuntimeError(f"下载任务不存在：{task_id}")
        status = int(task.get("status", -1))
        if status == 5:
            return
        if status in {6, 7}:
            message = task.get("error_message") or task.get("error") or f"状态 {status}"
            raise RuntimeError(f"视频下载失败：{message}")
        time.sleep(1)
    raise RuntimeError("等待视频下载完成超时")


def video_files(output_dir: Path) -> list[Path]:
    return [
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    ]


def main() -> int:
    args = parse_args()
    binary = Path(args.binary).expanduser()
    if not binary.is_file():
        raise RuntimeError("视频号下载器程序不存在")
    cookie = LocalSecretStore(settings).load(WX_YUANBAO_COOKIE)

    if args.check:
        if cookie:
            print(json.dumps({
                "available": True,
                "mode": "direct",
                "message": "已配置视频号自动解析，无需打开电脑版微信",
            }, ensure_ascii=False))
            return 0
        if page_available(args.api_base_url):
            print(json.dumps({
                "available": True,
                "mode": "desktop",
                "message": "当前使用电脑版微信视频号通道",
            }, ensure_ascii=False))
            return 0
        raise RuntimeError("尚未配置自动解析凭证，且电脑版微信视频号页面未连接")

    if not args.url or not args.output_dir:
        raise RuntimeError("必须提供 --url 和 --output-dir")
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    direct_error: Exception | None = None
    mode = "desktop"
    if cookie:
        try:
            call_direct_download(args, cookie, output_dir)
            mode = "direct"
        except Exception as exc:
            direct_error = exc

    if mode != "direct":
        if page_available(args.api_base_url):
            task_id = call_desktop_download(args, output_dir)
            wait_for_download(args, task_id)
        elif direct_error:
            raise direct_error
        else:
            raise RuntimeError("尚未配置自动解析凭证，请先在帧文案首页配置元宝 Cookie")

    videos = video_files(output_dir)
    if len(videos) != 1:
        raise RuntimeError(f"下载完成，但检测到 {len(videos)} 个视频文件")
    print(json.dumps({"mode": mode, "path": str(videos[0])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
