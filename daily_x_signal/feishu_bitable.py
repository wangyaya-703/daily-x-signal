from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from .models import Report
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

    fields = _build_fields(report, table_cfg)
    save_json(preview_path, fields)

    token = get_token(config)
    existing = _find_record(token, app_token, table_id, table_cfg, report.window_end.date().isoformat())
    if existing:
        _update_record(token, app_token, table_id, existing, fields)
        return preview_path, existing

    record_id = _create_record(token, app_token, table_id, fields)
    return preview_path, record_id


def _build_fields(report: Report, table_cfg: dict[str, Any]) -> dict[str, Any]:
    fields_map = table_cfg.get("fields", {})
    top_posts = "\n".join(
        f"{idx}. @{post.author.handle} | {post.why_it_matters} | {post.url}"
        for idx, post in enumerate(report.top_posts[:5], start=1)
    )
    overview = "\n".join(f"- {bullet}" for bullet in report.overview_bullets[:4])
    stats = report.section_stats or {}
    must_read = report.must_read
    return {
        fields_map.get("digest_date", "Digest Date"): report.window_end.date().isoformat(),
        fields_map.get("generated_at", "Generated At"): report.generated_at.isoformat(),
        fields_map.get("window", "Window"): f"{report.window_start.isoformat()} -> {report.window_end.isoformat()}",
        fields_map.get("must_read", "Must Read"): f"@{must_read.author.handle} {must_read.why_it_matters}" if must_read else "",
        fields_map.get("must_read_url", "Must Read URL"): (
            {"text": "查看原帖", "link": must_read.url} if must_read and must_read.url else None
        ),
        fields_map.get("overview", "Overview"): overview,
        fields_map.get("top_posts", "Top Posts"): top_posts,
        fields_map.get("candidate_count", "Candidate Count"): report.metadata.get("candidate_count", 0),
        fields_map.get("author_count", "Author Count"): report.metadata.get("author_count", 0),
        fields_map.get("top_topics", "Top Topics"): "、".join(stats.get("top_topics", [])),
        fields_map.get("high_fit_posts", "High Fit Posts"): stats.get("high_fit_posts", 0),
        fields_map.get("following_status", "Following Status"): report.metadata.get("following_status", {}).get("reason", ""),
    }


def _find_record(
    token: str,
    app_token: str,
    table_id: str,
    table_cfg: dict[str, Any],
    digest_date: str,
) -> str | None:
    digest_field = table_cfg.get("fields", {}).get("digest_date", "Digest Date")
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
                        "field_name": digest_field,
                        "operator": "is",
                        "value": [digest_date],
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
    return items[0].get("record_id")


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
