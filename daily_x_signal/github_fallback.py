from __future__ import annotations

import shutil
import subprocess
from typing import Any


def sync_success_marker(config: dict[str, Any], digest_date: str, sent_at: str) -> dict[str, str | bool]:
    fallback_cfg = config.get("github_fallback", {})
    if not bool(fallback_cfg.get("enabled", False)):
        return {"updated": False, "reason": "github_fallback.enabled=false"}

    owner = str(fallback_cfg.get("owner", "")).strip()
    repo = str(fallback_cfg.get("repo", "")).strip()
    if not owner or not repo:
        return {"updated": False, "reason": "未配置 GitHub 仓库 owner/repo"}

    gh = shutil.which("gh")
    if not gh:
        return {"updated": False, "reason": "未找到 gh CLI"}

    repo_ref = f"{owner}/{repo}"
    date_var = str(fallback_cfg.get("success_date_variable", "LAST_DAILY_X_SIGNAL_SUCCESS_DATE")).strip()
    time_var = str(fallback_cfg.get("success_at_variable", "LAST_DAILY_X_SIGNAL_SUCCESS_AT")).strip()

    _upsert_actions_variable(gh, repo_ref, date_var, digest_date)
    _upsert_actions_variable(gh, repo_ref, time_var, sent_at)
    return {"updated": True, "reason": f"已同步 GitHub 成功标记到 {repo_ref}"}


def _upsert_actions_variable(gh: str, repo_ref: str, name: str, value: str) -> None:
    cmd = [
        gh,
        "variable",
        "set",
        name,
        "--repo",
        repo_ref,
        "--body",
        value,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"GitHub Actions variable 写入失败：{message}")
