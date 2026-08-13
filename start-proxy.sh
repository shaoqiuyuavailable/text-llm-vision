#!/usr/bin/env bash
# visual-ds 代理启动脚本（macOS / Linux）
# 幂等：端口 8787 已监听则不重复启动。用法：bash start-proxy.sh
set -e

DIR="${HOME}/.claude/vision-eyes"
STATE="${DIR}/state"
PY=python3

# 默认档位：1=fast（如 state 不存在）
if [ ! -f "${STATE}" ]; then
  mkdir -p "${DIR}"
  printf '1' > "${STATE}"
fi

# 幂等检查：8787 已监听则直接退出
if lsof -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "proxy already running on 8787"
  exit 0
fi

cd "${DIR}"
nohup "${PY}" -m uvicorn proxy:app --port 8787 > proxy.log 2>&1 &
echo "proxy started (PID $!)"
exit 0
