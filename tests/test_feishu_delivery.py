from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from daily_x_signal.feishu import _resolve_delivery_type, _resolve_feishu_value, _resolve_receive_id_type, build_feishu_card
from daily_x_signal.models import Author, Post, Report


class FeishuDeliveryTests(unittest.TestCase):
    def test_build_feishu_card_includes_bitable_button_when_tracker_enabled(self) -> None:
        now = datetime.now(timezone.utc)
        post = Post(
            id="p1",
            conversation_id="c1",
            created_at=now,
            text="post",
            url="https://x.com/demo/status/p1",
            author=Author(handle="demo"),
            summary_bullets=["要点 1"],
            why_it_matters="值得看",
            priority_label="A",
        )
        report = Report(
            generated_at=now,
            window_start=now - timedelta(hours=24),
            window_end=now,
            mode="all_following",
            top_posts=[post],
            must_read=post,
            watchlist_authors=[],
            overview_bullets=["今天主线明确"],
            section_stats={},
            metadata={"candidate_count": 1, "author_count": 1},
        )
        config = {
            "outputs": {
                "feishu": {"card_theme": "blue", "top_n": 10, "mention_all": False},
                "feishu_bitable": {"enabled": True, "app_token": "app123"},
            }
        }

        card = build_feishu_card(report, config)
        action_blocks = [element for element in card["elements"] if element.get("tag") == "action"]

        self.assertEqual(len(action_blocks), 1)
        self.assertEqual(action_blocks[0]["actions"][0]["text"]["content"], "查看帖子追踪表")
        self.assertEqual(action_blocks[0]["actions"][0]["url"], "https://bytedance.larkoffice.com/base/app123")

    def test_standalone_uses_regular_feishu_config(self) -> None:
        config = {
            "runtime": {"host_mode": "standalone"},
            "outputs": {
                "feishu": {
                    "delivery_type": "webhook",
                    "receive_id_type": "email",
                    "app_id": "cli-app",
                }
            },
        }
        self.assertEqual(_resolve_delivery_type(config), "webhook")
        self.assertEqual(_resolve_feishu_value(config, "app_id"), "cli-app")
        self.assertEqual(_resolve_receive_id_type(config), "email")

    def test_openclaw_prefers_linked_bot_envs(self) -> None:
        config = {
            "runtime": {"host_mode": "openclaw"},
            "outputs": {
                "feishu": {
                    "delivery_type": "webhook",
                    "app_id_env": "TEST_UNUSED_FEISHU_APP_ID",
                    "receive_id_type": "",
                }
            },
            "openclaw": {
                "use_linked_feishu_bot": True,
                "bot_app_id_env": "OPENCLAW_FEISHU_APP_ID",
                "bot_receive_id_env": "OPENCLAW_FEISHU_RECEIVE_ID",
                "bot_receive_id_type": "chat_id",
            },
        }
        env = {
            "OPENCLAW_FEISHU_APP_ID": "openclaw-app",
            "OPENCLAW_FEISHU_RECEIVE_ID": "oc-chat",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(_resolve_delivery_type(config), "app")
            self.assertEqual(_resolve_feishu_value(config, "app_id"), "openclaw-app")
            self.assertEqual(_resolve_feishu_value(config, "receive_id"), "oc-chat")
            self.assertEqual(_resolve_receive_id_type(config), "chat_id")

    def test_openclaw_keeps_explicit_feishu_values(self) -> None:
        config = {
            "runtime": {"host_mode": "openclaw"},
            "outputs": {
                "feishu": {
                    "delivery_type": "app",
                    "app_id": "explicit-app",
                    "receive_id": "explicit-receiver",
                    "receive_id_type": "email",
                }
            },
            "openclaw": {
                "use_linked_feishu_bot": True,
                "bot_app_id_env": "OPENCLAW_FEISHU_APP_ID",
                "bot_receive_id_env": "OPENCLAW_FEISHU_RECEIVE_ID",
                "bot_receive_id_type": "open_id",
            },
        }
        with patch.dict(os.environ, {"OPENCLAW_FEISHU_APP_ID": "ignored"}, clear=False):
            self.assertEqual(_resolve_feishu_value(config, "app_id"), "explicit-app")
            self.assertEqual(_resolve_feishu_value(config, "receive_id"), "explicit-receiver")
            self.assertEqual(_resolve_receive_id_type(config), "email")


if __name__ == "__main__":
    unittest.main()
