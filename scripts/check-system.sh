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

if command -v yt-dlp >/dev/null 2>&1; then
  echo "B站公开视频解析器可用：$(yt-dlp --version)"
else
  echo "提示：未安装 yt-dlp，B站链接功能不可用"
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
  speech_python="python3"
  if [ -x ".venv-speech/bin/python" ]; then
    speech_python=".venv-speech/bin/python"
  elif [ -x ".venv/bin/python" ]; then
    speech_python=".venv/bin/python"
  fi
  if "$speech_python" -c 'import mlx_whisper' >/dev/null 2>&1; then
    echo "MLX Whisper 语音识别环境可用"
  else
    echo "提示：未安装 MLX Whisper，画面文字仍可正常使用；语音文案需安装 speech 可选依赖"
  fi
fi

