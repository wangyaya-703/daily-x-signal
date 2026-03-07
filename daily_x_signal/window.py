from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(slots=True)
class TimeWindow:
    start: datetime
    end: datetime


def resolve_window(mode: str, timezone_name: str, now: datetime | None = None) -> TimeWindow:
    tz = ZoneInfo(timezone_name)
    current = now.astimezone(tz) if now else datetime.now(tz)
    if mode == "rolling_24h":
        return TimeWindow(start=current - timedelta(hours=24), end=current)

    if mode != "scheduled":
        raise ValueError(f"Unsupported window mode: {mode}")

    today_0830 = current.replace(hour=8, minute=30, second=0, microsecond=0)
    if current < today_0830:
        end = today_0830 - timedelta(days=1)
    else:
        end = today_0830
    start = (end - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return TimeWindow(start=start, end=end)
