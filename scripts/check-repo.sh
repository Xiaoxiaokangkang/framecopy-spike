#!/bin/sh
set -eu

cd "$(dirname "$0")/.."

echo "[1/4] 检查必需工具"
command -v node >/dev/null
command -v npm >/dev/null
command -v clang >/dev/null

echo "[2/4] 检查不应入库的运行内容"
for path in data bin .cache; do
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    tracked="$(git ls-files "$path" | head -n 1)"
    if [ -n "$tracked" ]; then
      echo "错误：$path 中存在已跟踪文件：$tracked" >&2
      exit 1
    fi
  fi
done

echo "[3/4] 编译和自动测试"
npm run check

echo "[4/4] 检查完成"
