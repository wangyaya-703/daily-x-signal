from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .models import Post, Report
from .personalization import topic_labels
from .store import save_json


def upsert_feishu_bitable(
    report: Report,
    config: dict[str, Any],
    get_token,
) -> tuple[Path | None, str | None]:
    table_cfg = config.get("outputs", {}).get("feishu_bitable", {})
    if not bool(table_cfg.get("enabled", False)):
        return None, None

    app_token = str(table_cfg.get("app_token", "")).strip()
    table_id = str(table_cfg.get("table_id", "")).strip()
    if not app_token or not table_id:
        raise ValueError("Feishu bitable 已启用，但 app_token/table_id 未配置。")

    preview_dir = Path(str(table_cfg.get("preview_directory", "output/feishu-bitable-preview")))
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_path = preview_dir / f"daily-brief-{report.generated_at.strftime('%Y-%m-%d')}.json"

    rows = build_post_rows(report, table_cfg)
    save_json(preview_path, rows)

    token = get_token(config)
    upserted = 0
    for row in rows:
        existing_record_id = _find_record_id_by_post_id(token, app_token, table_id, table_cfg, str(row[_field_name(table_cfg, "post_id", "Post ID")]))
        if existing_record_id:
            _update_record(token, app_token, table_id, existing_record_id, row)
        else:
            _create_record(token, app_token, table_id, row)
        upserted += 1
    return preview_path, f"{upserted} rows upserted"


def bitable_app_url(config: dict[str, Any]) -> str | None:
    app_token = str(config.get("outputs", {}).get("feishu_bitable", {}).get("app_token", "")).strip()
    if not app_token:
        return None
    return f"https://bytedance.larkoffice.com/base/{app_token}"


def build_post_rows(report: Report, table_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [_build_post_row(report, post, table_cfg) for post in report.top_posts]


def _build_post_row(report: Report, post: Post, table_cfg: dict[str, Any]) -> dict[str, Any]:
    summary = _build_post_summary(post)
    return {
        _field_name(table_cfg, "post_id", "Post ID"): post.id,
        _field_name(table_cfg, "digest_date", "Digest Date"): report.window_end.date().isoformat(),
        _field_name(table_cfg, "priority", "Priority"): post.priority_label or "",
        _field_name(table_cfg, "priority_score", "Priority Score"): round(float(post.scores.get("priority", 0.0)), 4),
        _field_name(table_cfg, "original_url", "Original URL"): {"text": "查看原帖", "link": post.url} if post.url else None,
        _field_name(table_cfg, "summary", "Summary"): summary,
        _field_name(table_cfg, "published_at", "Published At"): post.created_at.isoformat(),
        _field_name(table_cfg, "topics", "Topics"): "、".join(_post_topics(post)),
    }


def _build_post_summary(post: Post) -> str:
    parts: list[str] = []
    if post.why_it_matters:
        parts.append(post.why_it_matters.strip())
    if post.summary_bullets:
        parts.extend(f"- {bullet.strip()}" for bullet in post.summary_bullets[:2] if bullet.strip())
    return "\n".join(parts).strip()


def _post_topics(post: Post) -> list[str]:
    topic_keys = [topic for topic, value in post.topic_scores.items() if value > 0]
    if topic_keys:
        return topic_labels(topic_keys)
    fallback_topics: list[str] = []
    for tag in post.tags:
        if tag.startswith(("freshness:", "signal:", "fit:", "format:", "author:")):
            continue
        fallback_topics.append(tag)
    return fallback_topics[:3]


def _find_record_id_by_post_id(
    token: str,
    app_token: str,
    table_id: str,
    table_cfg: dict[str, Any],
    post_id: str,
) -> str | None:
    post_id_field = _field_name(table_cfg, "post_id", "Post ID")
    response = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        params={"page_size": 1},
        json={
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": post_id_field,
                        "operator": "is",
                        "value": [post_id],
                    }
                ],
            }
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"Feishu bitable 查询失败: {payload}")
    items = payload.get("data", {}).get("items", [])
    if not items:
        return None
    return str(items[0].get("record_id") or "")


def _create_record(token: str, app_token: str, table_id: str, fields: dict[str, Any]) -> str:
    response = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"fields": fields},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"Feishu bitable 新增失败: {payload}")
    return str(payload.get("data", {}).get("record", {}).get("record_id", ""))


def _update_record(token: str, app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
    response = requests.put(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"fields": fields},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise ValueError(f"Feishu bitable 更新失败: {payload}")


def _field_name(table_cfg: dict[str, Any], key: str, default: str) -> str:
    return str(table_cfg.get("fields", {}).get(key, default))
