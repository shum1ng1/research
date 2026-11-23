#!/bin/bash
# Double-click on macOS. This file does not contain secrets.
# Finder starts .command files from your home directory, so jump to the repo first.
cd "$(dirname "$0")" || exit 1

pause() {
  echo
  read -r -p "按回车关闭窗口... " _
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 python3。请先安装 Python 3，然后再双击本文件。"
  pause
  exit 1
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv || { pause; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -r requirements.txt || { pause; exit 1; }

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已在本机生成 .env（从 .env.example 复制）。"
  echo "实况运行前，用文本编辑器填入 DEEPSEEK_API_KEY 和 SEC_USER_AGENT。"
  echo ".env 已被 git 忽略，不要提交，也不要把密钥贴到聊天里。"
fi

echo
echo "1) 离线演示（默认，不需要密钥，不访问网络）"
echo "2) 实况官方 DeepSeek（读取本机 .env）"
echo "直接回车等于 1。公司默认 MSFT，研究日 2026-11-13，时区 Asia/Hong_Kong。"
read -r choice
choice="${choice:-1}"

args=(--ticker MSFT --as-of 2026-11-13 --timezone Asia/Hong_Kong)
if [[ "$choice" == "2" ]]; then
  args+=(--live --provider deepseek)
else
  args+=(--offline)
fi

python -m integration "${args[@]}"
status=$?
pause
exit "$status"
