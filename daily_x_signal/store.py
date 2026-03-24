"""JSON 持久化工具，支持原子写和文件锁。"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path, default: Any) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str | Path, payload: Any) -> None:
    """原子写：先写临时文件，再 rename 替换，避免写到一半崩溃导致文件损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)  # 原子替换（同一文件系统内）


def save_json_locked(path: str | Path, payload: Any) -> None:
    """带排他锁的原子写，用于 scheduler_state.json 等并发场景。

    锁文件为 {path}.lock，避免 launchd 和 GitHub Actions 同时写入。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            tmp.replace(path)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def load_json_locked(path: str | Path, default: Any) -> Any:
    """带共享锁的读取，配合 save_json_locked 使用。"""
    path = Path(path)
    if not path.exists():
        return default
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_SH)
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
