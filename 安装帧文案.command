#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

finish() {
  echo
  read "?按回车键关闭此窗口…" || true
}

failed() {
  status=$?
  echo
  echo "安装没有完成，请把上方最后几行错误信息保存下来。"
  finish
  exit "$status"
}

trap failed ERR

echo "========================================"
echo "       帧文案 · Apple 芯片 Mac 安装"
echo "========================================"
echo

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "此安装器只支持 Apple 芯片 Mac（M1/M2/M3/M4）。"
  exit 1
fi

if ! xcode-select -p >/dev/null 2>&1; then
  echo "需要先安装 Apple 命令行工具。"
  echo "系统即将弹出安装窗口；安装完成后，请重新双击本文件。"
  xcode-select --install || true
  finish
  exit 0
fi

if [[ -x /opt/homebrew/bin/brew ]]; then
  export PATH="/opt/homebrew/bin:$PATH"
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "尚未安装 Homebrew，它用于安装 FFmpeg、uv 和 B站解析器。"
  echo "首次下载 Homebrew 通常需要等待 3–10 分钟，网络较慢时可能更久。"
  echo "终端暂时没有变化通常不是卡死，请不要关闭窗口或重复运行安装器。"
  read "answer?是否现在运行 Homebrew 官方安装程序？[Y/n] "
  if [[ "${answer:-Y}" == [nN] ]]; then
    echo "已取消。安装 Homebrew 后请重新运行本安装器。"
    finish
    exit 0
  fi
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  export PATH="/opt/homebrew/bin:$PATH"
fi

echo
echo "[1/6] 安装系统组件 FFmpeg、uv 和 B站解析器…"
brew install ffmpeg uv yt-dlp

echo
echo "[2/6] 安装并固定 Python 3.12…"
uv python install 3.12

echo
echo "[3/6] 创建帧文案运行环境…"
uv sync --frozen --python 3.12 --extra speech

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已创建本机配置文件 .env"
else
  echo "保留已有的本机配置文件 .env"
fi

echo
echo "[4/6] 下载并校验本地语音模型…"
.venv/bin/python scripts/prepare_speech_model.py --verify-load

echo
echo "[5/6] 准备 Apple Vision 画面识别组件…"
.venv/bin/python scripts/prepare_apple_vision.py

echo
echo "[6/6] 执行最终环境检查…"
./scripts/check-system.sh
.venv/bin/python -c "import mlx_whisper; print('MLX Whisper 导入检查通过')"

echo
if [[ -x .tools/wx_video_download/wx_video_download ]]; then
  echo "视频号解析器已就绪。"
else
  echo "提示：视频号解析器尚未配置。"
  echo "本地上传已经可以使用；如需粘贴视频号链接，请运行“配置视频号解析器.command”。"
fi

if command -v yt-dlp >/dev/null 2>&1; then
  echo "B站公开视频解析器已就绪。"
else
  echo "提示：B站解析器未安装，可重新运行本安装程序。"
fi

echo
echo "安装完成。以后只需双击“启动帧文案.command”。"
finish
