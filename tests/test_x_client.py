from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from daily_x_signal.x_client import XReachClient


class XReachClientTests(unittest.TestCase):
    def test_run_json_uses_proxy_from_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            (config_dir / "local.yaml").write_text("x:\n  proxy_url: http://127.0.0.1:7890\n", encoding="utf-8")
            with patch(
                "daily_x_signal.x_client.subprocess.run",
                return_value=CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),
            ) as mocked_run:
                client = XReachClient(binary="xreach", workdir=tmpdir)
                client.user("wangtianyu")

        cmd = mocked_run.call_args.kwargs.get("args") or mocked_run.call_args.args[0]
        self.assertTrue(str(cmd[0]).endswith("xreach"))
        self.assertEqual(cmd[1:4], ["--proxy", "http://127.0.0.1:7890", "user"])

    def test_explicit_proxy_overrides_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            (config_dir / "local.yaml").write_text("x:\n  proxy_url: http://127.0.0.1:7890\n", encoding="utf-8")
            with patch(
                "daily_x_signal.x_client.subprocess.run",
                return_value=CompletedProcess(args=[], returncode=0, stdout="{}", stderr=""),
            ) as mocked_run:
                client = XReachClient(binary="xreach", workdir=tmpdir, proxy="http://127.0.0.1:18080")
                client.user("wangtianyu")

        cmd = mocked_run.call_args.kwargs.get("args") or mocked_run.call_args.args[0]
        self.assertTrue(str(cmd[0]).endswith("xreach"))
        self.assertEqual(cmd[1:4], ["--proxy", "http://127.0.0.1:18080", "user"])


if __name__ == "__main__":
    unittest.main()
