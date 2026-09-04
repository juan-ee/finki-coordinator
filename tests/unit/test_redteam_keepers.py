"""Red-team keepers: held behaviors pinned by the phase-gate red team (AGENTS.md review §4.4).

Ported from the adversarial battery run at HEAD 1c221df: these tests encode real held
behavior — DST ±1s transitions, half-hour DST (Lord Howe), :45 offsets (Kathmandu), the
+14 wraparound, far-future instants, boundary dates, payload-type batteries — and must
always pass. Fixtures live under tmp_path (rule 9); cron ground truth is worked-example
literals; the S3c-style zone probe is environment-adaptive by design (macOS vs Pi tzdata).
"""

import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from coordinator import scheduling
from coordinator.config import ConfigError, load_config
from coordinator.db import connect, migrate
from coordinator.handlers import checkin_submit, member_add, member_update, setting_set
from coordinator.repositories import CheckinsRepo, MembersRepo, SettingsRepo

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)  # the red team's winter anchor instant
FIXED_APPLIED_AT = NOW.isoformat()
SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.schema.json"


class FixedClock:
    """Deterministic Clock returning one fixed aware UTC instant."""

    def now(self) -> datetime:
        """Return the fixed instant."""
        return NOW


@pytest.fixture()
def stack(
    tmp_path: pathlib.Path,
) -> Iterator[tuple[MembersRepo, CheckinsRepo, SettingsRepo, FixedClock]]:
    """A real SQLite repo stack on a tmp_path database (keepers pin behavior on real rows)."""
    conn = connect(tmp_path / "keepers.db")
    migrate(conn, FIXED_APPLIED_AT)
    yield MembersRepo(conn), CheckinsRepo(conn), SettingsRepo(conn), FixedClock()
    conn.close()


def _add_member(members: MembersRepo, **overrides: object) -> dict[str, object]:
    """Add Rita (Europe/Berlin, 08:00) through member_add with per-test overrides."""
    payload: dict[str, object] = {
        "name": "Rita",
        "timezone": "Europe/Berlin",
        "wake": "08:00",
        "telegram_id": 555000111,
    }
    payload.update(overrides)
    return member_add(payload, members, None, None, FixedClock())


# --- scheduling keepers (red team S3/S3c/S4/S5) -----------------------------------------


@pytest.mark.parametrize(
    ("wake", "tz", "at_utc", "expected"),
    [
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2026, 3, 29, 0, 59, 59, tzinfo=UTC),
            "0 7 * * *",
            id="berlin-spring-1s-before",
        ),
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2026, 3, 29, 1, 0, 0, tzinfo=UTC),
            "0 6 * * *",
            id="berlin-spring-1s-after",
        ),
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC),
            "0 6 * * *",
            id="berlin-fall-1s-before",
        ),
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2026, 10, 25, 1, 0, 0, tzinfo=UTC),
            "0 7 * * *",
            id="berlin-fall-1s-after",
        ),
        pytest.param(
            "09:00",
            "Australia/Lord_Howe",
            datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
            "0 22 * * *",
            id="lord-howe-dst-plus-11",
        ),
        pytest.param(
            "09:00",
            "Australia/Lord_Howe",
            datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
            "30 22 * * *",
            id="lord-howe-std-plus-1030",
        ),
        pytest.param(
            "08:00",
            "Asia/Kathmandu",
            datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
            "15 2 * * *",
            id="kathmandu-plus-0545",
        ),
        pytest.param(
            "00:00",
            "Pacific/Kiritimati",
            datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
            "0 10 * * *",
            id="kiritimati-plus-14-wraparound",
        ),
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2026, 1, 15, 7, 0, tzinfo=ZoneInfo("America/New_York")),
            "0 7 * * *",
            id="at_utc-carries-another-zone",
        ),
        pytest.param(
            "08:00",
            "Europe/Berlin",
            datetime(2500, 1, 15, 12, 0, tzinfo=UTC),
            "0 7 * * *",
            id="year-2500",
        ),
    ],
)
def test_wake_to_cron_dst_and_offset_battery(
    wake: str, tz: str, at_utc: datetime, expected: str
) -> None:
    """Exotic offsets compile exactly: ±1s DST edges, half-hour DST, :45, +14 wrap, year 2500."""
    assert scheduling.wake_to_cron_expr(wake, tz, at_utc) == expected


