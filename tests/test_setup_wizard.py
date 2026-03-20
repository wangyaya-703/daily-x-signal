from __future__ import annotations

import unittest

from daily_x_signal.setup_wizard import _select_topics, _split_csv, load_env_file


class SetupWizardTests(unittest.TestCase):
    def test_split_csv_trims_and_filters_empty(self) -> None:
        self.assertEqual(_split_csv(" codex, , agent ,"), ["codex", "agent"])

    def test_select_topics_maps_indexes(self) -> None:
        choices = [
            ("ai_coding", "AI 编程", 1.0),
            ("agent_frameworks", "Agent 框架", 0.8),
            ("model_releases", "模型发布", 0.5),
        ]

        self.assertEqual(_select_topics(choices, "1,3"), ["ai_coding", "model_releases"])

    def test_load_env_file_parses_export_lines(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env.local"
            path.write_text('export FOO="bar"\nBAZ=qux\n', encoding="utf-8")
            self.assertEqual(load_env_file(path), {"FOO": "bar", "BAZ": "qux"})


if __name__ == "__main__":
    unittest.main()
