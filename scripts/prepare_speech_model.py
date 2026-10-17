#!/usr/bin/env python3
"""Download and validate the local MLX Whisper model before the first job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def validate_snapshot(model_dir: Path) -> Path:
    config_path = model_dir / "config.json"
    if not config_path.is_file():
        raise RuntimeError("模型缺少 config.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("模型配置文件无法读取") from exc
    if not isinstance(config, dict) or not config:
        raise RuntimeError("模型配置文件内容无效")

    weights = next(
        (path for path in (model_dir / "weights.safetensors", model_dir / "weights.npz") if path.is_file()),
        None,
    )
    if weights is None or weights.stat().st_size == 0:
        raise RuntimeError("模型权重文件不存在或为空")
    return weights


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并校验帧文案使用的 MLX Whisper 模型")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--verify-load",
        action="store_true",
        help="下载后使用 MLX 实际加载一次模型，确认 Metal 和权重均可用",
    )
    args = parser.parse_args()

    model_path = Path(args.model).expanduser()
    if not model_path.is_dir():
        from huggingface_hub import snapshot_download

        print(f"准备下载语音模型：{args.model}", flush=True)
        print("首次下载文件较大，请保持网络连接，不要关闭此窗口。", flush=True)
        model_path = Path(snapshot_download(repo_id=args.model))

    weights = validate_snapshot(model_path)
    print(f"模型文件检查通过：{weights.name}（{weights.stat().st_size / 1024 / 1024:.0f} MB）", flush=True)

    if args.verify_load:
        print("正在使用 MLX 实际加载模型进行最终校验…", flush=True)
        from mlx_whisper.load_models import load_model

        load_model(str(model_path))
        print("模型加载校验通过。", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
