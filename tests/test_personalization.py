from __future__ import annotations

import unittest

from daily_x_signal.models import Author, Post
from daily_x_signal.personalization import build_interest_profile, snapshot_following_status


class PersonalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "profile": {
                "preferred_topics": ["ai_coding"],
                "interest_keywords": ["codex", "tool calling"],
                "disliked_keywords": [],
            },
            "topics": {
                "ai_coding": {"weight": 1.0, "keywords": ["codex", "mcp", "tool calling"]},
                "agent_frameworks": {"weight": 1.0, "keywords": ["agent", "workflow"]},
            },
            "personalization": {
                "enabled": True,
                "author_profile_sample_limit": 50,
                "interest_keyword_limit": 10,
            },
            "x": {
                "following_sync_max_pages": 20,
                "expected_following_count": 100,
                "following_count_confirmed": False,
                "require_following_confirmation": True,
                "following_completion_ratio_warn_threshold": 0.85,
            },
        }

    def test_build_interest_profile_combines_manual_and_history_signals(self) -> None:
        authors = [
            Author(handle="alice", description="Building codex agent workflow with mcp"),
            Author(handle="bob", description="Tool calling and agent systems"),
        ]
        history = {
            "authors": {
                "alice": {"selected_runs": 3, "avg_priority": 8.5},
            },
            "interest_profile": {
                "topic_hits": {"agent_frameworks": 4},
                "keyword_hits": {"benchmark": 5},
            },
        }

        profile = build_interest_profile(self.config, authors, history)

        self.assertTrue(profile["enabled"])
        self.assertIn("ai_coding", profile["top_topics"])
        self.assertIn("agent_frameworks", profile["top_topics"])
        self.assertIn("codex", profile["keywords"])
        self.assertIn("benchmark", profile["keywords"])
        self.assertIn("alice", profile["trusted_authors"])

    def test_snapshot_following_status_flags_incomplete_and_unconfirmed(self) -> None:
        authors = [Author(handle=f"user{i}") for i in range(60)]

        status = snapshot_following_status(
            self.config,
            authors,
            payload_meta={"has_more": True},
            viewer_profile={"followingCount": 100},
        )

        self.assertEqual(status["synced_count"], 60)
        self.assertEqual(status["expected_following_count"], 100)
        self.assertFalse(status["is_complete"])
        self.assertTrue(status["needs_confirmation"])
        self.assertIn("仍有下一页", status["reason"])

    def test_snapshot_following_status_zero_authors_is_not_complete(self) -> None:
        status = snapshot_following_status(
            self.config,
            [],
            payload_meta={"has_more": False},
            viewer_profile=None,
        )

        self.assertFalse(status["is_complete"])
        self.assertIn("未同步到任何 following", status["reason"])


if __name__ == "__main__":
    unittest.main()
