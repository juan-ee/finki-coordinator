"""Pure timezone-to-cron scheduling: the only module that computes schedules (AGENTS.md rule 4).

No I/O, no clock: the UTC offset is resolved exclusively from the caller-supplied aware
instant "at_utc" (the moment the cron job is created or updated), never from the host.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_WAKE_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SchedulingError(Exception):
    """Raised when a wake time, timezone, or instant cannot be compiled to a UTC cron schedule."""


def validate_wake(wake: str) -> None:
    """Validate a strict zero-padded 24h wake time ("HH:MM"), raising SchedulingError otherwise."""
    if _WAKE_RE.fullmatch(wake) is None:
        raise SchedulingError(
            f"invalid wake time {wake!r}: expected strict zero-padded 24-hour 'HH:MM' between "
            "00:00 and 23:59 (e.g. '08:00'); '8:00' (missing zero-pad) and '25:00' are rejected"
        )


def wake_to_cron_expr(wake: str, tz: str, at_utc: datetime) -> str:
    """Compile a daily local wake time and IANA zone into a UTC cron expression "M H * * *"."""
    validate_wake(wake)
    if at_utc.tzinfo is None or at_utc.utcoffset() is None:
        raise SchedulingError(
            "at_utc must be timezone-aware (tzinfo present), e.g. "
            "datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc); a naive datetime would "
            "silently assume the host's local timezone"
        )
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise SchedulingError(
            f"unknown timezone {tz!r}: IANA tz database lookup failed ({type(exc).__name__})"
        ) from exc
    # DST resolution lives here and only here: the offset is read from the single instant
    # at_utc (zoneinfo's flip decisions at second granularity are authoritative).
    offset = at_utc.astimezone(zone).utcoffset()
    if offset is None:  # unreachable for an aware datetime against a real zone; keeps mypy strict
        raise SchedulingError(f"timezone {tz!r} produced no UTC offset at the given instant")
    total_minutes = int(wake[:2]) * 60 + int(wake[3:5]) - offset // timedelta(minutes=1)
    # A date shift from modular wraparound (wake near midnight in far-offset zones) is
    # irrelevant: the daily '* * *' wildcard makes the cron fire at the same UTC
    # minute/hour every day, so only minute and hour survive below.
    minute = total_minutes % 60
    hour = (total_minutes // 60) % 24
    return f"{minute} {hour} * * *"
