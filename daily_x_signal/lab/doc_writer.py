"""实验记录写入：Mac mini EXPERIMENTS.md + 飞书云文档。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


def fetch_github_code_quality(github_url: str) -> dict:
    """从 GitHub API 获取代码质量指标。

    返回 {stars, last_commit_days_ago, has_readme, license}。
    失败时返回字段值为 None。
    """
    empty = {"stars": None, "last_commit_days_ago": None, "has_readme": None, "license": None}
    if not github_url:
        return empty
    try:
        # 从 URL 提取 owner/repo
        parts = github_url.rstrip("/").split("/")
        if len(parts) < 2:
            return empty
        owner, repo = parts[-2], parts[-1]

        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        if resp.status_code != 200:
            return empty

        data = resp.json()
        stars = data.get("stargazers_count")
        pushed_at = data.get("pushed_at")
        last_commit_days = None
        if pushed_at:
            pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            last_commit_days = (datetime.now(timezone.utc) - pushed_dt).days
        license_info = data.get("license")
        license_name = license_info.get("spdx_id") if isinstance(license_info, dict) else None

        return {
            "stars": stars,
            "last_commit_days_ago": last_commit_days,
            "has_readme": True,  # GitHub API 默认包含 README 检测
            "license": license_name,
        }
    except Exception:
        return empty


def _format_code_quality(cq: dict) -> str:
    """格式化代码质量为可读字符串。"""
    parts = []
    if cq.get("stars") is not None:
        parts.append(f"⭐ {cq['stars']} stars")
    if cq.get("last_commit_days_ago") is not None:
        parts.append(f"最近提交: {cq['last_commit_days_ago']}天前")
    if cq.get("license"):
        parts.append(cq["license"])
    return " | ".join(parts) if parts else "-"


def _format_experiments_md_row(experiment: dict) -> str:
    """生成 EXPERIMENTS.md 表格行。"""
    exp_id = experiment.get("experiment_id", "")
    date = experiment.get("brief_date", "")
    post_url = experiment.get("post_url", "")
    author = experiment.get("author_handle", "")
    github_url = experiment.get("github_url", "")
    install = experiment.get("install_success")
    cq = experiment.get("code_quality", {})

    # 格式化各字段
    author_link = f"@{author}" if author else "-"
    github_link = f"[{github_url.split('/')[-1]}]({github_url})" if github_url else "-"
    install_icon = "✅" if install is True else ("❌" if install is False else "-")
    cq_str = _format_code_quality(cq) if cq else "-"

    return f"| {date} | {exp_id} | {author_link} | {github_link} | {install_icon} | - | {cq_str} | - | - | - |"


def update_experiments_md_remote(runner: "SSHRunner", experiment: dict) -> dict:
    """通过 SSH 将新行追加到 Mac mini 的 EXPERIMENTS.md。"""
    row = _format_experiments_md_row(experiment)
    # 追加到文件末尾
    exp_id = experiment.get("experiment_id", "")
    code, out, err = runner.run(
        f"echo '{row}' >> EXPERIMENTS.md && git add EXPERIMENTS.md && "
        f"git commit -m 'doc: 更新实验记录 {exp_id}' --allow-empty"
    )
    if code != 0:
        return {"status": "error", "message": f"更新 EXPERIMENTS.md 失败: {err}"}
    return {"status": "ok", "message": "EXPERIMENTS.md 已更新"}


def _build_feishu_doc_content(experiment: dict) -> str:
    """构建要追加到飞书文档的 Markdown 内容。"""
    exp_id = experiment.get("experiment_id", "")
    post_url = experiment.get("post_url", "")
    author = experiment.get("author_handle", "")
    github_url = experiment.get("github_url", "")
    install = experiment.get("install_success")
    cq = experiment.get("code_quality", {})
    summary = experiment.get("summary_bullets", [])

    install_str = "✅" if install is True else ("❌" if install is False else "待执行")
    cq_str = _format_code_quality(cq)
    summary_str = "\n".join(f"  - {b}" for b in summary[:3]) if summary else "（无摘要）"

    lines = [
        f"## {experiment.get('brief_date', '')} | {exp_id}",
        f"- **来源帖子**：[@{author}]({post_url})",
        f"- **GitHub**：{github_url or '未提供'}",
        f"- **安装成功**：{install_str}",
        f"- **代码质量**：{cq_str}",
        f"- **摘要**：",
        summary_str,
        f"- **帖子一致性**：（待填写 1-5分）",
        f"- **个人评价**：（待填写）",
        f"- **日常使用场景**：（待填写：哪些工作/生活场景可用？）",
        f"- **Agent启发**：（待填写：对使用Agent工作的启发？）",
        "",
    ]
    return "\n".join(lines)


def sync_to_feishu_doc(experiment: dict, feishu_config: dict) -> dict:
    """将实验记录追加到飞书云文档。

    使用飞书文档 API 的 raw_content 追加模式。
    """
    doc_token = feishu_config.get("lab", {}).get("feishu_doc_token")
    if not doc_token:
        return {"status": "skip", "message": "未配置 feishu_doc_token"}

    try:
        token = _get_tenant_token(feishu_config)
        content = _build_feishu_doc_content(experiment)

        # 使用飞书文档 API 创建 block（追加内容）
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/blocks/"
            f"{doc_token}/children",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "children": [
                    {
                        "block_type": 3,  # heading2
                        "heading2": {
                            "elements": [
                                {
                                    "text_run": {
                                        "content": f"{experiment.get('brief_date', '')} | {experiment.get('experiment_id', '')}",
                                    }
                                }
                            ],
                        },
                    },
                    {
                        "block_type": 2,  # text
                        "text": {
                            "elements": [
                                {
                                    "text_run": {
                                        "content": content,
                                    }
                                }
                            ],
                        },
                    },
                ],
                "index": -1,  # 追加到末尾
            },
            timeout=30,
        )
        resp_data = resp.json()
        if resp_data.get("code") != 0:
            return {"status": "error", "message": f"飞书文档 API 失败: {resp_data.get('msg', '')}"}
        return {"status": "ok", "message": "飞书文档已更新"}
    except Exception as e:
        return {"status": "error", "message": f"飞书文档同步失败: {e}"}


def _get_tenant_token(feishu_config: dict) -> str:
    """获取飞书 tenant_access_token（内联实现，不跨模块引用私有函数）。"""
    import os

    feishu_cfg = feishu_config.get("outputs", {}).get("feishu", {})
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
