from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from .feishu_bitable import bitable_app_url, upsert_feishu_bitable
from .log import logger
from .models import Report
from .store import save_json

# 重试配置
_MAX_RETRIES = 3
_RETRY_BASE_SEC = 1


def _retry_request(
    func: Callable[[], requests.Response],
    label: str = "HTTP request",
) -> requests.Response:
    """带指数退避的 HTTP 请求重试。只对网络错误和 5xx 重试，4xx 直接抛出。"""
    for attempt in range(_MAX_RETRIES):
        try:
            response = func()
            if response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("%s 返回 %d，%ds 后重试 (%d/%d)", label, response.status_code, wait, attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("%s 网络错误: %s，%ds 后重试 (%d/%d)", label, type(exc).__name__, wait, attempt + 1, _MAX_RETRIES)
                time.sleep(wait)
            else:
                logger.error("%s 重试 %d 次后仍失败: %s", label, _MAX_RETRIES, exc)
                raise
    # 不应该到这里，但以防万一
    raise RuntimeError(f"{label} 重试次数用尽")


def build_feishu_card(report: Report, config: dict[str, Any]) -> dict[str, Any]:
    theme = config["outputs"]["feishu"].get("card_theme", "blue")
    top_n = int(config["outputs"]["feishu"].get("top_n", 10))
    top_posts = report.top_posts[: min(len(report.top_posts), top_n)]
    stats = report.section_stats or {}
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "**🧭 今日概览**"},
        {
            "tag": "markdown",
            "content": (
                f"**时间窗口**：{report.window_start.strftime('%m-%d %H:%M')} -> "
                f"{report.window_end.strftime('%m-%d %H:%M')}\n"
                f"**候选**：{report.metadata.get('candidate_count', 0)} 条  "
                f"**作者**：{report.metadata.get('author_count', 0)} 位  "
                f"**高匹配**：{stats.get('high_fit_posts', 0)} 条"
            ),
        },
        {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": f"高信号: {stats.get('high_signal_posts', 0)}"},
                {"tag": "plain_text", "content": f"主线: {'、'.join(stats.get('top_topics', [])[:3]) or '暂无'}"},
            ],
        },
    ]
    if report.overview_bullets:
        elements.extend({"tag": "markdown", "content": f"- {bullet}"} for bullet in report.overview_bullets[:4])
    if report.must_read:
        elements.extend(
            [
                {"tag": "hr"},
                {"tag": "markdown", "content": f"**🎯 今日必读**：[{report.must_read.author.handle}]({report.must_read.url})"},
                {"tag": "markdown", "content": report.must_read.why_it_matters},
                {
                    "tag": "markdown",
                    "content": "\n".join(f"- {bullet}" for bullet in report.must_read.summary_bullets[:4]),
                },
            ]
        )
    elements.extend([{"tag": "hr"}, {"tag": "markdown", "content": f"**📊 Top {len(top_posts)} 贴分析**"}])
    for idx, post in enumerate(top_posts, start=1):
        analysis_lines = [
            f"**TOP {idx}**  @{post.author.handle}",
            f"**价值判断**：{post.why_it_matters}",
        ]
        if post.summary_bullets:
            analysis_lines.append("**你该看什么**")
            analysis_lines.extend(f"- {bullet}" for bullet in post.summary_bullets[:3])
        analysis_lines.append(f"[原帖]({post.url})")
        elements.append({"tag": "markdown", "content": "\n".join(analysis_lines)})
    if report.watchlist_authors:
        elements.extend([{"tag": "hr"}, {"tag": "markdown", "content": "**👀 建议额外关注**"}])
        for item in report.watchlist_authors[:5]:
            elements.append({"tag": "markdown", "content": f"- @{item.get('handle', '')}：{item.get('reason', '')}"})
    tracker_url = bitable_app_url(config)
    if tracker_url and bool(config.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False)):
        elements.extend(
            [
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看帖子追踪表"},
                            "type": "primary",
                            "url": tracker_url,
                        }
                    ],
                },
            ]
        )
    if config["outputs"]["feishu"].get("mention_all", False):
        elements.append({"tag": "markdown", "content": "<at id=all></at>"})

    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": theme,
            "title": {"tag": "plain_text", "content": f"X 晨报 {report.generated_at.strftime('%Y-%m-%d')}"},
        },
        "elements": elements,
    }


