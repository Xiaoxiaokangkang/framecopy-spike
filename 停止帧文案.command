#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/data/framecopy.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "没有发现由启动器运行的帧文案服务。"
  read "?按回车键关闭此窗口…" || true
  exit 0
fi

app_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -n "$app_pid" ]] && kill -0 "$app_pid" 2>/dev/null; then
  process_command="$(ps -p "$app_pid" -o command= 2>/dev/null || true)"
  if [[ "$process_command" == *"$SCRIPT_DIR/.venv/bin/framecopy"* ]]; then
    kill "$app_pid"
    for attempt in {1..10}; do
      kill -0 "$app_pid" 2>/dev/null || break
      sleep 1
    done
    echo "帧文案已停止。"
  else
    echo "记录的进程不属于当前帧文案，未执行停止操作。"
  fi
else
  echo "帧文案当前没有运行。"
fi
rm -f "$PID_FILE"
read "?按回车键关闭此窗口…" || true
