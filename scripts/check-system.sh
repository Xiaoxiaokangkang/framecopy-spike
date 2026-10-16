#!/bin/sh
set -eu

missing=""
for command in python3 ffmpeg ffprobe; do
  if ! command -v "$command" >/dev/null 2>&1; then
    missing="$missing $command"
  fi
done

if [ -n "$missing" ]; then
  echo "缺少系统依赖:$missing"
  exit 1
fi

echo "系统依赖检查通过"
python3 --version
ffmpeg -version | sed -n '1p'

if [ "$(uname -s)" = "Darwin" ]; then
  if command -v swiftc >/dev/null 2>&1; then
    echo "Apple Vision Helper 构建环境可用"
    swiftc --version | sed -n '1p'
  else
    echo "警告：未找到 swiftc，Apple Vision 不可用，将尝试 PaddleOCR 回退"
  fi
fi