def deliver_feishu(report: Report, config: dict[str, Any]) -> tuple[Path | None, int | None]:
    feishu_cfg = config["outputs"]["feishu"]
    if not feishu_cfg.get("enabled", False):
        return None, None
    preview_dir = Path(feishu_cfg["preview_directory"])
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"daily-brief-{report.generated_at.strftime('%Y-%m-%d')}.json"
    delivery_type = _resolve_delivery_type(config)

    if delivery_type == "app":
        receive_id = _resolve_feishu_value(config, "receive_id")
        payload = {
            "receive_id": receive_id or "",
            "msg_type": "interactive",
            "content": build_feishu_card(report, config),
        }
        save_json(preview_path, payload)
        if not receive_id:
            return preview_path, None
        response = _send_app_message(config, receive_id, payload["content"])
        return preview_path, response.status_code

    payload = {
        "msg_type": "interactive",
        "card": build_feishu_card(report, config),
    }
    save_json(preview_path, payload)
    webhook_url = _resolve_value(feishu_cfg.get("webhook_url"), feishu_cfg.get("bot_webhook_env", ""))
    if not webhook_url:
        return preview_path, None
    logger.info("飞书 webhook 投递开始")
    response = _retry_request(
        lambda: requests.post(webhook_url, json=payload, timeout=30),
        label="飞书 webhook",
    )
    logger.info("飞书 webhook 投递成功: %d", response.status_code)
    return preview_path, response.status_code


def deliver_feishu_bitable(report: Report, config: dict[str, Any]) -> tuple[Path | None, str | None]:
    return upsert_feishu_bitable(report, config, get_tenant_access_token)


def _send_app_message(config: dict[str, Any], receive_id: str, card: dict[str, Any]) -> requests.Response:
    tenant_access_token = get_tenant_access_token(config)
    receive_id_type = _resolve_receive_id_type(config)
    logger.info("飞书 app 消息投递开始 (receive_id_type=%s)", receive_id_type)
    response = _retry_request(
        lambda: requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={
                "Authorization": f"Bearer {tenant_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": requests.compat.json.dumps(card, ensure_ascii=False),
            },
            timeout=30,
        ),
        label="飞书 app 消息",
    )
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"Feishu app delivery failed: {payload}")
    logger.info("飞书 app 消息投递成功")
    return response


def get_tenant_access_token(config: dict[str, Any]) -> str:
    app_id = _resolve_feishu_value(config, "app_id")
    app_secret = _resolve_feishu_value(config, "app_secret")
    if not app_id or not app_secret:
        raise ValueError("Feishu app delivery requires app_id and app_secret.")

    token_response = _retry_request(
        lambda: requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=30,
        ),
        label="飞书 token 获取",
    )
    tenant_access_token = token_response.json().get("tenant_access_token")
    if not tenant_access_token:
        raise ValueError(f"Missing tenant_access_token: {token_response.text}")
    return str(tenant_access_token)


def _resolve_value(raw_value: Any, env_name: str) -> str | None:
    if raw_value:
        return str(raw_value)
    if env_name:
        value = os.getenv(str(env_name))
        if value:
            return value
    return None


def _resolve_delivery_type(config: dict[str, Any]) -> str:
    feishu_cfg = config["outputs"]["feishu"]
    delivery_type = str(feishu_cfg.get("delivery_type", "webhook")).strip().lower()
    if _is_openclaw_host(config) and bool(config.get("openclaw", {}).get("use_linked_feishu_bot", True)):
        return "app"
    return delivery_type


def _resolve_feishu_value(config: dict[str, Any], key: str) -> str | None:
    feishu_cfg = config["outputs"]["feishu"]
    env_key_map = {
        "app_id": "app_id_env",
        "app_secret": "app_secret_env",
        "receive_id": "receive_id_env",
    }
    resolved = _resolve_value(feishu_cfg.get(key), str(feishu_cfg.get(env_key_map.get(key, ""), "")))
    if resolved:
        return resolved
    if not (_is_openclaw_host(config) and bool(config.get("openclaw", {}).get("use_linked_feishu_bot", True))):
        return None
    openclaw_cfg = config.get("openclaw", {})
    fallback_env_map = {
        "app_id": "bot_app_id_env",
        "app_secret": "bot_app_secret_env",
        "receive_id": "bot_receive_id_env",
    }
    env_name = str(openclaw_cfg.get(fallback_env_map[key], "") or "")
    if not env_name:
        return None
    value = os.getenv(env_name)
    return value or None


def _resolve_receive_id_type(config: dict[str, Any]) -> str:
    receive_id_type = str(config["outputs"]["feishu"].get("receive_id_type", "")).strip()
    if receive_id_type:
        return receive_id_type
    if _is_openclaw_host(config) and bool(config.get("openclaw", {}).get("use_linked_feishu_bot", True)):
        return str(config.get("openclaw", {}).get("bot_receive_id_type", "open_id")).strip() or "open_id"
    return "open_id"


def _is_openclaw_host(config: dict[str, Any]) -> bool:
    return str(config.get("runtime", {}).get("host_mode", "standalone")).strip().lower() == "openclaw"
