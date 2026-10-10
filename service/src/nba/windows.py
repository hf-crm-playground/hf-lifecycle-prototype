"""Local send windows. A decision made outside its window is held, not
dropped, and re-validated when the window opens (see run.release_held)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class SendTime:
    held: bool
    send_at: datetime  # UTC
    local_hour: Optional[int]
    opens: Optional[str] = None  # "09:00 Asia/Tokyo" when held


def _zone(tz: Optional[str]):
    if not tz:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def next_send_time(now: datetime, tz: Optional[str], window: Optional[Tuple[int, int]]) -> SendTime:
    """`window` is [start, end) in local hours; None means no window (in-app,
    native flows). An unknown timezone falls back to UTC for the check; the
    policy already refuses push without a known timezone."""
    now = now.astimezone(timezone.utc)
    if window is None:
        return SendTime(False, now, None)
    zone = _zone(tz) or timezone.utc
    local = now.astimezone(zone)
    lo, hi = window
    if lo <= local.hour < hi:
        return SendTime(False, now, local.hour)
    day = local.date() if local.hour < lo else local.date() + timedelta(days=1)
    opens_local = datetime(day.year, day.month, day.day, lo, 0, tzinfo=zone)
    return SendTime(True, opens_local.astimezone(timezone.utc), local.hour, f"{lo:02d}:00 {tz or 'UTC'}")
