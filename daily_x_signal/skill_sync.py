from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^version:\s*[\"']?([^\"'\n]+)[\"']?\s*$", re.MULTILINE)


def inspect_skill_sync(repo_skill_path: Path, installed_skill_path: Path) -> dict[str, Any]:
    repo_exists = repo_skill_path.exists()
    installed_exists = installed_skill_path.exists()

    if not repo_exists:
        return {
            "repo_exists": False,
            "installed_exists": installed_exists,
            "needs_update": False,
        }

    repo_text = repo_skill_path.read_text(encoding="utf-8")
    repo_version = extract_skill_version(repo_text)
    repo_hash = skill_content_hash(repo_text)

    if not installed_exists:
        return {
            "repo_exists": True,
            "installed_exists": False,
            "repo_version": repo_version,
            "installed_version": None,
            "needs_update": False,
        }

    installed_text = installed_skill_path.read_text(encoding="utf-8")
    installed_version = extract_skill_version(installed_text)
    installed_hash = skill_content_hash(installed_text)
    versions_differ = bool(repo_version and installed_version and repo_version != installed_version)
    hashes_differ = repo_hash != installed_hash

    return {
        "repo_exists": True,
        "installed_exists": True,
        "repo_version": repo_version,
        "installed_version": installed_version,
        "needs_update": versions_differ or hashes_differ,
        "sync_command": f"cp {repo_skill_path} {installed_skill_path}",
    }


def extract_skill_version(text: str) -> str | None:
    match = VERSION_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def skill_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
