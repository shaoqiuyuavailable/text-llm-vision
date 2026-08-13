#!/usr/bin/env bash
# 打包 text-llm-vision 为 .vsix（需 Node.js ≥ 18；自动装 vsce）
set -e
cd "$(dirname "$0")/.."
npm install -g @vscode/vsce >/dev/null 2>&1 || true
npx vsce package
echo "已生成 .vsix，安装：code --install-extension text-llm-vision-*.vsix"
