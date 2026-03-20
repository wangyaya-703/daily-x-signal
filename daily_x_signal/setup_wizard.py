from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .collector import sync_following
from .config import AppConfig, deep_merge, save_yaml
from .console import bool_text, print_section, render_table, status_text
from .core_authors import load_history
from .personalization import build_interest_profile, topic_labels
from .x_client import XReachClient


def run_setup(
    base_config_path: Path,
    target_config_path: Path,
    client: XReachClient,
    history_path: Path,
    following_cache_path: Path,
) -> int:
    base_config = AppConfig.load(base_config_path)
    target_override = AppConfig.load(target_config_path) if target_config_path.exists() else AppConfig(raw={}, path=target_config_path)
    config = AppConfig(raw=deep_merge(base_config.raw, target_override.raw), path=target_override.path).raw
    dotenv_values = load_env_file(Path.cwd() / ".env.local")

    print_section("Setup Target")
    print(render_table(["Item", "Value"], [["Base Config", str(base_config_path)], ["Write Target", str(target_config_path)]]))

    viewer_handle = _ask("X handle", str(config.get("x", {}).get("viewer_handle") or ""))
    viewer_user_id = _ask("X user id", str(config.get("x", {}).get("viewer_user_id") or ""))
    if viewer_handle:
        config.setdefault("x", {})["viewer_handle"] = viewer_handle
    if viewer_user_id:
        config.setdefault("x", {})["viewer_user_id"] = viewer_user_id

    checks = collect_setup_checks(config, client, dotenv_values)
    print_section("基础检查")
    print(render_table(["Check", "Status", "Detail"], [[item["name"], status_text(item["ok"]), item["detail"]] for item in checks]))

    authors = []
    following_status: dict[str, Any] = {
        "synced_count": 0,
        "expected_following_count": None,
        "completion_ratio": None,
        "is_complete": False,
        "needs_confirmation": True,
        "reason": "尚未执行 following 同步。",
    }
    if _check_ok(checks, "xreach_auth") and (viewer_handle or viewer_user_id):
        sync_result = sync_following(client, config)
        authors = sync_result["authors"]
        following_status = sync_result["status"]
        _save_following_cache(following_cache_path, authors, following_status)
        print_section("following 同步")
        print(
            render_table(
                ["Field", "Value"],
                [
                    ["synced_count", following_status.get("synced_count", 0)],
                    ["expected_following_count", following_status.get("expected_following_count", "")],
                    ["completion_ratio", following_status.get("completion_ratio", "")],
                    ["is_complete", bool_text(bool(following_status.get("is_complete", False)))],
                    ["needs_confirmation", bool_text(bool(following_status.get("needs_confirmation", False)))],
                    ["reason", following_status.get("reason", "")],
                ],
            )
        )
    else:
        print_section("following 同步")
        print(render_table(["Status", "Reason"], [["WARN", "缺少 X 认证或 viewer_handle/viewer_user_id，跳过同步。"]]))

    expected_default = following_status.get("expected_following_count") or following_status.get("synced_count") or ""
    expected_following_count = _ask("确认关注总数", str(expected_default))
    following_confirmed = _ask_yes_no("是否确认这个关注总数", bool(config.get("x", {}).get("following_count_confirmed", False)))

    history = load_history(history_path)
    interest_profile = build_interest_profile(config, authors, history)
    topic_choices = _topic_choices(config, interest_profile)
    print_section("兴趣建议")
    print(
        render_table(
            ["No", "Topic", "Recommended Weight"],
            [[str(idx), label, f"{score:.2f}"] for idx, (_key, label, score) in enumerate(topic_choices, start=1)],
        )
    )
    default_topic_selection = ",".join(str(idx) for idx in range(1, min(3, len(topic_choices)) + 1))
    selected_topic_numbers = _ask("选择关注主题编号（逗号分隔）", default_topic_selection)
    selected_topics = _select_topics(topic_choices, selected_topic_numbers)
    recommended_keywords = interest_profile.get("keywords", [])[:6]
    print(render_table(["Recommended Keywords"], [[", ".join(recommended_keywords) or "暂无建议"]]))
    extra_keywords = _split_csv(_ask("补充关键词（逗号分隔，可留空）", ""))
    disliked_keywords = _split_csv(_ask("屏蔽关键词（逗号分隔，可留空）", ",".join(config.get("profile", {}).get("disliked_keywords", []))))
    final_keywords = _dedupe([*recommended_keywords, *extra_keywords])

    feishu_default = bool(config.get("outputs", {}).get("feishu", {}).get("enabled", False))
    bitable_cfg = config.get("outputs", {}).get("feishu_bitable", {})
    bitable_ready = bool(bitable_cfg.get("app_token")) and bool(bitable_cfg.get("table_id"))
    print_section("输出设置")
    print(
        render_table(
            ["Output", "Current", "Ready", "Detail"],
            [
                ["Feishu Card", bool_text(feishu_default), bool_text(_check_ok(checks, "feishu_app")), "飞书卡片推送"],
                ["Feishu Bitable", bool_text(bool(bitable_cfg.get("enabled", False))), bool_text(bitable_ready), "日报追踪表"],
                ["Local Files", "Yes", "Yes", "Markdown + JSON"],
            ],
        )
    )
    enable_feishu = _ask_yes_no("启用飞书卡片推送", feishu_default)
    enable_bitable = _ask_yes_no("启用飞书多维表格追踪", bool(bitable_cfg.get("enabled", False)) if bitable_ready else False)

    patch = {
        "x": {
            "viewer_handle": viewer_handle or None,
            "viewer_user_id": viewer_user_id or None,
            "expected_following_count": int(expected_following_count) if str(expected_following_count).strip().isdigit() else None,
            "following_count_confirmed": following_confirmed,
        },
        "profile": {
            "preferred_topics": selected_topics,
            "interest_keywords": final_keywords,
            "disliked_keywords": disliked_keywords,
        },
        "outputs": {
            "feishu": {
                "enabled": enable_feishu,
            },
            "feishu_bitable": {
                "enabled": enable_bitable and bitable_ready,
            },
        },
    }
    final_override = deep_merge(target_override.raw, patch)

    print_section("将写入的配置摘要")
    print(
        render_table(
            ["Field", "Value"],
            [
                ["viewer_handle", final_override.get("x", {}).get("viewer_handle", "")],
                ["viewer_user_id", final_override.get("x", {}).get("viewer_user_id", "")],
                ["expected_following_count", final_override.get("x", {}).get("expected_following_count", "")],
                ["following_count_confirmed", bool_text(bool(final_override.get("x", {}).get("following_count_confirmed", False)))],
                ["preferred_topics", "、".join(topic_labels(final_override.get("profile", {}).get("preferred_topics", [])))],
                ["interest_keywords", ", ".join(final_override.get("profile", {}).get("interest_keywords", []))],
                ["disliked_keywords", ", ".join(final_override.get("profile", {}).get("disliked_keywords", []))],
                ["feishu.enabled", bool_text(bool(final_override.get("outputs", {}).get("feishu", {}).get("enabled", False)))],
                ["feishu_bitable.enabled", bool_text(bool(final_override.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False)))],
            ],
        )
    )

    if not _ask_yes_no(f"确认写入 {target_config_path}", True):
        print("已取消写入。")
        return 1

    save_yaml(target_config_path, final_override)
    print_section("完成")
    print(
        render_table(
            ["Next Step", "Command"],
            [
                ["验证 following", f"daily-x-signal sync-authors --override-config {target_config_path}"],
                ["生成样例日报", f"daily-x-signal generate --window-mode rolling_24h --override-config {target_config_path}"],
                ["调度检查", f"daily-x-signal schedule-tick --override-config {target_config_path}"],
            ],
        )
    )
    return 0