def test_wake_inside_spring_gap_still_compiles() -> None:
    """02:30 local does not exist on Berlin's 2026 spring-forward day; offset math compiles it."""
    cron = scheduling.wake_to_cron_expr(
        "02:30", "Europe/Berlin", datetime(2026, 3, 29, 1, 0, 0, tzinfo=UTC)
    )

    assert cron == "30 0 * * *"


@pytest.mark.parametrize("tz", ["America/Caracas", "Europe/Amsterdam"])
def test_1900_lmt_offset_within_one_minute(tz: str) -> None:
    """Sub-minute 1900 LMT offsets must not skew the compiled cron by more than one minute."""
    exact_utc = datetime(1900, 6, 16, 8, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC)

    cron = scheduling.wake_to_cron_expr("08:00", tz, exact_utc)

    minute, hour = (int(part) for part in cron.split()[:2])
    fired = datetime(1900, 6, 16, hour, minute, tzinfo=UTC)
    assert abs(fired - exact_utc) <= timedelta(minutes=1)


def test_lowercase_zone_names_follow_zoneinfo_and_fs() -> None:
    """Lowercase aliases resolve only where the OS tzdata allows; scheduling.py delegates.

    Environment probe kept from the red team: case-insensitive filesystems (macOS) resolve
    them, the Pi's case-sensitive ext4 does not — the module is correct either way.
    """
    for tz, expected in (("Europe/berlin", "0 7 * * *"), ("utc", "0 8 * * *")):
        try:
            ZoneInfo(tz)
            resolvable = True
        except (OSError, ValueError, ZoneInfoNotFoundError):
            resolvable = False
        if resolvable:
            assert scheduling.wake_to_cron_expr("08:00", tz, NOW) == expected
        else:
            with pytest.raises(scheduling.SchedulingError):
                scheduling.wake_to_cron_expr("08:00", tz, NOW)


# --- handlers keepers (red team H5a/H5b/H5c/H6) ------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"name": ["A"], "timezone": "UTC", "wake": "08:00"}, id="name-list"),
        pytest.param({"name": "A", "timezone": {"tz": "UTC"}, "wake": "08:00"}, id="tz-dict"),
        pytest.param({"name": "A", "timezone": "UTC", "wake": "24:61"}, id="wake-24-61"),
        pytest.param({"name": "A", "timezone": "UTC", "wake": "7:60"}, id="wake-unpadded-hour"),
        pytest.param({"name": "A", "timezone": "UTC", "wake": "08:00 "}, id="wake-trailing-space"),
        pytest.param(
            {"name": "A", "timezone": "UTC", "wake": "\uff14\uff18:00"}, id="wake-fullwidth"
        ),
        pytest.param(
            {"name": "A", "timezone": "UTC", "wake": "08:00", "telegram_id": 1.5}, id="tg-float"
        ),
        pytest.param(
            {"name": "A", "timezone": "UTC", "wake": "08:00", "telegram_id": True}, id="tg-bool"
        ),
        pytest.param(
            {"name": "A", "timezone": "UTC", "wake": "08:00", "active": 1}, id="self-service-active"
        ),
    ],
)
def test_member_add_rejects_malformed_payloads(
    payload: dict[str, object],
    stack: tuple[MembersRepo, CheckinsRepo, SettingsRepo, FixedClock],
) -> None:
    """Malformed member_add payloads fail cleanly: no relay, empty data, nothing inserted."""
    members, _checkins, _settings, _clock = stack

    result = member_add(payload, members, None, None, FixedClock())

    assert result["ok"] is False
    assert result["cron_relay"] is None
    assert result["data"] == {}
    assert members.list(active=None) == []


def test_update_and_checkin_payload_battery(
    stack: tuple[MembersRepo, CheckinsRepo, SettingsRepo, FixedClock],
) -> None:
    """Bad update/checkin payloads fail cleanly; boundary dates 0001-01-01 and 9999-12-31 hold."""
    members, checkins, settings, clock = stack
    added = _add_member(members)
    mid = added["data"]["member_id"]
    bad_updates: list[dict[str, object]] = [
        {"member_id": mid, "wake": "25:00"},
        {"member_id": mid, "active": 2},
        {"member_id": 0},
        {"member_id": -5},
        {"member_id": True},
        {"member_id": 1.5},
        {"member_id": mid, "timezone": "Nope/Nope"},
        {"member_id": mid, "role": 3},
    ]
    for payload in bad_updates:
        result = member_update(payload, members, checkins, settings, clock)
        assert result["ok"] is False, payload
    for bad_date in (
        "2026-02-30",
        "20260115",
        "2026-1-1",
        "2026-13-01",
        "\uff12\uff00\uff12\uff16-01-15",
    ):
        result = checkin_submit(
            {"member_id": mid, "date": bad_date}, members, checkins, settings, clock
        )
        assert result["ok"] is False, bad_date
    for boundary in ("0001-01-01", "9999-12-31"):
        result = checkin_submit(
            {"member_id": mid, "date": boundary}, members, checkins, settings, clock
        )
        assert result["ok"] is True, boundary

    edited = member_update(
        {"member_id": mid, "wake": "07:30", "timezone": "America/New_York"},
        members,
        checkins,
        settings,
        clock,
    )

    assert edited["ok"] is True
    relay = edited["cron_relay"]
    assert relay is not None
    assert relay["args"]["schedule"] == "30 12 * * *"


