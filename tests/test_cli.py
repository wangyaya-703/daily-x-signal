from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daily_x_signal.cli import build_client


class CliTests(unittest.TestCase):
    def test_build_client_uses_proxy_from_merged_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("daily_x_signal.cli.Path.cwd", return_value=Path(tmpdir)):
                client = build_client({"x": {"proxy_url": "http://127.0.0.1:7890"}})
        self.assertEqual(client.proxy, "http://127.0.0.1:7890")
        self.assertEqual(client.workdir, Path(tmpdir))


if __name__ == "__main__":
    unittest.main()
