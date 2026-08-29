"""Scheduling contracts: local wake time + IANA zone + aware instant -> UTC cron expression."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from coordinator.scheduling import SchedulingError, validate_wake, wake_to_cron_expr

# Fixed whole-day instants: every zone asserted below shares one UTC offset across its day.
AT_JAN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
AT_JUL = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)


def test_zoneinfo_flip_instants_berlin_2026() -> None:
    """Ground truth: Berlin's offset flips exactly at 01:00:00 UTC on both 2026 EU transitions."""
    spring_before = datetime(2026, 3, 29, 0, 59, 59, tzinfo=UTC)
    spring_after = datetime(2026, 3, 29, 1, 0, 0, tzinfo=UTC)
    fall_before = datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC)
    fall_after = datetime(2026, 10, 25, 1, 0, 0, tzinfo=UTC)

    assert spring_before.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() == timedelta(hours=1)
    assert spring_after.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() == timedelta(hours=2)
    assert fall_before.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() == timedelta(hours=2)
    assert fall_after.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize(
    ("wake", "tz", "at_utc", "expected"),
    [
        pytest.param("08:00", "America/Guayaquil", AT_JAN, "0 13 * * *", id="guayaquil-utc-5"),
        pytest.param("08:00", "Europe/Berlin", AT_JAN, "0 7 * * *", id="berlin-winter-utc-1"),
        pytest.param("08:00", "Europe/Berlin", AT_JUL, "0 6 * * *", id="berlin-summer-utc-2"),
    ],
)
def test_wake_to_cron_expr_spec_cases(wake: str, tz: str, at_utc: datetime, expected: str) -> None:
    """The ROADMAP spec cases compile a local wake time to the expected UTC cron expression."""
    assert wake_to_cron_expr(wake, tz, at_utc) == expected


@pytest.mark.parametrize(
    ("at_utc", "expected"),
    [
        pytest.param(
            datetime(2026, 3, 29, 0, 59, 0, tzinfo=UTC),
            "0 7 * * *",
            id="spring-0059Z-still-plus1",
        ),
        pytest.param(
            datetime(2026, 3, 29, 1, 0, 0, tzinfo=UTC),
            "0 6 * * *",
            id="spring-0100Z-flipped-plus2",
        ),
        pytest.param(
            datetime(2026, 10, 25, 0, 59, 0, tzinfo=UTC),
            "0 6 * * *",
            id="fall-0059Z-still-plus2",
        ),
        pytest.param(
            datetime(2026, 10, 25, 1, 0, 0, tzinfo=UTC),
            "0 7 * * *",
            id="fall-0100Z-flipped-plus1",
        ),
    ],
)
def test_dst_boundary_instants_flip_the_hour(at_utc: datetime, expected: str) -> None:
    """DST comes solely from at_utc: the Berlin hour flips exactly at the 01:00 UTC transitions."""
    assert wake_to_cron_expr("08:00", "Europe/Berlin", at_utc) == expected


def test_minutes_map_verbatim_into_the_expression() -> None:
    """Minute arithmetic: 08:15 in UTC-5 compiles to minute-first '15 13 * * *'."""
    assert wake_to_cron_expr("08:15", "America/Guayaquil", AT_JAN) == "15 13 * * *"


def test_wake_just_after_midnight_in_utc_plus_13_wraps_back() -> None:
    """00:00 in Pacific/Apia (+13) fires at 11:00 UTC on the previous day: hour wraps mod 24."""
    assert wake_to_cron_expr("00:00", "Pacific/Apia", AT_JAN) == "0 11 * * *"


def test_wake_just_before_midnight_in_utc_minus_9_wraps_forward() -> None:
    """23:59 in America/Anchorage (-9 AKST in January) fires the next day at 08:59 UTC."""
    assert wake_to_cron_expr("23:59", "America/Anchorage", AT_JAN) == "59 8 * * *"


@pytest.mark.parametrize(
    "bad_wake",
    ["25:00", "8:00", "24:00", "07:60", "0800", ""],
)
def test_malformed_wake_raises_scheduling_error(bad_wake: str) -> None:
    """wake_to_cron_expr rejects anything outside strict zero-padded HH:MM before any tz work."""
    with pytest.raises(SchedulingError, match="wake"):
        wake_to_cron_expr(bad_wake, "Europe/Berlin", AT_JAN)


def test_validate_wake_accepts_strict_zero_padded_times() -> None:
    """The strict form 00:00 through 23:59 validates without raising."""
    validate_wake("00:00")
    validate_wake("08:00")
    validate_wake("23:59")


@pytest.mark.parametrize("bad_wake", ["25:00", "8:00"])
def test_validate_wake_rejects_malformed_times(bad_wake: str) -> None:
    """validate_wake raises SchedulingError naming the strict HH:MM rule."""
    with pytest.raises(SchedulingError, match="HH:MM"):
        validate_wake(bad_wake)


def test_unknown_timezone_raises_scheduling_error_naming_the_zone() -> None:
    """A failed zoneinfo lookup is wrapped in SchedulingError naming the zone (never raw)."""
    with pytest.raises(SchedulingError, match="Mars/Olympus"):
        wake_to_cron_expr("08:00", "Mars/Olympus", AT_JAN)


def test_wake_is_validated_before_timezone_lookup() -> None:
    """An invalid wake raises before the timezone is resolved, even when the zone is unknown too."""
    with pytest.raises(SchedulingError, match="wake"):
        wake_to_cron_expr("25:00", "Mars/Olympus", AT_JAN)


def test_naive_at_utc_raises_scheduling_error() -> None:
    """A naive at_utc is rejected: it would silently adopt the host's local timezone."""
    naive = datetime(2026, 1, 15, 12, 0, 0)  # noqa: DTZ001 — naive input is the error case under test
    with pytest.raises(SchedulingError, match="timezone-aware"):
        wake_to_cron_expr("08:00", "Europe/Berlin", naive)


# --- phase-gate red-team regressions (F, H) --------------------------------------------


@pytest.mark.parametrize(
    "bad_wake",
    ["0\uff10:00", "\uff12\uff13:00", "08:\uff10\uff10", "23:5\uff15", "\u0661\u0662:\u0663\u0660"],
)
def test_validate_wake_rejects_non_ascii_digits(bad_wake: str) -> None:
    """Only ASCII digits satisfy strict HH:MM; full-width/Arabic-Indic digits are rejected."""
    with pytest.raises(SchedulingError):
        validate_wake(bad_wake)


def test_wake_to_cron_expr_rejects_non_ascii_digit_wake() -> None:
    """wake_to_cron_expr inherits the ASCII-only digit rule through validate_wake."""
    with pytest.raises(SchedulingError, match="wake"):
        wake_to_cron_expr("0\uff10:00", "Europe/Berlin", AT_JAN)


@pytest.mark.parametrize("bad", [None, 800, b"08:00", 8.0, ["08:00"]])
def test_validate_wake_rejects_non_string_wake(bad: object) -> None:
    """A non-str wake raises the module's SchedulingError, never a TypeError."""
    with pytest.raises(SchedulingError):
        validate_wake(bad)


@pytest.mark.parametrize("bad", [None, 800, b"08:00"])
def test_wake_to_cron_expr_rejects_non_string_wake(bad: object) -> None:
    """wake_to_cron_expr raises SchedulingError for a non-str wake before any tz work."""
    with pytest.raises(SchedulingError):
        wake_to_cron_expr(bad, "Europe/Berlin", AT_JAN)
