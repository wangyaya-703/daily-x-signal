from __future__ import annotations

import re
import unittest
from pathlib import Path


SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "daily-x-signal" / "SKILL.md"


class DailyXSignalSkillTests(unittest.TestCase):
    def test_skill_frontmatter_marks_user_invocable(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("user-invocable: true", text)
        self.assertIn('emoji: "📰"', text)
        self.assertIn('version: "2026.03.22.5"', text)

    def test_skill_includes_openclaw_workspace_bootstrap_path(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("~/.openclaw/workspace/daily-x-signal", text)
        self.assertIn("git clone https://github.com/wangyaya-703/daily-x-signal.git", text)
        self.assertIn("python3 -m venv .venv", text)
        self.assertIn("source .venv/bin/activate", text)

    def test_skill_requires_setup_before_generate(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        setup_pos = text.find("daily-x-signal setup")
        generate_pos = text.find("daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml")
        self.assertGreaterEqual(setup_pos, 0)
        self.assertGreaterEqual(generate_pos, 0)
        self.assertLess(setup_pos, generate_pos)

    def test_skill_calls_out_missing_dependency_checklist(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        for item in [
            "`Repo`: present / missing",
            "`Python`: present / missing",
            "`xreach`: present / missing",
            "`xreach auth`: ok / failed",
            "`Proxy env`: clean / cleared / active",
            "`config/local.yaml`: present / missing",
        ]:
            self.assertIn(item, text)

    def test_skill_mentions_xreach_auth_check_and_no_fabrication(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("xreach auth check", text)
        self.assertRegex(text, re.compile(r"不要.*编造.*日报内容"))

    def test_skill_clears_dead_local_proxy_before_bootstrap_and_runtime(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY", text)
        self.assertIn("先检查对应端口是否真的有进程在监听", text)

    def test_skill_mentions_persisting_xreach_proxy_in_config(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("x.proxy_url", text)
        self.assertIn("http://127.0.0.1:7890", text)

    def test_skill_sources_dotenv_before_runtime_commands(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("if [ -f .env.local ]; then set -a; source .env.local; set +a; fi", text)

    def test_skill_requires_batch_form_and_no_identity_echo(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("不要直接回显已有的 `viewer_handle` 或 `viewer_user_id` 原值", text)
        self.assertIn("topics=1,2,4", text)
        self.assertIn("不要说“看看 setup 下一步是否允许补关键词”", text)
        self.assertIn("默认只收集 handle", text)
        self.assertIn("只有 following 同步失败时", text)
        self.assertIn("自动执行一次 `daily-x-signal generate --window-mode rolling_24h --override-config config/local.yaml`", text)
        self.assertIn("建议新开一个 OpenClaw 会话", text)
        self.assertIn("老用户只确认推荐兴趣方向", text)
        self.assertIn("style=balanced", text)
        self.assertIn("output=card_and_table", text)
        self.assertIn("push_time=08:30", text)
        self.assertIn("不应该实际推送到飞书或帖子追踪表", text)

    def test_skill_describes_standalone_and_openclaw_modes(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("`standalone`", text)
        self.assertIn("`openclaw`", text)
        self.assertIn("OpenClaw 绑定的 Feishu Bot", text)
        self.assertIn("OpenClaw HEARTBEAT", text)
        self.assertIn("daily-x-signal schedule-tick --override-config config/local.yaml", text)


if __name__ == "__main__":
    unittest.main()
