from __future__ import annotations

import unittest
from unittest.mock import patch

from daily_x_signal.setup_wizard import (
    _infer_output_choice,
    _infer_style_choice,
    _is_existing_user_setup,
    _normalize_x_handle,
    _parse_form_lines,
    _parse_yes_no,
    _probe_proxy_url,
    _resolve_output_choice,
    _select_topics,
    _split_csv,
    _should_offer_access_retry,
    _topic_choices,
    _viewer_config_status,
    collect_setup_checks,
    load_env_file,
)
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

    def test_normalize_x_handle_accepts_profile_url(self) -> None:
        self.assertEqual(_normalize_x_handle("https://x.com/sama"), "sama")
        self.assertEqual(_normalize_x_handle("@karpathy"), "karpathy")

    def test_parse_form_lines_supports_batch_form(self) -> None:
        values = _parse_form_lines(["topics=1,2,4", "extra_keywords=paper, benchmark", "include_replies=no"])
        self.assertEqual(values["topics"], "1,2,4")
        self.assertEqual(values["extra_keywords"], "paper, benchmark")
        self.assertEqual(values["include_replies"], "no")

    def test_parse_yes_no_handles_default_and_known_values(self) -> None:
        self.assertTrue(_parse_yes_no("yes", False))
        self.assertFalse(_parse_yes_no("no", True))
        self.assertTrue(_parse_yes_no("", True))

    def test_viewer_config_status_does_not_expose_raw_identity(self) -> None:
        detail = _viewer_config_status("wangtianyu", "31415926")
        self.assertEqual(detail, "viewer_handle 已配置 / viewer_user_id 已配置")
        self.assertNotIn("wangtianyu", detail)
        self.assertNotIn("31415926", detail)

    def test_should_offer_access_retry_only_when_following_sync_looks_broken(self) -> None:
        self.assertTrue(_should_offer_access_retry({"reason": "following 同步失败，已回退本地缓存：timeout"}, "", ""))
        self.assertTrue(_should_offer_access_retry({"reason": "当前未同步到任何 following，建议检查 viewer 配置或 X 登录态。"}, "", ""))
        self.assertFalse(_should_offer_access_retry({"reason": "following 列表已达到当前配置下的完整性要求。"}, "", ""))
        self.assertFalse(_should_offer_access_retry({"reason": "following 同步失败，已回退本地缓存：timeout"}, "123", "http://127.0.0.1:7890"))

    def test_is_existing_user_setup_requires_handle_and_following_confirmation(self) -> None:
        self.assertTrue(_is_existing_user_setup({"x": {"viewer_handle": "demo", "following_count_confirmed": True}}))
        self.assertFalse(_is_existing_user_setup({"x": {"viewer_handle": "demo", "following_count_confirmed": False}}))
        self.assertFalse(_is_existing_user_setup({"x": {"viewer_handle": "", "following_count_confirmed": True}}))

    def test_infer_style_choice_prefers_expected_presets(self) -> None:
        self.assertEqual(_infer_style_choice("core_authors", 6, False, 150), "focused")
        self.assertEqual(_infer_style_choice("all_following", 12, True, 60), "broad")
        self.assertEqual(_infer_style_choice("all_following", 10, True, 100), "balanced")

    def test_infer_and_resolve_output_choice(self) -> None:
        self.assertEqual(_infer_output_choice(True, True), "card_and_table")
        self.assertEqual(_infer_output_choice(True, False), "card_only")
        self.assertEqual(_infer_output_choice(False, False), "local_only")
        self.assertEqual(_resolve_output_choice("keep", True, True, True), (True, True))
        self.assertEqual(_resolve_output_choice("card_and_table", True, False, False), (True, False))

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

    def test_topic_choices_include_directional_copy_and_seed_terms(self) -> None:
        config = {
            "topics": {
                "papers_algorithms": {"weight": 0.9, "keywords": ["paper", "arxiv", "benchmark"]},
                "model_releases": {"weight": 0.95, "keywords": ["release", "launch", "benchmark"]},
            }
        }
        interest_profile = {
            "topic_weights": {"papers_algorithms": 1.0, "model_releases": 0.8},
            "keywords": ["paper", "benchmark", "launch"],
        }
        choices = _topic_choices(config, interest_profile)
        self.assertEqual(choices[0][0], "papers_algorithms")
        self.assertIn("模型研究 / 论文 / Benchmark", choices[0][1])
        self.assertIn("paper", choices[0][3])
        self.assertIn("benchmark", choices[0][4])


if __name__ == "__main__":
    unittest.main()
