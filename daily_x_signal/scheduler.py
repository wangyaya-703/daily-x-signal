from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .store import load_json, save_json


def load_scheduler_state(path: str | Path) -> dict[str, Any]:
    return load_json(path, {"sent_dates": {}, "failed_attempts": []})


def should_run_scheduler(
    config: dict[str, Any], state: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    scheduler_cfg = config.get("scheduler", {})
    if not bool(scheduler_cfg.get("enabled", True)):
        return {"should_run": False, "reason": "scheduler.enabled=false，当前未启用定时发送。", "digest_date": None}
    timezone_name = config["profile"]["timezone"]
    tz = ZoneInfo(timezone_name)
    current = now.astimezone(tz) if now else datetime.now(tz)

    trigger_hour = int(scheduler_cfg.get("trigger_hour", 8))
    trigger_minute = int(scheduler_cfg.get("trigger_minute", 30))
    deadline_hour = int(scheduler_cfg.get("catchup_deadline_hour", 11))
    deadline_minute = int(scheduler_cfg.get("catchup_deadline_minute", 30))

    trigger_at = current.replace(hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0)
    deadline_at = current.replace(hour=deadline_hour, minute=deadline_minute, second=0, microsecond=0)
    digest_date = trigger_at.date().isoformat()

    sent_dates = state.get("sent_dates", {})
    if digest_date in sent_dates:
        return {"should_run": False, "reason": f"今天 {digest_date} 的日报已经发送过了。", "digest_date": digest_date}
    if current < trigger_at:
        return {"should_run": False, "reason": "当前还没到 08:30，不执行发送。", "digest_date": digest_date}
    if current > deadline_at:
        return {
            "should_run": False,
            "reason": f"当前已经超过补偿截止时间 {deadline_hour:02d}:{deadline_minute:02d}，不再补发今天日报。",
            "digest_date": digest_date,
        }
    return {
        "should_run": True,
        "reason": f"当前处于发送窗口内，将尝试发送 {digest_date} 的日报。",
        "digest_date": digest_date,
    }


def record_scheduler_result(
    path: str | Path,
    digest_date: str,
    reason: str,
    success: bool,
    feishu_status: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    state = load_scheduler_state(path)
    current_time = datetime.now().astimezone().isoformat()
    state["last_attempt_at"] = current_time

    if success:
        state.setdefault("sent_dates", {})[digest_date] = {
            "sent_at": current_time,
            "reason": reason,
            "feishu_status": feishu_status,
            **(metadata or {}),
        }
        state["last_success_at"] = current_time
    else:
        failed_attempts = state.setdefault("failed_attempts", [])
        failed_attempts.append(
            {
                "attempted_at": current_time,
                "digest_date": digest_date,
                "reason": reason,
                **(metadata or {}),
            }
        )
        state["failed_attempts"] = failed_attempts[-20:]

    save_json(path, state)
