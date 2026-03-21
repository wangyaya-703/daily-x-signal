from __future__ import annotations

import unittest
from unittest.mock import patch

from daily_x_signal.setup_wizard import _probe_proxy_url, _select_topics, _split_csv, collect_setup_checks, load_env_file
from daily_x_signal.x_client import XReachClient


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

    def test_collect_setup_checks_handles_non_executable_xreach(self) -> None:
        client = XReachClient(binary="/tmp/xreach")
        with patch("daily_x_signal.setup_wizard.Path.exists", return_value=True), patch(
            "daily_x_signal.setup_wizard.os.access", return_value=False
        ):
            checks = collect_setup_checks({}, client, {})

        binary_check = next(item for item in checks if item["key"] == "xreach_binary")
        auth_check = next(item for item in checks if item["key"] == "xreach_auth")
        self.assertFalse(binary_check["ok"])
        self.assertIn("存在但不可执行", binary_check["detail"])
        self.assertFalse(auth_check["ok"])
        self.assertIn("存在但不可执行", auth_check["detail"])

    def test_probe_proxy_url_detects_dead_loopback_proxy(self) -> None:
        ok, detail = _probe_proxy_url("http://127.0.0.1:65535")
        self.assertFalse(ok)
        self.assertIn("未监听", detail)

    def test_collect_setup_checks_includes_proxy_status(self) -> None:
        client = XReachClient(binary="/tmp/xreach")
        with patch("daily_x_signal.setup_wizard.Path.exists", return_value=True), patch(
            "daily_x_signal.setup_wizard.os.access", return_value=False
        ):
            checks = collect_setup_checks({"x": {"proxy_url": "http://127.0.0.1:65535"}}, client, {})
        proxy_check = next(item for item in checks if item["key"] == "xreach_proxy")
        self.assertFalse(proxy_check["ok"])
        self.assertIn("未监听", proxy_check["detail"])


if __name__ == "__main__":
    unittest.main()
