from __future__ import annotations

import os
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .collector import sync_following
from .config import AppConfig, deep_merge, save_yaml
from .console import bool_text, print_section, render_table, status_text
from .core_authors import load_history
from .feishu_bitable import bitable_app_url
from .personalization import build_interest_profile, topic_descriptions, topic_labels, topic_seed_keywords
from .x_client import XReachClient


STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "focused": {
        "label": "精读优先",
        "description": "更少条目，只看强信号，不纳入回复帖。",
        "default_mode": "core_authors",
        "digest_top_n": 6,
        "include_replies": False,
        "reply_like_threshold": 150,
    },
    "balanced": {
        "label": "均衡推荐",
        "description": "默认推荐，兼顾主线、方法论和高质量回复。",
        "default_mode": "all_following",
        "digest_top_n": 10,
        "include_replies": True,
        "reply_like_threshold": 100,
    },
    "broad": {
        "label": "尽量别漏",
        "description": "覆盖更广，回复帖门槛更低，适合扫面式阅读。",
        "default_mode": "all_following",
        "digest_top_n": 12,
        "include_replies": True,
        "reply_like_threshold": 60,
    },
}


def run_setup(
    base_config_path: Path,
    target_config_path: Path,
    client: XReachClient,
    history_path: Path,
    following_cache_path: Path,
    post_write_generate: Callable[[Path], int] | None = None,
) -> int:
    base_config = AppConfig.load(base_config_path)
    target_override = AppConfig.load(target_config_path) if target_config_path.exists() else AppConfig(raw={}, path=target_config_path)
    config = AppConfig(raw=deep_merge(base_config.raw, target_override.raw), path=target_override.path).raw
    dotenv_values = load_env_file(Path.cwd() / ".env.local")

    print_section("Setup Target")
    print(render_table(["Item", "Value"], [["Base Config", str(base_config_path)], ["Write Target", str(target_config_path)]]))
    print(render_table(["Flow", "What happens"], [["1", "检查依赖、认证、飞书输出条件"], ["2", "同步 following 并确认关注总数"], ["3", "根据 following + 历史结果推荐兴趣主题"], ["4", "确认输出方式与运行偏好"], ["5", "写入本地私有配置"]]))

    existing_handle = str(config.get("x", {}).get("viewer_handle") or "").strip()
    existing_user_id = str(config.get("x", {}).get("viewer_user_id") or "").strip()
    proxy_default = str(
        config.get("x", {}).get("proxy_url")
        or os.getenv("DAILY_X_SIGNAL_XREACH_PROXY")
        or os.getenv("XREACH_PROXY")
        or ""
    )
    existing_user_setup = _is_existing_user_setup(config)
    viewer_handle, viewer_user_id, proxy_url = _collect_access_form(existing_handle, existing_user_id, proxy_default, existing_user_setup)
    if viewer_handle:
        config.setdefault("x", {})["viewer_handle"] = viewer_handle
    if viewer_user_id:
        config.setdefault("x", {})["viewer_user_id"] = viewer_user_id
    config.setdefault("x", {})["proxy_url"] = proxy_url or None
    client = XReachClient(binary=client.binary, workdir=client.workdir, proxy=proxy_url or None)

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
        print(render_table(["Status", "Reason"], [["WARN", "缺少 X 认证或 X 账号配置，跳过同步。"]]))

    if _should_offer_access_retry(following_status, viewer_user_id, proxy_url):
        viewer_user_id, proxy_url = _collect_access_fallback(viewer_user_id, proxy_url)
        if viewer_user_id:
            config.setdefault("x", {})["viewer_user_id"] = viewer_user_id
        if proxy_url:
            config.setdefault("x", {})["proxy_url"] = proxy_url
        client = XReachClient(binary=client.binary, workdir=client.workdir, proxy=proxy_url or None)
        checks = collect_setup_checks(config, client, dotenv_values)
        print_section("补充访问配置后检查")
        print(render_table(["Check", "Status", "Detail"], [[item["name"], status_text(item["ok"]), item["detail"]] for item in checks]))
        if _check_ok(checks, "xreach_auth") and (viewer_handle or viewer_user_id):
            sync_result = sync_following(client, config)
            authors = sync_result["authors"]
            following_status = sync_result["status"]
            _save_following_cache(following_cache_path, authors, following_status)
            print_section("following 重试结果")
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

    history = load_history(history_path)
    interest_profile = build_interest_profile(config, authors, history)
    print_section("当前画像解释")
    print(
        render_table(
            ["Signal", "Value"],
            [
                ["推断方向", "、".join(topic_labels(interest_profile.get("top_topics", []))) or "暂无"],
                ["自动提炼关键词", ", ".join(interest_profile.get("keywords", [])[:10]) or "暂无"],
                ["高命中作者", ", ".join(f"@{handle}" for handle in list(interest_profile.get("trusted_authors", {}).keys())[:5]) or "暂无"],
            ],
        )
    )
    topic_choices = _topic_choices(config, interest_profile)
    print_section("兴趣建议")
    print(
        render_table(
            ["No", "Direction", "Why Recommended", "Suggested Terms"],
            [
                [str(idx), label, reason, examples]
                for idx, (_key, label, _score, reason, examples) in enumerate(topic_choices, start=1)
            ],
        )
    )
    setup_choices = _collect_preference_choices(config, checks, following_status, topic_choices, interest_profile, existing_user_setup)
    selected_topics = setup_choices["selected_topics"]
    final_keywords = setup_choices["final_keywords"]
    disliked_keywords = setup_choices["disliked_keywords"]
    expected_following_count = setup_choices["expected_following_count"]
    following_confirmed = setup_choices["following_confirmed"]
    default_mode = setup_choices["default_mode"]
    digest_top_n = setup_choices["digest_top_n"]
    include_replies = setup_choices["include_replies"]
    reply_like_threshold = setup_choices["reply_like_threshold"]
    enable_feishu = setup_choices["enable_feishu"]
    enable_bitable = setup_choices["enable_bitable"]

    patch = {
        "profile": {
            "preferred_topics": selected_topics,
            "interest_keywords": final_keywords,
            "disliked_keywords": disliked_keywords,
            "default_mode": default_mode,
            "digest_top_n": int(digest_top_n) if str(digest_top_n).strip().isdigit() else config.get("profile", {}).get("digest_top_n", 10),
        },
        "x": {
            "viewer_handle": viewer_handle or None,
            "viewer_user_id": viewer_user_id or None,
            "proxy_url": proxy_url or None,
            "expected_following_count": int(expected_following_count) if str(expected_following_count).strip().isdigit() else None,
            "following_count_confirmed": following_confirmed,
            "include_replies": include_replies,
            "reply_like_threshold": int(reply_like_threshold) if str(reply_like_threshold).strip().isdigit() else config.get("x", {}).get("reply_like_threshold", 100),
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
                ["viewer_handle", _mask_value(str(final_override.get("x", {}).get("viewer_handle", "") or ""))],
                ["viewer_user_id", "已配置" if final_override.get("x", {}).get("viewer_user_id") else "留空"],
                ["proxy_url", final_override.get("x", {}).get("proxy_url", "") or "留空"],
                ["expected_following_count", final_override.get("x", {}).get("expected_following_count", "")],
                ["following_count_confirmed", bool_text(bool(final_override.get("x", {}).get("following_count_confirmed", False)))],
                ["preferred_topics", "、".join(topic_labels(final_override.get("profile", {}).get("preferred_topics", [])))],
                ["interest_keywords", ", ".join(final_override.get("profile", {}).get("interest_keywords", []))],
                ["disliked_keywords", ", ".join(final_override.get("profile", {}).get("disliked_keywords", []))],
                ["default_mode", final_override.get("profile", {}).get("default_mode", "")],
                ["digest_top_n", final_override.get("profile", {}).get("digest_top_n", "")],
                ["include_replies", bool_text(bool(final_override.get("x", {}).get("include_replies", True)))],
                ["reply_like_threshold", final_override.get("x", {}).get("reply_like_threshold", "")],
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
    next_rows = [
        ["验证 following", f"daily-x-signal sync-authors --override-config {target_config_path}"],
        ["生成样例日报", f"daily-x-signal generate --window-mode rolling_24h --override-config {target_config_path}"],
        ["调度检查", f"daily-x-signal schedule-tick --override-config {target_config_path}"],
    ]
    if final_override.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False):
        next_rows.append(["查看帖子追踪表", bitable_app_url({"outputs": {"feishu_bitable": final_override.get("outputs", {}).get("feishu_bitable", {})}}) or ""])
    print(render_table(["Next Step", "Command"], next_rows))
    if post_write_generate is not None:
        print_section("首版日报预览")
        print("已按当前配置自动执行 rolling_24h generate，供你确认首版结果。")
        return post_write_generate(target_config_path)
    return 0


def collect_setup_checks(config: dict[str, Any], client: XReachClient, dotenv_values: dict[str, str]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    xreach_binary = shutil.which(client.binary) if not Path(client.binary).exists() else client.binary
    xreach_ready = bool(xreach_binary) and os.access(str(xreach_binary), os.X_OK)
    if xreach_binary and not xreach_ready:
        xreach_detail = f"{xreach_binary}（存在但不可执行）"
    else:
        xreach_detail = xreach_binary or "未找到 xreach"
    checks.append(
        {
            "key": "xreach_binary",
            "name": "xreach binary",
            "ok": xreach_ready,
            "detail": xreach_detail,
        }
    )
    proxy_url = str(config.get("x", {}).get("proxy_url") or "").strip()
    proxy_ok, proxy_detail = _probe_proxy_url(proxy_url)
    checks.append({"key": "xreach_proxy", "name": "xreach proxy", "ok": proxy_ok, "detail": proxy_detail})
    auth_ok = False
    auth_detail = "xreach 未安装，无法检查认证。"
    if xreach_ready:
        try:
            proc = subprocess.run(
                [client.binary, "auth", "check"],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
            )
            auth_ok = proc.returncode == 0
            auth_detail = (proc.stdout or proc.stderr or "").strip().splitlines()[0] if (proc.stdout or proc.stderr) else "已检查"
        except OSError as exc:
            auth_ok = False
            auth_detail = f"无法执行 xreach auth check：{exc.strerror or exc}"
    elif xreach_binary:
        auth_detail = "xreach 存在但不可执行。"
    checks.append({"key": "xreach_auth", "name": "xreach auth", "ok": auth_ok, "detail": auth_detail})

    viewer_handle = str(config.get("x", {}).get("viewer_handle") or "").strip()
    viewer_user_id = str(config.get("x", {}).get("viewer_user_id") or "").strip()
    checks.append(
        {
            "key": "viewer_profile",
            "name": "viewer config",
            "ok": bool(viewer_handle or viewer_user_id),
            "detail": _viewer_config_status(viewer_handle, viewer_user_id),
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


def _probe_proxy_url(proxy_url: str) -> tuple[bool, str]:
    if not proxy_url:
        return True, "未配置，默认直连"
    try:
        parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    except ValueError:
        return False, "proxy_url 格式无效"
    host = parsed.hostname or ""
    port = parsed.port
    if not host or not port:
        return False, "proxy_url 缺少 host/port"
    if host not in {"127.0.0.1", "localhost"}:
        return True, proxy_url
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True, proxy_url
    except OSError:
        return False, f"{proxy_url} 未监听"


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


def _topic_choices(config: dict[str, Any], interest_profile: dict[str, Any]) -> list[tuple[str, str, float, str, str]]:
    choices: list[tuple[str, str, float, str, str]] = []
    weights = interest_profile.get("topic_weights", {})
    topic_keys = list(config.get("topics", {}).keys())
    label_map = dict(zip(topic_keys, topic_labels(topic_keys)))
    description_map = dict(zip(topic_keys, topic_descriptions(topic_keys)))
    profile_keywords = [str(keyword).lower() for keyword in interest_profile.get("keywords", [])]
    for topic in config.get("topics", {}).keys():
        topic_cfg = config.get("topics", {}).get(topic, {})
        topic_keywords = [str(keyword) for keyword in topic_cfg.get("keywords", [])]
        matched_keywords = [
            keyword
            for keyword in topic_keywords
            if any(keyword.lower() in profile_keyword or profile_keyword in keyword.lower() for profile_keyword in profile_keywords)
        ][:4]
        score = float(weights.get(topic, 0.0))
        if matched_keywords:
            reason = f"following 和历史里更常出现：{', '.join(matched_keywords[:3])}"
        elif score > 0:
            reason = description_map.get(topic, "当前 following 和历史命中更偏向这个方向。")
        else:
            reason = f"{description_map.get(topic, '当前证据较弱，但可以作为补充方向。')}"
        examples = ", ".join(_dedupe([*matched_keywords, *topic_seed_keywords([topic])])[:5])
        choices.append((topic, label_map.get(topic, topic), score, reason, examples))
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


def _ask_choice(prompt: str, options: list[str], default: str) -> str:
    option_text = "/".join(options)
    while True:
        value = _ask(f"{prompt} ({option_text})", default).strip()
        if value in options:
            return value
        print(f"请输入以下选项之一: {option_text}")


def _ask_form(prompt: str, defaults: dict[str, str]) -> dict[str, str]:
    print(prompt)
    print("示例：")
    for key, value in defaults.items():
        print(f"  {key}={value}")
    print("开始输入；每行一个 key=value，直接回车结束并接受默认值。")
    lines: list[str] = []
    while True:
        line = input().strip()
        if not line:
            break
        lines.append(line)
    values = dict(defaults)
    values.update(_parse_form_lines(lines))
    return values


def _collect_access_form(existing_handle: str, existing_user_id: str, existing_proxy_url: str, reuse_existing: bool) -> tuple[str, str, str]:
    print_section("访问配置")
    if reuse_existing and existing_handle:
        print(render_table(["Field", "Status"], [["已有配置", "检测到旧版配置，默认沿用当前 X 访问配置"], ["viewer_handle", _mask_value(existing_handle)]]))
        return existing_handle, existing_user_id, existing_proxy_url
    if existing_handle or existing_user_id or existing_proxy_url:
        print(
            render_table(
                ["Field", "Status"],
                [
                    ["已有配置", "检测到已配置的 X 访问配置"],
                    ["viewer_handle", _mask_value(existing_handle)],
                    ["viewer_user_id", "已配置" if existing_user_id else "留空"],
                    ["proxy_url", existing_proxy_url or "留空"],
                ],
            )
        )
        if _ask_yes_no("是否沿用当前 X 访问配置", True):
            return existing_handle, existing_user_id, existing_proxy_url

    print(
        render_table(
            ["Field", "How to fill"],
            [
                ["X handle", "填 x.com/<handle> 里的那段名字；例如 https://x.com/sama 就填 sama，也可以直接贴主页链接。"],
                ["X user id / proxy_url", "默认不需要现在填写；只有 following 同步失败时再补。"],
            ],
        )
    )
    viewer_handle = _normalize_x_handle(_ask("X handle（必填，可填主页链接）", existing_handle))
    return viewer_handle, existing_user_id, existing_proxy_url


def _collect_access_fallback(existing_user_id: str, existing_proxy_url: str) -> tuple[str, str]:
    print_section("补充访问配置")
    print(
        render_table(
            ["Field", "How to fill"],
            [
                ["viewer_user_id", "可留空；只有 handle 路径不稳定时再补。"],
                ["proxy_url", "可留空；远端访问 X 不稳定时再填，例如 http://127.0.0.1:7890。"],
            ],
        )
    )
    form_values = _ask_form(
        "following 同步看起来不稳定。若你知道 user id 或代理地址，可在这里一次性补充；直接回车则跳过。",
        {
            "viewer_user_id": existing_user_id,
            "proxy_url": existing_proxy_url,
        },
    )
    return form_values["viewer_user_id"].strip(), form_values["proxy_url"].strip()


def _normalize_x_handle(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            return path_parts[0].lstrip("@")
    return raw.removeprefix("@")


def _viewer_config_status(viewer_handle: str, viewer_user_id: str) -> str:
    if viewer_handle and viewer_user_id:
        return "viewer_handle 已配置 / viewer_user_id 已配置"
    if viewer_handle:
        return "viewer_handle 已配置"
    if viewer_user_id:
        return "viewer_user_id 已配置"
    return "缺少 viewer_handle / viewer_user_id"


def _mask_value(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


def _is_existing_user_setup(config: dict[str, Any]) -> bool:
    x_cfg = config.get("x", {})
    return bool(str(x_cfg.get("viewer_handle") or "").strip()) and bool(x_cfg.get("following_count_confirmed", False))


def _should_offer_access_retry(following_status: dict[str, Any], viewer_user_id: str, proxy_url: str) -> bool:
    reason = str(following_status.get("reason", ""))
    if viewer_user_id and proxy_url:
        return False
    return "following 同步失败" in reason or "未同步到任何 following" in reason


def _collect_preference_choices(
    config: dict[str, Any],
    checks: list[dict[str, Any]],
    following_status: dict[str, Any],
    topic_choices: list[tuple[str, str, float, str, str]],
    interest_profile: dict[str, Any],
    existing_user_setup: bool,
) -> dict[str, Any]:
    current_mode = str(config.get("profile", {}).get("default_mode", "all_following"))
    current_top_n = int(config.get("profile", {}).get("digest_top_n", 10))
    current_include_replies = bool(config.get("x", {}).get("include_replies", True))
    current_reply_threshold = int(config.get("x", {}).get("reply_like_threshold", 100))
    feishu_default = bool(config.get("outputs", {}).get("feishu", {}).get("enabled", False))
    bitable_cfg = config.get("outputs", {}).get("feishu_bitable", {})
    bitable_ready = bool(bitable_cfg.get("app_token")) and bool(bitable_cfg.get("table_id"))
    existing_bitable_enabled = bool(bitable_cfg.get("enabled", False)) if bitable_ready else False
    default_topic_selection = ",".join(str(idx) for idx in range(1, min(3, len(topic_choices)) + 1))
    expected_default = str(following_status.get("expected_following_count") or following_status.get("synced_count") or "")
    confirmed_default = bool(config.get("x", {}).get("following_count_confirmed", False))
    existing_keywords = [str(item).strip() for item in config.get("profile", {}).get("interest_keywords", []) if str(item).strip()]
    existing_disliked_keywords = [str(item).strip() for item in config.get("profile", {}).get("disliked_keywords", []) if str(item).strip()]
    recommended_keyword_write = _build_recommended_keywords(interest_profile, _select_topics(topic_choices, default_topic_selection), existing_keywords)

    print_section("输出状态")
    print(
        render_table(
            ["Output", "Current", "Ready"],
            [
                ["Feishu Card", bool_text(feishu_default), bool_text(_check_ok(checks, "feishu_app"))],
                ["Feishu Bitable", bool_text(existing_bitable_enabled), bool_text(bitable_ready)],
                ["Local Files", "Yes", "Yes"],
            ],
        )
    )

    if existing_user_setup:
        print_section("老用户快速更新")
        print(
            render_table(
                ["Field", "Default", "How to fill"],
                [
                    ["topics", default_topic_selection, "只需要确认推荐方向编号；留空采用推荐组合。"],
                    ["extra_keywords", "", "如果你想额外追踪论文、bench、产品名，再补少量关键词。"],
                ],
            )
        )
        print(render_table(["Default Keyword Write"], [[", ".join(recommended_keyword_write) or "暂无建议"]]))
        topic_selection = _ask("选择关注方向编号（留空采用推荐组合）", default_topic_selection)
        extra_keywords = _split_csv(_ask("补充少量关键词（可留空）", ""))
        selected_topics = _select_topics(topic_choices, topic_selection)
        final_keywords = _dedupe([*_build_recommended_keywords(interest_profile, selected_topics, existing_keywords), *extra_keywords])
        return {
            "selected_topics": selected_topics,
            "final_keywords": final_keywords,
            "disliked_keywords": existing_disliked_keywords,
            "expected_following_count": expected_default,
            "following_confirmed": confirmed_default,
            "default_mode": current_mode,
            "digest_top_n": current_top_n,
            "include_replies": current_include_replies,
            "reply_like_threshold": current_reply_threshold,
            "enable_feishu": feishu_default,
            "enable_bitable": existing_bitable_enabled,
        }

    style_choice = _infer_style_choice(current_mode, current_top_n, current_include_replies, current_reply_threshold)
    output_choice = _infer_output_choice(feishu_default, existing_bitable_enabled)
    need_following_confirm = bool(following_status.get("needs_confirmation", False)) or not confirmed_default

    print_section("快速选择")
    print(
        render_table(
            ["Preset", "Style", "Meaning"],
            [[key, value["label"], value["description"]] for key, value in STYLE_PRESETS.items()],
        )
    )
    print(
        render_table(
            ["Output Choice", "Meaning"],
            [
                ["keep", "沿用当前输出设置"],
                ["local_only", "只保留本地 Markdown + JSON"],
                ["card_only", "发送飞书卡片，不写帖子追踪表"],
                ["card_and_table", "发送飞书卡片，并写入帖子追踪表"],
            ],
        )
    )
    if need_following_confirm:
        print(render_table(["Following"], [[f"当前同步到 {following_status.get('synced_count', 0)} 个关注对象；如果看起来合理，就确认这个总数。"]]))
        expected_following_count = _ask("确认关注总数", expected_default)
        following_confirmed = _ask_yes_no("是否确认这个关注总数", confirmed_default or bool(expected_following_count))
    else:
        expected_following_count = expected_default
        following_confirmed = confirmed_default
    selected_topics = _select_topics(topic_choices, _ask("选择关注方向编号（留空采用推荐组合）", default_topic_selection))
    extra_keywords = _split_csv(_ask("补充少量关键词（可留空）", ""))
    style_choice = _ask_choice("选择阅读风格", list(STYLE_PRESETS.keys()), style_choice)
    output_choice = _ask_choice("选择输出方式", ["keep", "local_only", "card_only", "card_and_table"], output_choice)

    style = STYLE_PRESETS[style_choice]
    final_keywords = _dedupe([*_build_recommended_keywords(interest_profile, selected_topics, existing_keywords), *extra_keywords])
    enable_feishu, enable_bitable = _resolve_output_choice(output_choice, feishu_default, existing_bitable_enabled, bitable_ready)
    return {
        "selected_topics": selected_topics,
        "final_keywords": final_keywords,
        "disliked_keywords": existing_disliked_keywords,
        "expected_following_count": expected_following_count,
        "following_confirmed": following_confirmed,
        "default_mode": style["default_mode"],
        "digest_top_n": int(style["digest_top_n"]),
        "include_replies": bool(style["include_replies"]),
        "reply_like_threshold": int(style["reply_like_threshold"]),
        "enable_feishu": enable_feishu,
        "enable_bitable": enable_bitable,
    }


def _build_recommended_keywords(interest_profile: dict[str, Any], selected_topics: list[str], existing_keywords: list[str]) -> list[str]:
    return _dedupe([*interest_profile.get("keywords", [])[:8], *topic_seed_keywords(selected_topics)[:8], *existing_keywords])[:12]


def _infer_style_choice(current_mode: str, current_top_n: int, current_include_replies: bool, current_reply_threshold: int) -> str:
    if current_mode == "core_authors" and not current_include_replies and current_top_n <= 6:
        return "focused"
    if current_top_n >= 12 or current_reply_threshold <= 60:
        return "broad"
    return "balanced"


def _infer_output_choice(feishu_enabled: bool, bitable_enabled: bool) -> str:
    if feishu_enabled and bitable_enabled:
        return "card_and_table"
    if feishu_enabled:
        return "card_only"
    return "local_only"


def _resolve_output_choice(choice: str, feishu_default: bool, bitable_default: bool, bitable_ready: bool) -> tuple[bool, bool]:
    if choice == "keep":
        return feishu_default, bitable_default if bitable_ready else False
    if choice == "local_only":
        return False, False
    if choice == "card_only":
        return True, False
    if choice == "card_and_table":
        return True, bitable_ready
    return feishu_default, bitable_default if bitable_ready else False


def _parse_form_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in lines:
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        cleaned_key = key.strip()
        if cleaned_key:
            values[cleaned_key] = value.strip()
    return values


def _parse_yes_no(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if not lowered:
        return default
    if lowered in {"y", "yes", "true", "1"}:
        return True
    if lowered in {"n", "no", "false", "0"}:
        return False
    return default
