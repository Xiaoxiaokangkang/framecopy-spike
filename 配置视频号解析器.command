#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$SCRIPT_DIR/.tools/wx_video_download"
TARGET="$TARGET_DIR/wx_video_download"

echo "请在弹出的窗口中选择已经下载并解压的 wx_video_download 可执行文件。"
selected="$(osascript -e 'POSIX path of (choose file with prompt "选择 wx_video_download 可执行文件")')" || {
  echo "已取消。"
  read "?按回车键关闭此窗口…" || true
  exit 0
}

mkdir -p "$TARGET_DIR"
cp "$selected" "$TARGET"
chmod 700 "$TARGET"

if /usr/bin/file "$TARGET" | grep -Eq 'arm64|universal binary'; then
  echo "视频号解析器已经配置完成。"
  echo "启动帧文案后，请在首页保存本机的视频号解析凭证。"
else
  echo "文件已放入指定位置，但架构检查未通过。请确认下载的是 macOS Apple 芯片版本。"
fi
read "?按回车键关闭此窗口…" || true
