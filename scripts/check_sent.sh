#!/usr/bin/env bash
# 快速检查今日 X 日报是否已发送
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TODAY=$(date +%Y-%m-%d)
STATE_FILE="state/scheduler_state.json"

if [ ! -f "$STATE_FILE" ]; then
  echo "PENDING: state 文件不存在，今日尚未发送"
  exit 1
fi

if python3 -c "
import json, sys
state = json.load(open(\"$STATE_FILE\"))
if \"$TODAY\" in state.get(\"sent_dates\", {}):
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
  echo "SKIP: $TODAY 的 X 日报已发送，无需重复执行。"
  exit 0
else
  echo "PENDING: $TODAY 的 X 日报尚未发送，继续执行。"
  exit 1
fi
