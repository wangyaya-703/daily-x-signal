from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .console import bool_text, print_section, render_table
from .collector import (
    authors_from_cache,
    collect_home_candidates,
    collect_posts_for_authors,
    dedupe_posts,
    hydrate_threads,
    limit_posts_per_author,
    prioritize_authors,
    sync_following,
)
from .core_authors import author_stats_from_history, build_core_pool, load_history, save_history, update_history
from .feishu import deliver_feishu, deliver_feishu_bitable
from .feishu_bitable import bitable_app_url
from .github_fallback import sync_success_marker
from .llm import LLMClient, apply_llm_summary, extract_llm_overview, extract_llm_watchlist
from .models import Report
from .personalization import build_interest_profile
from .report import build_report_overview, fallback_enrich, write_outputs
from .scheduler import load_scheduler_state, record_scheduler_result, should_run_scheduler
from .scoring import rank_posts, suggested_authors
from .skill_sync import inspect_skill_sync
from .setup_wizard import run_setup
from .store import load_json, save_json
from .window import resolve_window
from .x_client import XReachClient

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = _PROJECT_ROOT / "config" / "default.yaml"
LOCAL_CONFIG_PATH = _PROJECT_ROOT / "config" / "local.yaml"
REPO_SKILL_PATH = _PROJECT_ROOT / "skills" / "daily-x-signal" / "SKILL.md"
OPENCLAW_SKILL_PATH = Path.home() / ".openclaw" / "skills" / "daily-x-signal" / "SKILL.md"
STATE_PATH = _PROJECT_ROOT / "state" / "history.json"
FOLLOWING_CACHE_PATH = _PROJECT_ROOT / "state" / "following_cache.json"
CORE_POOL_PATH = _PROJECT_ROOT / "state" / "core_authors.json"
SCHEDULER_STATE_PATH = _PROJECT_ROOT / "state" / "scheduler_state.json"
LAB_STATE_PATH = _PROJECT_ROOT / "state" / "lab_experiments.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-x-signal")
    parser.add_argument("command", choices=["generate", "sync-authors", "show-core-authors", "schedule-tick", "setup", "lab"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--override-config")
    parser.add_argument("--window-mode", choices=["scheduled", "rolling_24h"], default="scheduled")
    parser.add_argument("--mode", choices=["all_following", "core_authors"], default=None)
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser


def build_client(config: dict | None = None) -> XReachClient:
    proxy_url = ""
    if config:
        proxy_url = str(config.get("x", {}).get("proxy_url") or "").strip()
    return XReachClient(workdir=Path.cwd(), proxy=proxy_url or None)


def cmd_sync_authors(config: dict, client: XReachClient) -> int:
    sync_result = sync_following(client, config)
    authors = sync_result["authors"]
    payload = {
        "refreshed_at": datetime.now().isoformat(),
        "status": sync_result["status"],
        "authors": [author.raw for author in authors],
    }
    save_json(FOLLOWING_CACHE_PATH, payload)
    print_section("following 同步结果")
    print(
        render_table(
            ["Field", "Value"],
            [
                ["refreshed_at", payload["refreshed_at"]],
                ["synced_count", sync_result["status"].get("synced_count", 0)],
                ["expected_following_count", sync_result["status"].get("expected_following_count", "")],
                ["completion_ratio", sync_result["status"].get("completion_ratio", "")],
                ["is_complete", bool_text(bool(sync_result["status"].get("is_complete", False)))],
                ["needs_confirmation", bool_text(bool(sync_result["status"].get("needs_confirmation", False)))],
                ["reason", sync_result["status"].get("reason", "")],
            ],
        )
    )
    if authors:
        print_section("作者样本")
        print(
            render_table(
                ["Handle", "Name", "Followers"],
                [[author.handle, author.name, author.followers_count] for author in authors[:10]],
            )
        )
    return 0


def cmd_show_core_authors(config: dict) -> int:
    history = load_history(STATE_PATH)
    pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": datetime.now().isoformat(), "authors": pool})
    print(json.dumps(pool, ensure_ascii=False, indent=2))
    return 0


def generate_digest(args: argparse.Namespace, config: dict, client: XReachClient) -> dict[str, object]:
    history = load_history(STATE_PATH)
    author_stats = author_stats_from_history(history)
    following_cache = load_json(FOLLOWING_CACHE_PATH, {"authors": []})
    timezone_name = config["profile"]["timezone"]
    window = resolve_window(args.window_mode, timezone_name)
    mode = args.mode or config["profile"].get("default_mode", "all_following")

    home_candidates = []
    if config["x"].get("fallback_to_home_timeline", True):
        try:
            home_candidates = collect_home_candidates(client, window)
        except Exception:
            home_candidates = []

    sync_result = sync_following(client, config)
    authors = sync_result["authors"]
    following_status = sync_result["status"]
    following_cache_used = False
    if authors:
        save_json(
            FOLLOWING_CACHE_PATH,
            {
                "refreshed_at": datetime.now().isoformat(),
                "status": following_status,
                "authors": [author.raw for author in authors],
            },
        )
    else:
        authors = authors_from_cache(following_cache, int(config["x"].get("max_authors_per_run", 0) or 0) or len(following_cache.get("authors", [])))
        following_cache_used = bool(authors)
        following_status = following_cache.get("status", following_status)

    if following_status.get("needs_confirmation") and bool(config["x"].get("require_following_confirmation", True)):
        print(f"[warning] {following_status.get('reason')}")

    candidate_posts = list(home_candidates)
    if mode == "core_authors":
        pool = build_core_pool(history, config)
        core_handles = {item["handle"] for item in pool}
        authors = [author for author in authors if author.handle in core_handles]

    interest_profile = build_interest_profile(config, authors, history)
    prioritized_authors = prioritize_authors(
        authors,
        home_candidates,
        int(config["x"].get("max_active_authors_per_run", len(authors) or 0)),
    )
    if prioritized_authors:
        candidate_posts.extend(collect_posts_for_authors(client, prioritized_authors, config, window))
    candidate_posts = dedupe_posts(candidate_posts, bool(config["x"].get("dedupe_by_conversation", True)))
    ranked = rank_posts(candidate_posts, config, author_stats, interest_profile=interest_profile)
    ranked = limit_posts_per_author(ranked, int(config["ranking"].get("max_posts_per_author", 2)))
    top_n = args.top_n or int(config["profile"].get("digest_top_n", 10))
    hydrate_threads(client, ranked, int(config["x"].get("thread_fetch_top_n", 12)))

    llm_client = LLMClient(config)
    llm_payload = llm_client.summarize_posts(
        ranked[: int(config["llm"].get("max_input_posts", 12))],
        interest_profile=interest_profile,
    )
    must_read_id = apply_llm_summary(ranked, llm_payload)
    llm_watchlist = extract_llm_watchlist(llm_payload)
    llm_overview = extract_llm_overview(llm_payload)
    fallback_enrich(ranked[:top_n])

    top_posts = ranked[:top_n]
    must_read = next((post for post in top_posts if post.id == must_read_id), top_posts[0] if top_posts else None)
    watchlist = merge_watchlists(suggested_authors(top_posts, author_stats), llm_watchlist)
    overview_bullets, section_stats = build_report_overview(top_posts, interest_profile, llm_overview, following_status)
    report = Report(
        generated_at=datetime.now(window.end.tzinfo),
        window_start=window.start,
        window_end=window.end,
        mode=mode,
        top_posts=top_posts,
        must_read=must_read,
        watchlist_authors=watchlist,
        overview_bullets=overview_bullets,
        section_stats=section_stats,
        metadata={
            "candidate_count": len(candidate_posts),
            "author_count": len(prioritized_authors or authors),
            "following_cache_used": following_cache_used,
            "following_status": following_status,
            "interest_profile": interest_profile,
            "llm_enabled": llm_client.is_enabled(),
        },
    )

    if args.dry_run:
        return {
            "dry_run": True,
            "top_post_urls": [post.url for post in top_posts],
            "must_read_url": must_read.url if must_read else None,
            "report": report,
        }

    md_path, json_path = write_outputs(report, config)
    history = update_history(history, top_posts, report.generated_at)
    save_history(STATE_PATH, history)
    core_pool = build_core_pool(history, config)
    save_json(CORE_POOL_PATH, {"generated_at": report.generated_at.isoformat(), "authors": core_pool})
    feishu_preview_path, feishu_status = deliver_feishu(report, config)
    bitable_preview_path, bitable_record_id = deliver_feishu_bitable(report, config)
    return {
        "report": report,
        "md_path": md_path,
        "json_path": json_path,
        "feishu_preview_path": feishu_preview_path,
        "feishu_status": feishu_status,
        "bitable_preview_path": bitable_preview_path,
        "bitable_record_id": bitable_record_id,
        "core_pool_size": len(core_pool),
    }


def cmd_generate(args: argparse.Namespace, config: dict, client: XReachClient) -> int:
    result = generate_digest(args, config, client)
    if result.get("dry_run"):
        print(
            json.dumps(
                {
                    "top_post_urls": result.get("top_post_urls", []),
                    "must_read": result.get("must_read_url"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return print_generate_result(result, config)


def print_generate_result(result: dict[str, object], config: dict) -> int:

    md_path = result.get("md_path")
    json_path = result.get("json_path")
    feishu_preview_path = result.get("feishu_preview_path")
    feishu_status = result.get("feishu_status")
    bitable_preview_path = result.get("bitable_preview_path")
    bitable_record_id = result.get("bitable_record_id")
    print_section("生成结果")
    print(
        render_table(
            ["Item", "Value"],
            [
                ["Markdown", str(md_path or "")],
                ["JSON", str(json_path or "")],
                ["Core author pool size", result.get("core_pool_size", 0)],
            ],
        )
    )
    print_section("投递状态")
    print(
        render_table(
            ["Channel", "Enabled", "Result", "Detail"],
            [
                [
                    "Feishu Card",
                    bool_text(bool(config.get("outputs", {}).get("feishu", {}).get("enabled", False))),
                    "OK" if feishu_status == 200 else ("SKIP" if feishu_preview_path and not feishu_status else "WARN"),
                    str(feishu_preview_path or feishu_status or "未启用"),
                ],
                [
                    "Feishu Bitable",
                    bool_text(bool(config.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False))),
                    "OK" if bitable_record_id else ("SKIP" if bitable_preview_path else "WARN"),
                    str(bitable_record_id or bitable_preview_path or "未启用"),
                ],
            ],
        )
    )
    if config.get("outputs", {}).get("feishu_bitable", {}).get("enabled", False):
        print_section("帖子追踪表")
        print(
            render_table(
                ["Field", "Value"],
                [
                    ["Open URL", bitable_app_url(config) or ""],
                    ["Write Model", "按 Post ID upsert，1 个帖子 1 行"],
                    ["Tracked Fields", "Priority / Priority Score / Original URL / Summary / Published At / Topics"],
                ],
            )
        )
    return 0


def cmd_schedule_tick(args: argparse.Namespace, config: dict, client: XReachClient) -> int:
    scheduler_state = load_scheduler_state(SCHEDULER_STATE_PATH)
    decision = should_run_scheduler(config, scheduler_state)
    if not decision["should_run"] and not args.force:
        print(decision["reason"])
        return 0

    scheduled_args = argparse.Namespace(
        command="generate",
        config=args.config,
        override_config=args.override_config,
        window_mode="scheduled",
        mode=args.mode,
        top_n=args.top_n,
        dry_run=False,
        force=args.force,
    )
    try:
        result = generate_digest(scheduled_args, config, client)
        report = result["report"]
        feishu_enabled = bool(config["outputs"]["feishu"].get("enabled", False))
        feishu_status = result.get("feishu_status")
        success = bool(result.get("md_path")) and (not feishu_enabled or feishu_status == 200)
        record_scheduler_result(
            SCHEDULER_STATE_PATH,
            digest_date=report.window_end.date().isoformat(),
            reason=str(decision["reason"]),
            success=success,
            feishu_status=feishu_status if isinstance(feishu_status, int) else None,
            metadata={
                "window_end": report.window_end.isoformat(),
                "markdown_path": str(result.get("md_path") or ""),
                "json_path": str(result.get("json_path") or ""),
            },
        )
        if not success:
            raise RuntimeError("日报已生成，但飞书发送未成功。")
        marker_result = sync_success_marker(
            config,
            digest_date=report.window_end.date().isoformat(),
            sent_at=report.generated_at.isoformat(),
        )
        print(f"Scheduled digest sent for {report.window_end.date().isoformat()}")
        print(f"Feishu status: {feishu_status}")
        print(f"GitHub fallback marker: {marker_result['reason']}")
        return 0
    except Exception as exc:
        latest_state = load_scheduler_state(SCHEDULER_STATE_PATH)
        sent_dates = latest_state.get("sent_dates", {})
        if str(decision["digest_date"]) in sent_dates:
            raise
        record_scheduler_result(
            SCHEDULER_STATE_PATH,
            digest_date=str(decision["digest_date"]),
            reason=str(decision["reason"]),
            success=False,
            metadata={"error": str(exc)},
        )
        raise


def build_lab_parser() -> argparse.ArgumentParser:
    """解析 lab 子命令的参数。"""
    parser = argparse.ArgumentParser(prog="daily-x-signal lab")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="扫描最新日报 → 推飞书 → 等回复 → 部署")
    group.add_argument("--list", action="store_true", help="列出历史实验")
    group.add_argument("--rollback", metavar="EXP_ID", help="回滚指定实验")
    group.add_argument("--init-repo", action="store_true", help="初始化远端 x-lab 仓库")
    parser.add_argument("--brief", metavar="PATH", help="指定 brief JSON 文件路径")
    parser.add_argument("--auto-run", action="store_true", help="部署后自动执行 setup.sh")
    return parser


def cmd_lab(config: dict, lab_args: argparse.Namespace) -> int:
    """执行 lab 子命令。"""
    import sys
    from .lab.scanner import find_latest_brief, load_brief, scan_for_candidates
    from .lab.state import load_lab_state, record_experiment, list_experiments_local
    from .lab.deployer import (
        SSHRunner, init_lab_repo, deploy_experiment, rollback_experiment,
        list_experiments, _make_experiment_id,
    )
    from .lab.doc_writer import fetch_github_code_quality, update_experiments_md_remote, sync_to_feishu_doc
    from .lab.feishu_selector import push_candidates_to_feishu, poll_user_reply, push_deploy_result, trigger_luna_research

    lab_cfg = config.get("lab", {})
    if not lab_cfg.get("enabled", False):
        print("lab 功能未启用。请在 config/local.yaml 中设置 lab.enabled: true")
        return 1

    ssh_host = lab_cfg.get("ssh_host", "mac-mini")
    remote_base = lab_cfg.get("remote_base", "~/工作/x-lab")
    ssh_timeout = int(lab_cfg.get("ssh_timeout_sec", 60))
    runner = SSHRunner(ssh_host, remote_base, ssh_timeout)

    # --init-repo
    if lab_args.init_repo:
        print("测试 SSH 连接...")
        if not runner.test_connection():
            print(f"SSH 连接失败: {ssh_host}")
            return 1
        print(f"SSH 连接成功: {ssh_host}")
        result = init_lab_repo(runner)
        print(f"仓库状态: {result['status']} - {result['message']}")
        return 0 if result["status"] != "error" else 1

    # --list
    if lab_args.list:
        experiments = list_experiments_local(LAB_STATE_PATH)
        if not experiments:
            print("暂无实验记录")
            return 0
        print_section("实验历史")
        print(
            render_table(
                ["实验ID", "状态", "作者", "分支", "部署时间"],
                [
                    [
                        e.get("experiment_id", ""),
                        e.get("status", ""),
                        e.get("author_handle", ""),
                        e.get("branch", ""),
                        e.get("deployed_at", "")[:19],
                    ]
                    for e in experiments
                ],
            )
        )
        return 0

    # --rollback
    if lab_args.rollback:
        print("测试 SSH 连接...")
        if not runner.test_connection():
            print(f"SSH 连接失败: {ssh_host}")
            return 1
        result = rollback_experiment(runner, lab_args.rollback)
        print(f"回滚结果: {result['status']} - {result.get('message', '')}")
        return 0 if result["status"] != "error" else 1

    # --scan
    if lab_args.scan:
        # 1. 加载 brief
        output_dir = Path(config.get("outputs", {}).get("local", {}).get("directory", "output"))
        if not output_dir.is_absolute():
            output_dir = _PROJECT_ROOT / output_dir
        if lab_args.brief:
            brief_path = Path(lab_args.brief)
        else:
            brief_path = find_latest_brief(output_dir)

        if not brief_path or not brief_path.exists():
            print("未找到日报 brief 文件。请先运行 generate 或用 --brief 指定路径")
            return 1

        print(f"加载日报: {brief_path.name}")
        brief = load_brief(brief_path)

        # 2. 扫描候选
        min_score = int(lab_cfg.get("min_actionable_score", 2))
        candidates = scan_for_candidates(brief)
        max_display = int(lab_cfg.get("max_candidates_display", 10))
        candidates = candidates[:max_display]

        if not candidates:
            print("本期日报未发现实操候选帖子")
            return 0

        print(f"\n找到 {len(candidates)} 个实操候选:")
        for idx, c in enumerate(candidates, 1):
            post = c["post"]
            handle = post.get("author", {}).get("handle", "?")
            priority = post.get("priority_label", "?")
            print(f"  {idx}. [{priority}级] @{handle} (实操分:{c['actionable_score']}) - {c['reason'][:60]}")

        # 3. 推送到飞书
        feishu_enabled = config.get("outputs", {}).get("feishu", {}).get("enabled", False)
        if not feishu_enabled:
            print("\n飞书未启用，进入本地交互模式")
            user_input = input("请输入选择（如 1,3 或 全部 或 跳过）: ").strip()
            if not user_input or user_input in ("跳过", "skip"):
                print("已跳过")
                return 0

            from .lab.feishu_selector import _parse_reply
            parsed = _parse_reply(user_input, len(candidates))
            selected_indices = parsed["selected"]
            github_urls = parsed.get("github_urls", {})
        else:
            print("\n推送候选到飞书...")
            try:
                msg_id = push_candidates_to_feishu(candidates, config)
                print(f"已推送 (message_id: {msg_id})")
            except Exception as e:
                print(f"飞书推送失败: {e}")
                return 1

            # 4. 轮询回复
            poll_timeout = int(lab_cfg.get("feishu_poll_timeout_sec", 1800))
            poll_interval = int(lab_cfg.get("feishu_poll_interval_sec", 30))
            print(f"等待飞书回复（超时 {poll_timeout // 60} 分钟）...")
            reply = poll_user_reply(
                config,
                sent_after=datetime.now(),
                timeout_sec=poll_timeout,
                poll_interval=poll_interval,
                candidate_count=len(candidates),
            )
            if reply.get("timeout"):
                print("等待超时，自动跳过")
                return 0
            selected_indices = reply.get("selected", [])
            github_urls = reply.get("github_urls", {})
            print(f"用户选择: {reply.get('raw', '')}")

        if not selected_indices:
            print("未选择任何实验")
            return 0

        # 5. SSH 连接测试
        print("\n测试 SSH 连接...")
        if not runner.test_connection():
            print(f"SSH 连接失败: {ssh_host}")
            return 1
        print(f"SSH 连接成功: {ssh_host}")

        # 确保仓库已初始化
        init_result = init_lab_repo(runner)
        if init_result["status"] == "error":
            print(f"仓库初始化失败: {init_result['message']}")
            return 1

        # 6. 部署选中的实验
        auto_run = lab_args.auto_run or lab_cfg.get("auto_run_setup", False)
        for idx in selected_indices:
            candidate = candidates[idx]
            post = candidate["post"]
            exp_id = _make_experiment_id(post)
            github_url = github_urls.get(str(idx))

            print(f"\n部署实验: {exp_id}")

            # 获取 GitHub 代码质量
            code_quality = {}
            if github_url:
                print(f"  获取 GitHub 信息: {github_url}")
                code_quality = fetch_github_code_quality(github_url)

            result = deploy_experiment(
                runner, exp_id, candidate,
                github_url=github_url,
                auto_run=auto_run,
            )
            print(f"  状态: {result['status']}")

            if result["status"] == "deployed":
                # 记录到本地状态
                record_experiment(
                    LAB_STATE_PATH,
                    experiment_id=exp_id,
                    branch=result["branch"],
                    post_url=result["post_url"],
                    github_url=github_url,
                    author_handle=result["author_handle"],
                    status="deployed",
                    brief_date=brief.get("generated_at", "")[:10],
                )

                # 更新 EXPERIMENTS.md（远端）
                doc_exp = {
                    "experiment_id": exp_id,
                    "brief_date": brief.get("generated_at", "")[:10],
                    "post_url": result["post_url"],
                    "author_handle": result["author_handle"],
                    "github_url": github_url,
                    "install_success": result.get("install_success"),
                    "code_quality": code_quality,
                    "summary_bullets": post.get("summary_bullets", []),
                }

                # 切到实验分支更新文档
                md_result = update_experiments_md_remote(runner, doc_exp)
                if md_result["status"] == "ok":
                    print(f"  EXPERIMENTS.md 已更新")

                # 飞书文档同步
                doc_token = lab_cfg.get("feishu_doc_token")
                if doc_token:
                    doc_result = sync_to_feishu_doc(doc_exp, config)
                    print(f"  飞书文档: {doc_result['status']}")

                # 推送部署结果到飞书
                if feishu_enabled:
                    try:
                        push_deploy_result(result, config)
                    except Exception:
                        pass

                # 触发 Luna 自动调研（如果配置了 luna_receive_id）
                if github_url and lab_cfg.get("luna_receive_id"):
                    luna_result = trigger_luna_research(
                        experiment_id=exp_id,
                        github_url=github_url,
                        post_url=result["post_url"],
                        author_handle=result["author_handle"],
                        feishu_config=config,
                        luna_receive_id=lab_cfg.get("luna_receive_id"),
                    )
                    print(f"  Luna 调研: {luna_result['status']} - {luna_result.get('message', '')}")
            else:
                print(f"  错误: {result.get('message', '')}")

        print("\n部署完成")
        return 0

    return 0


def main() -> int:
    import sys

    _maybe_warn_skill_update()

    # lab 命令需要提前拦截，因为它有自己的参数解析器
    if len(sys.argv) >= 2 and sys.argv[1] == "lab":
        # 只解析 --config 和 --override-config（lab 之前的通用参数）
        base_config = AppConfig.load(str(DEFAULT_CONFIG))
        config = base_config.merged_with(str(LOCAL_CONFIG_PATH) if LOCAL_CONFIG_PATH.exists() else None).raw
        lab_parser = build_lab_parser()
        lab_args = lab_parser.parse_args(sys.argv[2:])
        return cmd_lab(config, lab_args)

    parser = build_parser()
    args = parser.parse_args()
    if args.command == "setup":
        target_config = Path(args.override_config) if args.override_config else LOCAL_CONFIG_PATH
        def _post_write_generate(config_path: Path) -> int:
            merged_config = AppConfig.load(args.config).merged_with(str(config_path)).raw
            preview_config = {
                **merged_config,
                "outputs": {
                    **merged_config.get("outputs", {}),
                    "feishu": {
                        **merged_config.get("outputs", {}).get("feishu", {}),
                        "enabled": False,
                    },
                    "feishu_bitable": {
                        **merged_config.get("outputs", {}).get("feishu_bitable", {}),
                        "enabled": False,
                    },
                },
            }
            preview_args = argparse.Namespace(
                command="generate",
                config=args.config,
                override_config=str(config_path),
                window_mode="rolling_24h",
                mode=None,
                top_n=None,
                dry_run=False,
                force=False,
            )
            result = generate_digest(preview_args, preview_config, build_client(preview_config))
            return print_generate_result(result, preview_config)

        return run_setup(
            base_config_path=Path(args.config),
            target_config_path=target_config,
            client=build_client(),
            history_path=STATE_PATH,
            following_cache_path=FOLLOWING_CACHE_PATH,
            post_write_generate=_post_write_generate,
        )
    base_config = AppConfig.load(args.config)
    config = base_config.merged_with(args.override_config).raw
    client = build_client(config)
    if args.command == "sync-authors":
        return cmd_sync_authors(config, client)
    if args.command == "show-core-authors":
        return cmd_show_core_authors(config)
    if args.command == "schedule-tick":
        return cmd_schedule_tick(args, config, client)
    return cmd_generate(args, config, client)


def merge_watchlists(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for item in [*secondary, *primary]:
        handle = str(item.get("handle", "")).lstrip("@")
        if not handle or handle in seen:
            continue
        seen.add(handle)
        merged.append(item)
    return merged


def _maybe_warn_skill_update() -> None:
    status = inspect_skill_sync(REPO_SKILL_PATH, OPENCLAW_SKILL_PATH)
    if not status.get("needs_update"):
        return
    print_section("Skill Update")
    print(
        render_table(
            ["Field", "Value"],
            [
                ["Repo Version", status.get("repo_version") or "unknown"],
                ["Installed Version", status.get("installed_version") or "unknown"],
                ["Status", "OpenClaw skill 副本比仓库旧，建议先同步"],
                ["Sync Command", status.get("sync_command") or ""],
                ["Next Step", "同步后建议新开一个 OpenClaw 会话，再继续配置或生成日报。"],
            ],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
