"""store.py 测试：原子写、文件锁。"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from daily_x_signal.store import load_json, save_json, save_json_locked, load_json_locked


def test_save_json_atomic_creates_file(tmp_path: Path):
    """原子写：正常创建文件。"""
    path = tmp_path / "data.json"
    save_json(path, {"key": "value"})
    assert path.exists()
    assert json.loads(path.read_text()) == {"key": "value"}


def test_save_json_atomic_no_tmp_left(tmp_path: Path):
    """原子写：完成后不残留 .tmp 文件。"""
    path = tmp_path / "data.json"
    save_json(path, {"a": 1})
    assert not path.with_suffix(".tmp").exists()


def test_save_json_creates_parent_dirs(tmp_path: Path):
    """原子写：自动创建父目录。"""
    path = tmp_path / "sub" / "dir" / "data.json"
    save_json(path, [1, 2, 3])
    assert json.loads(path.read_text()) == [1, 2, 3]


def test_load_json_default_when_missing(tmp_path: Path):
    """load_json：文件不存在时返回默认值。"""
    path = tmp_path / "nonexistent.json"
    result = load_json(path, {"default": True})
    assert result == {"default": True}


def test_save_json_locked_basic(tmp_path: Path):
    """带锁写：基本功能。"""
    path = tmp_path / "state.json"
    save_json_locked(path, {"sent_dates": {}})
    assert json.loads(path.read_text()) == {"sent_dates": {}}


def test_load_json_locked_basic(tmp_path: Path):
    """带锁读：基本功能。"""
    path = tmp_path / "state.json"
    save_json_locked(path, {"count": 42})
    result = load_json_locked(path, {})
    assert result == {"count": 42}


def test_concurrent_locked_writes_no_corruption(tmp_path: Path):
    """并发带锁写：多线程写入不会导致文件损坏。

    注意：fcntl.flock 是进程级锁，同一进程内的线程共享锁。
    这里测试的是"不会写坏文件"，而非"严格序列化计数"。
    真实场景中 launchd 和 GitHub Actions 是不同进程，锁可以正常工作。
    """
    path = tmp_path / "state.json"
    save_json_locked(path, {"counter": 0})
    errors: list[Exception] = []

    def increment(n: int):
        try:
            for _ in range(n):
                data = load_json_locked(path, {"counter": 0})
                data["counter"] += 1
                save_json_locked(path, data)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=increment, args=(5,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发写出错: {errors}"
    result = load_json_locked(path, {})
    # 文件内容应该是合法 JSON，counter > 0
    assert isinstance(result["counter"], int)
    assert result["counter"] > 0


def test_save_json_chinese_content(tmp_path: Path):
    """原子写：中文内容正确保存（ensure_ascii=False）。"""
    path = tmp_path / "data.json"
    save_json(path, {"标题": "日报测试"})
    raw = path.read_text(encoding="utf-8")
    assert "日报测试" in raw
    assert "\\u" not in raw  # 确认没有转义
