"""飞书交互：推送候选卡片 → 轮询用户回复 → 解析选择。"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any

import requests


def push_candidates_to_feishu(
    candidates: list[dict],
    feishu_config: dict,
) -> str:
    """推送候选卡片到飞书，返回 message_id。"""
    token = _get_tenant_token(feishu_config)
    receive_id = _resolve_receive_id(feishu_config)
    receive_id_type = feishu_config.get("outputs", {}).get("feishu", {}).get("receive_id_type", "open_id")

    card = _build_candidate_card(candidates)

    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(f"飞书发送失败: {data}")
    return data.get("data", {}).get("message_id", "")


def poll_user_reply(
    feishu_config: dict,
    sent_after: datetime,
    timeout_sec: int = 1800,
    poll_interval: int = 30,
    candidate_count: int = 0,
) -> dict:
    """轮询飞书消息，等待用户回复。

    返回 {"selected": [0, 2], "github_urls": {"0": "https://..."}, "raw": "原始回复"}
    超时返回 {"selected": [], "timeout": True}
    """
    token = _get_tenant_token(feishu_config)
    receive_id = _resolve_receive_id(feishu_config)
    receive_id_type = feishu_config.get("outputs", {}).get("feishu", {}).get("receive_id_type", "open_id")

    # 将 sent_after 转为 unix 时间戳（秒）
    sent_ts = str(int(sent_after.timestamp()))
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            # 刷新 token（可能过期）
            token = _get_tenant_token(feishu_config)
            # 获取最近消息
            resp = requests.get(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "container_id_type": receive_id_type.replace("open_id", "chat"),
                    "container_id": receive_id,
                    "page_size": 10,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            items = data.get("data", {}).get("items", [])
            for msg in items:
                # 检查是否在发送之后
                create_time = msg.get("create_time", "0")
                if int(create_time) <= int(sent_ts):
                    continue

                # 只处理文本消息
                msg_type = msg.get("msg_type", "")
                if msg_type != "text":
                    continue

                # 解析消息内容
                content = msg.get("body", {}).get("content", "")
                try:
                    content_obj = json.loads(content)
                    text = content_obj.get("text", "")
                except (json.JSONDecodeError, AttributeError):
                    text = str(content)

                # 检查是否是有效回复（包含数字、"全部"、"跳过"等）
                if not _is_valid_reply(text):
                    continue

                parsed = _parse_reply(text, candidate_count)
                parsed["raw"] = text
                return parsed

        except Exception:
            continue

    return {"selected": [], "timeout": True, "github_urls": {}}


def push_deploy_result(result: dict, feishu_config: dict) -> None:
    """部署完成后推送结果摘要到飞书。"""
    token = _get_tenant_token(feishu_config)
    receive_id = _resolve_receive_id(feishu_config)
    receive_id_type = feishu_config.get("outputs", {}).get("feishu", {}).get("receive_id_type", "open_id")

    status = result.get("status", "unknown")
    exp_id = result.get("experiment_id", "")
    branch = result.get("branch", "")
    github_url = result.get("github_url", "")

    if status == "deployed":
        icon = "✅"
        detail = f"分支: {branch}"
        if github_url:
            detail += f"\nGitHub: {github_url}"
    else:
        icon = "❌"
        detail = result.get("message", "部署失败")

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if status == "deployed" else "red",
            "title": {"tag": "plain_text", "content": f"{icon} 实验部署结果"},
        },
        "elements": [
            {"tag": "markdown", "content": f"**实验ID**: {exp_id}"},
            {"tag": "markdown", "content": f"**状态**: {status}"},
            {"tag": "markdown", "content": detail},
        ],
    }

    requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        timeout=30,
    )


def trigger_luna_research(
    experiment_id: str,
    github_url: str,
    post_url: str,
    author_handle: str,
    feishu_config: dict,
    luna_receive_id: str | None = None,
) -> dict:
    """通过飞书给 Luna 发送调研指令。

    Luna 是 Mac mini 上的 OpenClaw agent，装有 x-lab-research skill，
    收到消息后会自动调研指定的 GitHub 仓库。
    """
    token = _get_tenant_token(feishu_config)
    receive_id = luna_receive_id or feishu_config.get("lab", {}).get("luna_receive_id")
    if not receive_id:
        return {"status": "skip", "message": "未配置 luna_receive_id"}

    receive_id_type = feishu_config.get("lab", {}).get("luna_receive_id_type", "open_id")

    # 构造 Luna 能识别的调研指令
    message = (
        f"帮我调研一下这个项目：\n\n"
        f"GitHub: {github_url}\n"
        f"来源帖子: {post_url}\n"
        f"作者: @{author_handle}\n"
        f"实验ID: {experiment_id}\n\n"
        f"请用 x-lab-research skill 完成调研，生成 6 维度评价和报告。"
    )

    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": message}, ensure_ascii=False),
            },
            timeout=30,
        )
        resp_data = resp.json()
        if resp_data.get("code") != 0:
            return {"status": "error", "message": f"发送失败: {resp_data.get('msg', '')}"}
        return {"status": "ok", "message": "已通知 Luna 开始调研"}
    except Exception as e:
        return {"status": "error", "message": f"通知 Luna 失败: {e}"}


def _build_candidate_card(candidates: list[dict]) -> dict:
    """构建飞书候选卡片 JSON。"""
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": f"📋 **今日实验候选（{len(candidates)}条）**\n━━━━━━━━━━━━━━━━━━",
        }
    ]

    for idx, candidate in enumerate(candidates, start=1):
        post = candidate["post"]
        author = post.get("author", {}).get("handle", "unknown")
        priority = post.get("priority_label", "?")
        score = candidate.get("actionable_score", 0)
        tags = candidate.get("actionable_tags", [])
        summary = (post.get("summary_bullets") or [""])[0][:80]

        tag_str = ", ".join(tags) if tags else "无标签"
        line = (
            f"**{idx}. [{priority}级] @{author}**\n"
            f"实操分: {score} | 标签: {tag_str}\n"
            f"摘要: {summary}"
        )
        elements.append({"tag": "markdown", "content": line})

    elements.extend([
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "请回复选择序号（如 **1,3** 或 **全部** 或 **跳过**）\n"
                "如需提供 GitHub URL，格式：`1:https://github.com/xxx/yyy`"
            ),
        },
    ])

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "🧪 x-lab 实验候选"},
        },
        "elements": elements,
    }


def _parse_reply(text: str, max_index: int) -> dict:
    """解析用户回复。

    - "1,3" → selected=[0, 2]（转 0-based）
    - "全部" / "all" → selected=list(range(max_index))
    - "跳过" / "skip" → selected=[]
    - "1:https://github.com/xxx" → github_urls={"0": "https://..."}
    """
    text = text.strip()
    result: dict[str, Any] = {"selected": [], "github_urls": {}}

    # 跳过
    if text.lower() in ("跳过", "skip", "取消", "cancel"):
        return result

    # 全部
    if text.lower() in ("全部", "all", "所有"):
        result["selected"] = list(range(max_index))
        return result

    # 解析序号和 URL
    selected: set[int] = set()
    github_urls: dict[str, str] = {}

    for part in re.split(r"[,，\s]+", text):
        part = part.strip()
        if not part:
            continue

        # 格式: 1:https://github.com/xxx
        url_match = re.match(r"(\d+)\s*[:：]\s*(https?://\S+)", part)
        if url_match:
            num = int(url_match.group(1))
            url = url_match.group(2)
            idx = num - 1  # 转 0-based
            if 0 <= idx < max_index:
                selected.add(idx)
                github_urls[str(idx)] = url
            continue

        # 纯数字
        num_match = re.match(r"(\d+)", part)
        if num_match:
            num = int(num_match.group(1))
            idx = num - 1
            if 0 <= idx < max_index:
                selected.add(idx)

    result["selected"] = sorted(selected)
    result["github_urls"] = github_urls
    return result


def _is_valid_reply(text: str) -> bool:
    """判断文本是否像有效的选择回复。"""
    text = text.strip().lower()
    if text in ("跳过", "skip", "取消", "cancel", "全部", "all", "所有"):
        return True
    if re.search(r"\d", text):
        return True
    return False


def _get_tenant_token(config: dict) -> str:
    """获取飞书 tenant_access_token（内联实现）。"""
    feishu_cfg = config.get("outputs", {}).get("feishu", {})
    app_id = feishu_cfg.get("app_id") or os.getenv(feishu_cfg.get("app_id_env", ""), "")
    app_secret = feishu_cfg.get("app_secret") or os.getenv(feishu_cfg.get("app_secret_env", ""), "")
    if not app_id or not app_secret:
        raise ValueError("飞书 app_id/app_secret 未配置")

    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("tenant_access_token")
    if not token:
        raise ValueError(f"获取 tenant_access_token 失败: {resp.text}")
    return token


def _resolve_receive_id(config: dict) -> str:
    """解析飞书 receive_id。"""
    feishu_cfg = config.get("outputs", {}).get("feishu", {})
    receive_id = feishu_cfg.get("receive_id") or os.getenv(
        feishu_cfg.get("receive_id_env", ""), ""
    )
    if not receive_id:
        raise ValueError("飞书 receive_id 未配置")
    return receive_id
