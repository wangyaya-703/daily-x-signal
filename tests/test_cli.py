from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from daily_x_signal.cli import build_client, collect_candidate_posts
from daily_x_signal.models import Author, Post


class CliTests(unittest.TestCase):
    def test_build_client_uses_proxy_from_merged_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("daily_x_signal.cli.Path.cwd", return_value=Path(tmpdir)):
                client = build_client({"x": {"proxy_url": "http://127.0.0.1:7890"}})
        self.assertEqual(client.proxy, "http://127.0.0.1:7890")
        self.assertEqual(client.workdir, Path(tmpdir))

    def test_collect_candidate_posts_expands_to_next_batch_when_first_batch_empty(self) -> None:
        authors = [Author(handle=f"a{i}") for i in range(1, 6)]
        config = {
            "x": {
                "max_active_authors_per_run": 2,
                "max_authors_per_run": 0,
                "dedupe_by_conversation": True,
            }
        }
        sample_post = Post(
            id="p1",
            conversation_id="c1",
            created_at=datetime.now(),
            text="useful update",
            url="https://x.com/a3/status/p1",
            author=authors[2],
        )

        def fake_collect_posts(_client, batch, _config, _window):
            handles = [author.handle for author in batch]
            if handles == ["a1", "a2"]:
                return []
            if handles == ["a3", "a4"]:
                return [sample_post]
            return []

        with patch("daily_x_signal.cli.collect_posts_for_authors", side_effect=fake_collect_posts):
            candidate_posts, scanned_authors, author_scan_limit = collect_candidate_posts(
                client=object(),
                authors=authors,
                home_candidates=[],
                config=config,
                window=None,
                target_candidate_count=1,
            )

        self.assertEqual([post.id for post in candidate_posts], ["p1"])
        self.assertEqual([author.handle for author in scanned_authors], ["a1", "a2", "a3", "a4"])
        self.assertEqual(author_scan_limit, 5)


if __name__ == "__main__":
    unittest.main()
