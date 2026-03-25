"""llm.py 测试：重试逻辑、缓存、错误回传。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from daily_x_signal.llm import LLMClient, _build_cache_key, _load_cache, _save_cache
from daily_x_signal.models import Author, Post


def _make_post(post_id: str = "123", text: str = "test post") -> Post:
    return Post(
        id=post_id,
        conversation_id=post_id,
        created_at=datetime(2026, 3, 24),
        text=text,
        url=f"https://x.com/user/status/{post_id}",
        author=Author(handle="testuser"),
    )


def _make_config(**overrides) -> dict:
    base = {
        "llm": {
            "enabled": True,
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
            "api_style": "chat_completions",
            "api_key": "test-key",
            "request_timeout_sec": 10,
            "temperature": 0.2,
            "max_input_posts": 12,
        }
    }
    base["llm"].update(overrides)
    return base


class TestLLMRetry:
    """LLM 重试逻辑测试。"""

    @patch("daily_x_signal.llm.requests.post")
    @patch("daily_x_signal.llm._load_cache", return_value=None)
    @patch("daily_x_signal.llm.time.sleep")
    def test_retry_on_connection_error(self, mock_sleep, mock_cache, mock_post):
        """网络错误触发重试。"""
        # 前 2 次连接失败，第 3 次成功
        llm_response = {"overview": [], "posts": [], "must_read_id": None, "watchlist": []}
        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.raise_for_status = MagicMock()
        success_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(llm_response)}}]
        }
        mock_post.side_effect = [
            requests.ConnectionError("refused"),
            requests.ConnectionError("refused"),
            success_resp,
        ]

        client = LLMClient(_make_config())
        result, error = client.summarize_posts([_make_post()])
        assert result is not None
        assert error is None
        assert mock_sleep.call_count == 2  # 2 次退避

    @patch("daily_x_signal.llm.requests.post")
    @patch("daily_x_signal.llm._load_cache", return_value=None)
    def test_4xx_no_retry(self, mock_cache, mock_post):
        """4xx 错误不重试，直接返回错误。"""
        resp = MagicMock()
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_post.return_value = resp

        client = LLMClient(_make_config())
        result, error = client.summarize_posts([_make_post()])
        assert result is None
        assert "401" in error
        # 两种 API 风格各调用一次，4xx 不重试
        assert mock_post.call_count == 2

    @patch("daily_x_signal.llm.requests.post")
    @patch("daily_x_signal.llm._load_cache", return_value=None)
    def test_returns_error_description_on_failure(self, mock_cache, mock_post):
        """所有重试失败后返回错误描述。"""
        mock_post.side_effect = requests.Timeout("timeout")

        client = LLMClient(_make_config())
        result, error = client.summarize_posts([_make_post()])
        assert result is None
        assert error is not None
        assert "Timeout" in error


class TestLLMCache:
    """LLM 缓存测试。"""

    def test_cache_key_deterministic(self):
        """相同输入产生相同 key。"""
        posts = [_make_post("1"), _make_post("2")]
        profile = {"top_topics": ["ai"]}
        key1 = _build_cache_key(posts, profile)
        key2 = _build_cache_key(posts, profile)
        assert key1 == key2

    def test_cache_key_changes_with_different_posts(self):
        """不同帖子产生不同 key。"""
        key1 = _build_cache_key([_make_post("1")], {})
        key2 = _build_cache_key([_make_post("2")], {})
        assert key1 != key2

    @patch("daily_x_signal.llm._CACHE_PATH")
    def test_cache_round_trip(self, mock_path, tmp_path):
        """缓存写入后能读取。"""
        cache_file = tmp_path / "llm_cache.json"
        cache_file.write_text("{}")

        with patch("daily_x_signal.llm._CACHE_PATH", cache_file):
            data = {"overview": ["test"], "posts": []}
            _save_cache("testkey", data)
            loaded = _load_cache("testkey")
            assert loaded == data

    @patch("daily_x_signal.llm.requests.post")
    @patch("daily_x_signal.llm._save_cache")
    def test_cache_hit_skips_api(self, mock_save, mock_post):
        """缓存命中时不调用 API。"""
        cached_data = {"overview": ["cached"], "posts": [], "must_read_id": None, "watchlist": []}
        with patch("daily_x_signal.llm._load_cache", return_value=cached_data):
            client = LLMClient(_make_config())
            result, error = client.summarize_posts([_make_post()])
            assert result == cached_data
            assert error is None
            mock_post.assert_not_called()


class TestLLMDisabled:
    """LLM 禁用场景。"""

    def test_disabled_returns_none(self):
        """LLM 禁用时返回 (None, None)。"""
        config = _make_config(enabled=False)
        client = LLMClient(config)
        result, error = client.summarize_posts([_make_post()])
        assert result is None
        assert error is None

    def test_empty_posts_returns_none(self):
        """空帖子列表返回 (None, None)。"""
        client = LLMClient(_make_config())
        result, error = client.summarize_posts([])
        assert result is None
        assert error is None
