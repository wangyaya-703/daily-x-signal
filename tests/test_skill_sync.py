from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from daily_x_signal.skill_sync import extract_skill_version, inspect_skill_sync


class SkillSyncTests(unittest.TestCase):
    def test_extract_skill_version_reads_frontmatter(self) -> None:
        text = '---\nname: demo\nversion: "2026.03.22.2"\n---\n'
        self.assertEqual(extract_skill_version(text), "2026.03.22.2")

    def test_inspect_skill_sync_detects_outdated_installed_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_skill = root / "repo" / "SKILL.md"
            installed_skill = root / "installed" / "SKILL.md"
            repo_skill.parent.mkdir(parents=True, exist_ok=True)
            installed_skill.parent.mkdir(parents=True, exist_ok=True)
            repo_skill.write_text('---\nname: demo\nversion: "2"\n---\nnew', encoding="utf-8")
            installed_skill.write_text('---\nname: demo\nversion: "1"\n---\nold', encoding="utf-8")

            status = inspect_skill_sync(repo_skill, installed_skill)

        self.assertTrue(status["needs_update"])
        self.assertEqual(status["repo_version"], "2")
        self.assertEqual(status["installed_version"], "1")
        self.assertIn("cp ", status["sync_command"])

    def test_inspect_skill_sync_skips_warning_when_installed_copy_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_skill = root / "repo" / "SKILL.md"
            installed_skill = root / "installed" / "SKILL.md"
            repo_skill.parent.mkdir(parents=True, exist_ok=True)
            repo_skill.write_text('---\nname: demo\nversion: "2"\n---\nnew', encoding="utf-8")

            status = inspect_skill_sync(repo_skill, installed_skill)

        self.assertFalse(status["needs_update"])
        self.assertTrue(status["repo_exists"])
        self.assertFalse(status["installed_exists"])


if __name__ == "__main__":
    unittest.main()
