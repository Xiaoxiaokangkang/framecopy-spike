#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_URL="http://127.0.0.1:8000/"
HEALTH_URL="http://127.0.0.1:8000/api/health"
PID_FILE="$SCRIPT_DIR/data/framecopy.pid"
LOG_DIR="$SCRIPT_DIR/data/logs"
LOG_FILE="$LOG_DIR/framecopy.log"

# 一键安装版统一使用主环境中的 MLX Whisper，避免旧的独立环境抢占。
export FRAMECOPY_SPEECH_PYTHON="$SCRIPT_DIR/.venv/bin/python"

if [[ ! -x .venv/bin/framecopy ]]; then
  echo "帧文案尚未安装，请先双击“安装帧文案.command”。"
  read "?按回车键关闭此窗口…" || true
  exit 1
fi

if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
  open "$APP_URL"
  exit 0
fi

mkdir -p "$LOG_DIR"
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && ! kill -0 "$old_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
  fi
fi

echo "正在启动帧文案…"
nohup "$SCRIPT_DIR/.venv/bin/framecopy" --host 127.0.0.1 --port 8000 >>"$LOG_FILE" 2>&1 &
app_pid=$!
echo "$app_pid" > "$PID_FILE"

for attempt in {1..30}; do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "启动成功，正在打开浏览器。"
    open "$APP_URL"
    exit 0
  fi
  if ! kill -0 "$app_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "启动失败，请查看日志：$LOG_FILE"
tail -n 20 "$LOG_FILE" 2>/dev/null || true
read "?按回车键关闭此窗口…" || true
exit 1
