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
  echo "请先用文本编辑器填入 DEEPSEEK_API_KEY 和 SEC_USER_AGENT，然后再双击。"
  echo ".env 已被 git 忽略，不要提交，也不要把密钥贴到聊天里。"
  pause
  exit 1
fi

if ! grep -Eq '^DEEPSEEK_API_KEY=.+' .env || ! grep -Eq '^SEC_USER_AGENT=.+' .env; then
  echo ".env 里的 DEEPSEEK_API_KEY 或 SEC_USER_AGENT 还是空的。"
  echo "填好这两项后再双击。不要把密钥贴到聊天里。"
  pause
  exit 1
fi

echo
echo "将使用本机 .env 调用官方 DeepSeek，依次生成三份英文报告：MSFT、CSCO、AAPL。"
echo "研究日 2026-11-13，时区 Asia/Hong_Kong。"
echo "这一步会访问 DeepSeek、Yahoo 和 SEC。按回车开始，输入 q 再回车则取消。"
read -r choice
if [[ "$choice" == "q" || "$choice" == "Q" ]]; then
  exit 0
fi

python -m integration.pack --live --provider deepseek
status=$?
echo
echo "三份报告在 course_pack/live/MSFT、course_pack/live/CSCO、course_pack/live/AAPL。"
echo "每家公司的 PDF 在该目录的 reports/ 下面。"
pause
exit "$status"
