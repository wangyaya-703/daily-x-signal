"""SSH 部署执行器：通过 subprocess + ssh 在 Mac mini 上操作 x-lab 仓库。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from typing import Any


class SSHRunner:
    """通过 SSH 在远端执行命令。"""

    def __init__(self, host: str, remote_base: str, timeout: int = 60):
        self.host = host
        self.remote_base = remote_base
        self.timeout = timeout

    def run(self, cmd: str, workdir: str | None = None) -> tuple[int, str, str]:
        """在远端执行命令，返回 (returncode, stdout, stderr)。"""
        cd_part = f"cd '{workdir}'" if workdir else f"cd '{self.remote_base}'"
        full_cmd = f"export LANG=zh_CN.UTF-8 && {cd_part} && {cmd}"
        result = subprocess.run(
            ["ssh", self.host, full_cmd],
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        return result.returncode, result.stdout, result.stderr

    def test_connection(self) -> bool:
        """测试 SSH 连通性。"""
        try:
            code, out, _ = self.run("echo ok", workdir="/tmp")
            return code == 0 and "ok" in out
        except (subprocess.TimeoutExpired, OSError):
            return False

    def file_exists(self, remote_path: str) -> bool:
        """检查远端文件是否存在。"""
        code, _, _ = self.run(f"test -e '{remote_path}'", workdir="/tmp")
        return code == 0


def init_lab_repo(runner: SSHRunner) -> dict:
    """幂等初始化远端 x-lab 仓库。已存在则跳过。"""
    # 检查仓库是否已存在
    code, _, _ = runner.run("git rev-parse --is-inside-work-tree 2>/dev/null")
    if code == 0:
        return {"status": "exists", "message": "x-lab 仓库已存在"}

    # 创建目录并初始化 git
    base = runner.remote_base
    commands = [
        f"mkdir -p '{base}/experiments'",
        f"cd '{base}' && git init",
        f"cat > '{base}/README.md' << 'HEREDOC'\n# x-lab\n\n实操实验仓库：从 X 日报识别的实操帖子部署到这里进行实验。\nHEREDOC",
        f"cat > '{base}/.gitignore' << 'HEREDOC'\n.DS_Store\n*.log\nnode_modules/\nvenv/\n__pycache__/\nHEREDOC",
        f"cat > '{base}/EXPERIMENTS.md' << 'HEREDOC'\n# 实验记录\n\n| 日期 | 实验ID | 来源帖子 | GitHub | 安装成功 | 帖子一致性 | 代码质量 | 个人评价 | 日常使用场景 | Agent启发 |\n|------|--------|---------|--------|---------|-----------|---------|---------|------------|----------|\nHEREDOC",
        f"cd '{base}' && git add -A && git commit -m 'init: x-lab 实验仓库'",
    ]
    for cmd in commands:
        code, out, err = runner.run(cmd, workdir="/tmp")
        if code != 0:
            return {"status": "error", "message": f"初始化失败: {err}", "cmd": cmd}

    return {"status": "created", "message": "x-lab 仓库初始化完成"}


def create_experiment_branch(runner: SSHRunner, experiment_id: str) -> dict:
    """创建实验分支。"""
    branch = f"exp/{experiment_id}"
    # 确保在 main 分支上
    runner.run("git checkout main 2>/dev/null || git checkout -b main")
    code, out, err = runner.run(f"git checkout -b '{branch}'")
    if code != 0:
        # 分支可能已存在
        code2, _, _ = runner.run(f"git checkout '{branch}'")
        if code2 != 0:
            return {"status": "error", "message": f"创建分支失败: {err}"}
        return {"status": "exists", "branch": branch}
    return {"status": "created", "branch": branch}


def deploy_experiment(
    runner: SSHRunner,
    experiment_id: str,
    selected_candidate: dict,
    github_url: str | None = None,
    auto_run: bool = False,
) -> dict:
    """部署一个实验到远端。

    步骤：
    1. 创建实验分支
    2. 创建 experiments/{id}/ 目录
    3. 写入 experiment.json + setup.sh
    4. git add + commit
    5. 若 auto_run=True，执行 setup.sh
    """
    post = selected_candidate["post"]
    post_url = post.get("url", "")
    author_handle = post.get("author", {}).get("handle", "unknown")
    branch_result = create_experiment_branch(runner, experiment_id)
    if branch_result.get("status") == "error":
        return branch_result

    branch = branch_result["branch"]
    exp_dir = f"experiments/{experiment_id}"

    # 创建实验目录
    runner.run(f"mkdir -p '{exp_dir}'")

    # 写入 experiment.json
    exp_json = _make_experiment_json(selected_candidate, branch, github_url)
    exp_json_str = json.dumps(exp_json, ensure_ascii=False, indent=2)
    # 用 python 写文件避免 heredoc 转义问题
    runner.run(
        f"python3 -c \"import sys; open('{exp_dir}/experiment.json','w').write(sys.stdin.read())\" "
        f"<< 'EXPJSONEOF'\n{exp_json_str}\nEXPJSONEOF"
    )

    # 写入 setup.sh
    setup_sh = _make_setup_sh(github_url, post_url)
    runner.run(
        f"python3 -c \"import sys; open('{exp_dir}/setup.sh','w').write(sys.stdin.read())\" "
        f"<< 'SETUPEOF'\n{setup_sh}\nSETUPEOF"
    )
    runner.run(f"chmod +x '{exp_dir}/setup.sh'")

    # 写入 notes.md
    notes_content = f"# {experiment_id} 实验笔记\\n\\n## 个人评价\\n\\n\\n## 日常使用场景\\n\\n\\n## Agent 启发\\n\\n"
    runner.run(f"printf '{notes_content}' > '{exp_dir}/notes.md'")

    # git add + commit
    code, out, err = runner.run(
        f"git add '{exp_dir}' && git commit -m 'exp: 部署实验 {experiment_id}'"
    )
    if code != 0:
        return {"status": "error", "message": f"git commit 失败: {err}"}

    result = {
        "status": "deployed",
        "experiment_id": experiment_id,
        "branch": branch,
        "post_url": post_url,
        "github_url": github_url,
        "author_handle": author_handle,
    }

    # 自动执行 setup.sh
    if auto_run and github_url:
        code, out, err = runner.run(
            f"cd '{exp_dir}' && bash setup.sh > run.log 2>&1"
        )
        runner.run(f"git add '{exp_dir}/run.log' && git commit -m 'exp: {experiment_id} 执行日志' --allow-empty")
        result["auto_run"] = True
        result["install_success"] = code == 0
        result["run_log_tail"] = out[-500:] if out else err[-500:]
    else:
        result["auto_run"] = False
        result["install_success"] = None

    # 切回 main
    runner.run("git checkout main")

    return result


def rollback_experiment(runner: SSHRunner, branch: str) -> dict:
    """回滚实验：切回 main 分支，但保留实验分支供查阅。"""
    if not branch.startswith("exp/"):
        branch = f"exp/{branch}"
    code, out, err = runner.run("git checkout main")
    if code != 0:
        return {"status": "error", "message": f"回滚失败: {err}"}
    return {"status": "rolled_back", "branch": branch, "message": "已切回 main，实验分支保留"}


def list_experiments(runner: SSHRunner) -> list[dict]:
    """列出远端所有实验分支。"""
    code, out, _ = runner.run("git branch -a")
    if code != 0:
        return []
    branches = []
    for line in out.strip().splitlines():
        line = line.strip().lstrip("* ")
        if line.startswith("exp/"):
            branches.append({"branch": line, "experiment_id": line[4:]})
    return branches


def _make_experiment_id(post: dict) -> str:
    """生成实验 ID：YYYY-MM-DD-{author_handle}-{slug}。"""
    today = datetime.now().strftime("%Y-%m-%d")
    handle = post.get("author", {}).get("handle", "unknown")
    # 从帖子文本提取 slug（前 20 个字母数字字符）
    text = post.get("text", "")
    slug_chars = re.sub(r"[^a-zA-Z0-9]", "", text)[:20]
    if not slug_chars:
        slug_chars = post.get("id", "unknown")[:12]
    # 转为更短的连字符格式
    slug = slug_chars.lower()[:16]
    return f"{today}-{handle}-{slug}"


def _make_setup_sh(github_url: str | None, post_url: str) -> str:
    """生成 setup.sh 脚本内容。"""
    lines = [
        "#!/bin/bash",
        "set -e",
        "",
        f'echo "来源帖子: {post_url}"',
        f'echo "部署时间: $(date)"',
        "",
    ]
    if github_url:
        # 从 URL 提取 repo 名
        parts = github_url.rstrip("/").split("/")
        repo_name = parts[-1] if parts else "repo"
        lines.extend([
            f'echo "克隆仓库: {github_url}"',
            f"git clone {github_url} {repo_name}",
            f'echo "克隆完成: {repo_name}"',
            "",
            "# 以下步骤请根据项目 README 手动添加",
            f"# cd {repo_name}",
            "# pip install -r requirements.txt  # 或其他安装命令",
        ])
    else:
        lines.extend([
            "echo '未提供 GitHub URL，请手动设置实验环境'",
            "echo '可以编辑此脚本添加具体步骤'",
        ])
    return "\n".join(lines) + "\n"


def _make_experiment_json(
    candidate: dict,
    branch: str,
    github_url: str | None,
) -> dict:
    """生成 experiment.json 内容（含6维度字段）。"""
    post = candidate["post"]
    return {
        "experiment_id": "",  # 由调用方填充
        "status": "deployed",
        "brief_date": datetime.now().strftime("%Y-%m-%d"),
        "post_url": post.get("url", ""),
        "github_url": github_url,
        "branch": branch,
        "deployed_at": datetime.now().isoformat(),
        "author_handle": post.get("author", {}).get("handle", ""),
        "actionable_score": candidate.get("actionable_score", 0),
        "reason": candidate.get("reason", ""),
        "summary_bullets": post.get("summary_bullets", []),
        "dimensions": {
            "install_success": None,
            "consistency_with_post": None,
            "code_quality": {
                "stars": None,
                "last_commit_days_ago": None,
                "has_readme": None,
                "license": None,
            },
            "personal_verdict": None,
            "daily_usecase": None,
            "agent_insight": None,
        },
    }
