"""实验记录持久化：读写 state/lab_experiments.json。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..store import load_json, save_json


def load_lab_state(state_path: Path) -> dict:
    """加载实验状态文件，不存在则返回空结构。"""
    return load_json(state_path, {"experiments": {}})


def record_experiment(
    state_path: Path,
    experiment_id: str,
    branch: str,
    post_url: str,
    github_url: str | None,
    author_handle: str,
    status: str,
    brief_date: str,
    log: str = "",
) -> None:
    """记录一个新实验到状态文件。"""
    state = load_lab_state(state_path)
    state["experiments"][experiment_id] = {
        "experiment_id": experiment_id,
        "branch": branch,
        "post_url": post_url,
        "github_url": github_url,
        "author_handle": author_handle,
        "status": status,
        "brief_date": brief_date,
        "deployed_at": datetime.now().isoformat(),
        "log": log,
    }
    save_json(state_path, state)


def update_experiment_status(
    state_path: Path,
    experiment_id: str,
    status: str,
    log: str = "",
) -> None:
    """更新实验状态。"""
    state = load_lab_state(state_path)
    if experiment_id not in state["experiments"]:
        state["experiments"][experiment_id] = {}
    state["experiments"][experiment_id]["status"] = status
    state["experiments"][experiment_id]["updated_at"] = datetime.now().isoformat()
    if log:
        state["experiments"][experiment_id]["log"] = log
    save_json(state_path, state)


def list_experiments_local(state_path: Path) -> list[dict]:
    """列出所有本地记录的实验。"""
    state = load_lab_state(state_path)
    experiments = list(state.get("experiments", {}).values())
    experiments.sort(key=lambda e: e.get("deployed_at", ""), reverse=True)
    return experiments