def collect_setup_checks(config: dict[str, Any], client: XReachClient, dotenv_values: dict[str, str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    xreach_binary = shutil.which(client.binary) if not Path(client.binary).exists() else client.binary
    checks.append(
        {
            "key": "xreach_binary",
            "name": "xreach binary",
            "ok": bool(xreach_binary),
            "detail": xreach_binary or "未找到 xreach",
        }
    )
    auth_ok = False
    auth_detail = "xreach 未安装，无法检查认证。"
    if xreach_binary:
        proc = subprocess.run(
            [client.binary, "auth", "check"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
        )
        auth_ok = proc.returncode == 0
        auth_detail = (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "已检查"
    checks.append({"key": "xreach_auth", "name": "xreach auth", "ok": auth_ok, "detail": auth_detail})

    viewer_handle = str(config.get("x", {}).get("viewer_handle") or "").strip()
    viewer_user_id = str(config.get("x", {}).get("viewer_user_id") or "").strip()
    checks.append(
        {
            "key": "viewer_profile",
            "name": "viewer config",
            "ok": bool(viewer_handle or viewer_user_id),
            "detail": viewer_handle or viewer_user_id or "缺少 viewer_handle / viewer_user_id",
        }
    )
    checks.append(
        {
            "key": "llm_key",
            "name": "llm api key",
            "ok": bool(_resolve_config_value(config.get("llm", {}).get("api_key"), config.get("llm", {}).get("api_key_env"), dotenv_values)),
            "detail": "已配置" if _resolve_config_value(config.get("llm", {}).get("api_key"), config.get("llm", {}).get("api_key_env"), dotenv_values) else "缺少 llm api key",
        }
    )
    feishu_app = _resolve_config_value(config.get("outputs", {}).get("feishu", {}).get("app_id"), config.get("outputs", {}).get("feishu", {}).get("app_id_env"), dotenv_values)
    feishu_secret = _resolve_config_value(config.get("outputs", {}).get("feishu", {}).get("app_secret"), config.get("outputs", {}).get("feishu", {}).get("app_secret_env"), dotenv_values)
    feishu_receive = _resolve_config_value(config.get("outputs", {}).get("feishu", {}).get("receive_id"), config.get("outputs", {}).get("feishu", {}).get("receive_id_env"), dotenv_values)
    checks.append(
        {
            "key": "feishu_app",
            "name": "feishu app",
            "ok": bool(feishu_app and feishu_secret and feishu_receive),
            "detail": "app_id/app_secret/receive_id 已齐全" if (feishu_app and feishu_secret and feishu_receive) else "飞书凭证不完整",
        }
    )
    bitable_cfg = config.get("outputs", {}).get("feishu_bitable", {})
    checks.append(
        {
            "key": "feishu_bitable",
            "name": "feishu bitable",
            "ok": bool(bitable_cfg.get("app_token") and bitable_cfg.get("table_id")),
            "detail": "app_token/table_id 已配置" if (bitable_cfg.get("app_token") and bitable_cfg.get("table_id")) else "未配置追踪表",
        }
    )
    return checks


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_config_value(raw_value: Any, env_name: Any, dotenv_values: dict[str, str]) -> str | None:
    if raw_value:
        return str(raw_value)
    if env_name:
        env_key = str(env_name)
        value = os.getenv(env_key) or dotenv_values.get(env_key)
        if value:
            return value
    return None


def _save_following_cache(path: Path, authors: list[Any], status: dict[str, Any]) -> None:
    from .store import save_json

    save_json(
        path,
        {
            "refreshed_at": datetime.now().isoformat(),
            "status": status,
            "authors": [author.raw for author in authors],
        },
    )


def _topic_choices(config: dict[str, Any], interest_profile: dict[str, Any]) -> list[tuple[str, str, float]]:
    choices: list[tuple[str, str, float]] = []
    weights = interest_profile.get("topic_weights", {})
    label_map = dict(zip(config.get("topics", {}).keys(), topic_labels(list(config.get("topics", {}).keys()))))
    for topic in config.get("topics", {}).keys():
        choices.append((topic, label_map.get(topic, topic), float(weights.get(topic, 0.0))))
    choices.sort(key=lambda item: item[2], reverse=True)
    return choices


def _select_topics(topic_choices: list[tuple[str, str, float]], selected: str) -> list[str]:
    selected_indexes = [int(item) for item in _split_csv(selected) if item.isdigit()]
    results: list[str] = []
    for index in selected_indexes:
        real_index = index - 1
        if 0 <= real_index < len(topic_choices):
            results.append(topic_choices[real_index][0])
    return results or [item[0] for item in topic_choices[:3] if item[2] > 0][:3]


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        results.append(item)
    return results


def _check_ok(checks: list[dict[str, Any]], key: str) -> bool:
    return any(item.get("key") == key and item.get("ok") for item in checks)


def _ask(prompt: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}