def test_unicode_names_and_huge_fields_are_stored_verbatim(
    stack: tuple[MembersRepo, CheckinsRepo, SettingsRepo, FixedClock],
) -> None:
    """Unicode names and 10k-char check-in fields store verbatim; null optional = absent."""
    members, checkins, settings, clock = stack
    added = _add_member(members, name="R\u00f8d \U0001f680")
    assert added["ok"] is True
    mid = added["data"]["member_id"]
    big = "x" * 10_000

    result = checkin_submit(
        {"member_id": mid, "date": "2026-01-15", "done": big, "next": big, "blockers": big},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is True
    stored = checkins.by_date("2026-01-15")
    assert stored[0].done == big
    deactivated = member_update({"member_id": mid, "active": 0}, members, checkins, settings, clock)
    assert deactivated["ok"] is True  # documents: deactivation relays pause for a live job
    inactive = checkin_submit(
        {"member_id": mid, "date": "2026-01-16", "done": "hi"}, members, checkins, settings, clock
    )
    assert inactive["ok"] is True  # documents: check-in for an inactive member succeeds
    nulled = member_update({"member_id": mid, "role": None}, members, checkins, settings, clock)
    assert nulled["ok"] is True  # documents: null in an optional field means absent


@pytest.mark.parametrize(
    ("key", "value", "expect_ok"),
    [
        pytest.param("digest_time", "00:00", False, id="dropped-dial-min"),
        pytest.param("digest_time", "23:59", False, id="dropped-dial-max"),
        pytest.param("digest_time", "24:00", False, id="time-overflow"),
        pytest.param("digest_time", "18:00 ", False, id="time-trailing-space"),
        pytest.param("nudge_limit", "000", True, id="nudge-zero-padded"),
        pytest.param("nudge_limit", "9" * 500, True, id="nudge-500-digits"),
        pytest.param("nudge_limit", "-1", False, id="nudge-negative"),
        pytest.param("digest_chat", "-0", True, id="chat-negative-zero"),
        pytest.param("digest_chat", "+123", False, id="chat-plus-sign"),
        pytest.param("digest_chat", "dm", True, id="chat-dm"),
    ],
)
def test_setting_value_boundaries(
    key: str,
    value: str,
    expect_ok: bool,
    stack: tuple[MembersRepo, CheckinsRepo, SettingsRepo, FixedClock],
) -> None:
    """The documented per-key setting shapes hold at their boundaries (incl. 500-digit values)."""
    members, _checkins, settings, clock = stack

    result = setting_set({"key": key, "value": value}, members, None, settings, clock)

    assert result["ok"] is expect_ok
    if expect_ok:
        assert settings.get(key) == value


# --- config + db keepers (red team C3b/D4) ------------------------------------------------


def test_load_errors_are_config_error(tmp_path: pathlib.Path) -> None:
    """Every bad-load shape (missing, directory, tab indent, non-mapping docs) raises ConfigError."""
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml", SCHEMA_PATH)
    with pytest.raises(ConfigError):
        load_config(tmp_path, SCHEMA_PATH)  # config path is a directory
    tabbed = tmp_path / "tabbed.yaml"
    tabbed.write_text("project:\n\tname: x\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tabbed, SCHEMA_PATH)
    for text in ("- a\n- b\n", "", "just a string\n"):
        doc = tmp_path / "doc.yaml"
        doc.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(doc, SCHEMA_PATH)


def test_settings_null_value_falls_back_to_default(tmp_path: pathlib.Path) -> None:
    """A stored NULL in settings falls back to the documented default instead of 'None'."""
    conn = connect(tmp_path / "settings-null.db")
    migrate(conn, FIXED_APPLIED_AT)
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES ('nudge_limit', NULL)")
        conn.commit()

        assert SettingsRepo(conn).get("nudge_limit") == "2"
    finally:
        conn.close()
