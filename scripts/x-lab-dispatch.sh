#!/usr/bin/env bash
# X-Lab 实操候选派发：读取当日日报，筛选候选，写入 pending.json，并推送飞书候选卡片。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f "$ROOT_DIR/.env.local" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi

TODAY="$(TZ=Asia/Shanghai date +%Y-%m-%d)"
BRIEF_JSON="$ROOT_DIR/output/daily-brief-$TODAY.json"
XLAB_DIR="$HOME/.openclaw/workspace/x-lab"
PENDING_JSON="$XLAB_DIR/pending.json"
DISPATCH_LOG="$ROOT_DIR/state/dispatch-log-$TODAY.json"

if [ ! -f "$BRIEF_JSON" ]; then
  echo "SKIP: 当日日报不存在：$BRIEF_JSON"
  exit 0
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
else
  echo "ERROR: 未找到可用 Python 解释器。" >&2
  exit 127
fi

"$PYTHON_BIN" - "$ROOT_DIR" "$BRIEF_JSON" "$PENDING_JSON" "$DISPATCH_LOG" "$TODAY" <<'PY'
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
brief_json = Path(sys.argv[2])
pending_json = Path(sys.argv[3])
dispatch_log = Path(sys.argv[4])
today = sys.argv[5]

sys.path.insert(0, str(root))

from daily_x_signal.lab.scanner import scan_for_candidates  # noqa: E402
from daily_x_signal.lab.feishu_selector import push_candidates_to_feishu  # noqa: E402
from daily_x_signal.config import AppConfig  # noqa: E402


def _extract_github_url(post: dict) -> str:
    pattern = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+", re.IGNORECASE)
    candidates = []
    for field in ("url", "text", "why_it_matters"):
        val = post.get(field)
        if isinstance(val, str):
            candidates.append(val)
    for field in ("summary_bullets", "tags"):
        val = post.get(field)
        if isinstance(val, list):
            candidates.extend(str(x) for x in val)
    joined = "\n".join(candidates)
    m = pattern.search(joined)
    return m.group(0).rstrip(').,]') if m else ""


with brief_json.open("r", encoding="utf-8") as f:
    brief = json.load(f)

candidates = scan_for_candidates(brief)
if not candidates:
    print("SKIP: 本期日报未发现实操候选帖子")
    sys.exit(0)

created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

tasks = []
for idx, cand in enumerate(candidates, start=1):
    post = cand.get("post", {})
    task = {
        "index": idx,
        "status": "pending",
        "created_at": created_at,
        "source_date": today,
        "actionable_score": cand.get("actionable_score", 0),
        "reason": cand.get("reason", ""),
        "github_url": _extract_github_url(post),
        "post": {
            "id": post.get("id", ""),
            "url": post.get("url", ""),
            "title": post.get("title", ""),
            "author_handle": post.get("author", {}).get("handle", ""),
            "priority_label": post.get("priority_label", ""),
            "summary_bullets": post.get("summary_bullets", [])[:3],
        },
    }
    tasks.append(task)

pending_json.parent.mkdir(parents=True, exist_ok=True)
dispatch_log.parent.mkdir(parents=True, exist_ok=True)

pending_payload = {
    "date": today,
    "generated_at": created_at,
    "source_brief": str(brief_json),
    "status": "pending",
    "tasks": tasks,
}
with pending_json.open("w", encoding="utf-8") as f:
    json.dump(pending_payload, f, ensure_ascii=False, indent=2)

log_payload = {
    "date": today,
    "generated_at": created_at,
    "source_brief": str(brief_json),
    "candidate_count": len(candidates),
    "pending_path": str(pending_json),
    "candidates": [
        {
            "index": item["index"],
            "author_handle": item["post"]["author_handle"],
            "title": item["post"]["title"],
            "actionable_score": item["actionable_score"],
            "github_url": item["github_url"],
        }
        for item in tasks
    ],
}
with dispatch_log.open("w", encoding="utf-8") as f:
    json.dump(log_payload, f, ensure_ascii=False, indent=2)

print(f"OK: 生成候选 {len(candidates)} 条，已写入 {pending_json}")
print(f"OK: 已写入派发日志 {dispatch_log}")

# 推送飞书候选卡片（失败不终止，避免丢失 pending.json）
if os.getenv("DISPATCH_NO_PUSH", "") == "1":
    print("SKIP: DISPATCH_NO_PUSH=1，跳过飞书推送")
    sys.exit(0)

try:
    default_cfg = root / "config" / "default.yaml"
    local_cfg = root / "config" / "local.yaml"
    cfg = AppConfig.load(str(default_cfg)).merged_with(str(local_cfg) if local_cfg.exists() else None).raw
    enabled = bool(cfg.get("outputs", {}).get("feishu", {}).get("enabled", False))
    if not enabled:
        print("SKIP: outputs.feishu.enabled=false，未推送飞书卡片")
    else:
        msg_id = push_candidates_to_feishu(candidates, cfg)
        print(f"OK: 飞书候选卡片已发送 (message_id={msg_id})")
except Exception as exc:
    print(f"WARN: 飞书推送失败，但 pending 已落盘: {exc}")
PY
