"""Handler contracts: the 7 tool handlers on in-memory fakes + FakeClock (deterministic).

Ground truth for cron strings is the worked examples pinned by T0.7 — 08:00 in
America/Guayaquil at 2026-01-15T12:00Z is "0 13 * * *", 08:00 in Europe/Berlin is
"0 7 * * *" — asserted as literals so these tests can disagree with scheduling.py.
The fakes below are dict-backed implementations of the repositories Protocols: handlers
are typed against those Protocols, so every green call here is the DIP proof itself.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from coordinator.handlers import (
    checkin_submit,
    checkins_by_date,
    member_add,
    member_list,
    member_update,
    setting_get,
    setting_set,
)
from coordinator.repositories import (
    DEFAULTS,
    Checkin,
    CheckinsRepository,
    Member,
    MembersRepository,
    SettingsRepository,
)

AT_NOON = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)  # winter instant from the T0.7 cases
SEED_CREATED_AT = "2026-01-01T09:00:00+00:00"


class FakeClock:
    """Deterministic Clock: returns a fixed aware UTC instant (settable per test)."""

    def __init__(self, instant: datetime | None = None) -> None:
        self.instant = instant if instant is not None else AT_NOON

    def now(self) -> datetime:
        """Return the fixed instant."""
        return self.instant


class FakeMembers:
    """Dict-backed MembersRepository fake mirroring MembersRepo (None = leave unchanged)."""

    def __init__(self) -> None:
        self._rows: dict[int, Member] = {}
        self._next_id = 1

    def add(
        self,
        *,
        name: str,
        telegram_id: int | None = None,
        timezone: str = "UTC",
        wake: str | None = None,
        role: str | None = None,
        status_days: str | None = None,
        active: int = 1,
        created_at: str,
    ) -> Member:
        """Insert a member and return the stored row."""
        member = Member(
            id=self._next_id,
            name=name,
            telegram_id=telegram_id,
            timezone=timezone,
            wake=wake,
            role=role,
            status_days=status_days,
            active=active,
            created_at=created_at,
            updated_at=created_at,
        )
        self._next_id += 1
        self._rows[member.id] = member
        return member

    def update(
        self,
        member_id: int,
        *,
        updated_at: str,
        name: str | None = None,
        telegram_id: int | None = None,
        timezone: str | None = None,
        wake: str | None = None,
        role: str | None = None,
        status_days: str | None = None,
        active: int | None = None,
    ) -> Member | None:
        """Set the supplied columns, stamping updated_at (None when the id is unknown)."""
        member = self._rows.get(member_id)
        if member is None:
            return None
        changes: dict[str, object] = {"updated_at": updated_at}
        for column, value in (
            ("name", name),
            ("telegram_id", telegram_id),
            ("timezone", timezone),
            ("wake", wake),
            ("role", role),
            ("status_days", status_days),
            ("active", active),
        ):
            if value is not None:
                changes[column] = value
        updated = replace(member, **changes)
        self._rows[member_id] = updated
        return updated

    def get(self, member_id: int) -> Member | None:
        """Return one member by id, including inactive rows (None if absent)."""
        return self._rows.get(member_id)

    def list(self, *, active: int | None = None) -> list[Member]:
        """Return members ordered by name, optionally filtered to the given active flag."""
        rows = list(self._rows.values())
        if active is not None:
            rows = [member for member in rows if member.active == active]
        return sorted(rows, key=lambda member: member.name)


class FakeCheckins:
    """Dict-backed CheckinsRepository fake mirroring CheckinsRepo (upsert, latest wins)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[int, str], Checkin] = {}
        self._next_id = 1

    def submit(
        self,
        *,
        member_id: int,
        date: str,
        done: str | None = None,
        next: str | None = None,
        blockers: str | None = None,
        source: str = "auto",
        created_at: str,
    ) -> Checkin:
        """Upsert the member's check-in for date, latest-wins, and return the stored row."""
        checkin = Checkin(
            id=self._next_id,
            member_id=member_id,
            date=date,
            done=done,
            next=next,
            blockers=blockers,
            source=source,
            created_at=created_at,
        )
        self._next_id += 1
        self._rows[(member_id, date)] = checkin
        return checkin

    def by_date(self, date: str) -> list[Checkin]:
        """Return the date's check-ins ordered by member id."""
        rows = [row for (_member_id, day), row in self._rows.items() if day == date]
        return sorted(rows, key=lambda row: row.member_id)


class FakeSettings:
    """Dict-backed SettingsRepository fake mirroring SettingsRepo (DEFAULTS fallback)."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str:
        """Return the stored value for key, falling back to DEFAULTS (KeyError when unknown)."""
        if key in self._values:
            return self._values[key]
        return DEFAULTS[key]

    def set(self, key: str, value: str) -> None:
        """Insert or overwrite the stored value for key."""
        self._values[key] = value


def _wire(
    clock: FakeClock | None = None,
) -> tuple[MembersRepository, CheckinsRepository, SettingsRepository, FakeClock]:
    """Build a fresh fake stack typed as the handler Protocols — the DIP seam under test."""
    return (
        FakeMembers(),
        FakeCheckins(),
        FakeSettings(),
        clock if clock is not None else FakeClock(),
    )


def _seed_member(members: MembersRepository, **overrides: object) -> Member:
    """Add one active member (Alice, 08:00 wake, America/Guayaquil) with per-test overrides."""
    fields: dict[str, object] = {
        "name": "Alice",
        "telegram_id": 111,
        "timezone": "America/Guayaquil",
        "wake": "08:00",
        "role": "Developer",
        "created_at": SEED_CREATED_AT,
    }
    fields.update(overrides)
    return members.add(**fields)


# --- member_add ----------------------------------------------------------------------


def test_member_add_happy_path_creates_row_and_relay() -> None:
    """A valid add inserts the member and returns the exact create relay for its cron job."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {
            "name": "Alice",
            "timezone": "America/Guayaquil",
            "wake": "08:00",
            "telegram_id": 111,
            "role": "Dev",
        },
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "create", "name": "checkin-1", "schedule": "0 13 * * *"},
    }
    assert result["data"] == {"member_id": 1}
    assert "Alice added" in result["summary"]
    assert "checkin-1" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.name == "Alice"
    assert member.telegram_id == 111
    assert member.timezone == "America/Guayaquil"
    assert member.wake == "08:00"
    assert member.role == "Dev"
    assert member.active == 1
    assert member.created_at == AT_NOON.isoformat()
    assert member.updated_at == AT_NOON.isoformat()


def test_member_add_defaults_role_and_telegram_to_none() -> None:
    """Omitted optional fields store as None, and the member still gets a create relay."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {"name": "Bob", "timezone": "Europe/Berlin", "wake": "08:00"},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "create", "name": "checkin-1", "schedule": "0 7 * * *"},
    }
    member = members.get(1)
    assert member is not None
    assert member.telegram_id is None
    assert member.role is None


def test_member_add_rejects_active_key_self_service_rule() -> None:
    """member_add has no active field: a payload carrying one is rejected, nothing inserted."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {"name": "Alice", "timezone": "America/Guayaquil", "wake": "08:00", "active": 0},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "active" in result["summary"]
    assert result["cron_relay"] is None
    assert result["data"] == {}
    assert members.list(active=1) == []
    assert members.list(active=0) == []


def test_member_add_rejects_unknown_timezone() -> None:
    """An unresolvable IANA zone fails with the bad name in the summary; nothing inserted."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {"name": "Alice", "timezone": "Mars/Olympus", "wake": "08:00"},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "Mars/Olympus" in result["summary"]
    assert "timezone" in result["summary"]
    assert result["cron_relay"] is None
    assert members.list(active=1) == []


@pytest.mark.parametrize("wake", ["25:00", "8:00", "0800"])
def test_member_add_rejects_bad_wake(wake: str) -> None:
    """A wake outside strict HH:MM fails, naming the field, with no relay and no row."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {"name": "Alice", "timezone": "America/Guayaquil", "wake": wake},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "wake" in result["summary"]
    assert result["cron_relay"] is None
    assert members.list(active=1) == []


@pytest.mark.parametrize("missing", ["name", "timezone", "wake"])
def test_member_add_missing_required_field(missing: str) -> None:
    """Each required field is enforced; the summary names the first missing one."""
    members, checkins, settings, clock = _wire()
    payload: dict[str, object] = {
        "name": "Alice",
        "timezone": "America/Guayaquil",
        "wake": "08:00",
    }
    del payload[missing]

    result = member_add(payload, members, checkins, settings, clock)

    assert result["ok"] is False
    assert missing in result["summary"]
    assert result["cron_relay"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("name", 7, id="name-int"),
        pytest.param("telegram_id", "111", id="telegram-str"),
        pytest.param("telegram_id", True, id="telegram-bool"),
    ],
)
def test_member_add_rejects_wrong_types(field: str, value: object) -> None:
    """Typed validation: a present-but-wrongly-typed field fails, naming the field."""
    members, checkins, settings, clock = _wire()
    payload: dict[str, object] = {
        "name": "Alice",
        "timezone": "America/Guayaquil",
        "wake": "08:00",
        field: value,
    }

    result = member_add(payload, members, checkins, settings, clock)

    assert result["ok"] is False
    assert field in result["summary"]
    assert result["cron_relay"] is None


# --- member_update -------------------------------------------------------------------


def test_member_update_wake_change_relays_edit_exactly() -> None:
    """A wake change re-schedules the job: the edit relay carries the fresh cron string."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "wake": "09:00"}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "edit", "name": "checkin-1", "schedule": "0 14 * * *"},
    }
    assert result["data"] == {"member_id": 1}
    assert "rescheduled" in result["summary"]
    assert "14:00 UTC" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.wake == "09:00"
    assert member.updated_at == AT_NOON.isoformat()


def test_member_update_deactivate_relays_pause_exactly() -> None:
    """Deactivating pauses the job: the relay has action/name only — no schedule key."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "active": 0}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "pause", "name": "checkin-1"},
    }
    assert "paused" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.active == 0


def test_member_update_deactivate_with_wake_change_pause_wins() -> None:
    """Wake + deactivate together: the job is paused (not edited), the row keeps the wake."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update(
        {"member_id": 1, "wake": "09:00", "active": 0}, members, checkins, settings, clock
    )

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "pause", "name": "checkin-1"},
    }
    member = members.get(1)
    assert member is not None
    assert member.active == 0
    assert member.wake == "09:00"


def test_member_update_deactivate_already_inactive_no_relay() -> None:
    """Re-sending active=0 to an already-inactive member is a no-op: ok, but no relay."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)
    members.update(1, updated_at=SEED_CREATED_AT, active=0)

    result = member_update({"member_id": 1, "active": 0}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] is None
    assert "already inactive" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.active == 0


def test_member_update_deactivate_already_inactive_with_wake_no_relay() -> None:
    """Wake + active=0 on an already-inactive member relays nothing; the row keeps the wake."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)
    members.update(1, updated_at=SEED_CREATED_AT, active=0)

    result = member_update(
        {"member_id": 1, "wake": "09:00", "active": 0}, members, checkins, settings, clock
    )

    assert result["ok"] is True
    assert result["cron_relay"] is None
    assert "already inactive" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.active == 0
    assert member.wake == "09:00"


def test_member_update_timezone_change_recomputes_schedule() -> None:
    """The schedule depends on the timezone, so a tz-only change rebuilds the cron relay."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, timezone="Europe/Berlin")  # 08:00 Berlin winter -> "0 7 * * *"

    result = member_update(
        {"member_id": 1, "timezone": "America/Guayaquil"}, members, checkins, settings, clock
    )

    assert result["ok"] is True
    assert result["cron_relay"] == {
        "tool": "cronjob",
        "args": {"action": "edit", "name": "checkin-1", "schedule": "0 13 * * *"},
    }


def test_member_update_same_wake_no_relay() -> None:
    """Only a CHANGE re-schedules: sending the stored wake again yields no cron_relay."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "wake": "08:00"}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] is None


def test_member_update_reactivate_has_no_relay() -> None:
    """Reactivate alone relays nothing in T0.8 (un-pausing a paused job is a noted gap)."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)
    members.update(1, updated_at=SEED_CREATED_AT, active=0)

    result = member_update({"member_id": 1, "active": 1}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] is None
    member = members.get(1)
    assert member is not None
    assert member.active == 1


def test_member_update_unknown_member() -> None:
    """An unknown id fails with an actionable summary naming the id; no relay."""
    members, checkins, settings, clock = _wire()

    result = member_update({"member_id": 99, "wake": "09:00"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "unknown member 99" in result["summary"]
    assert result["cron_relay"] is None


def test_member_update_rejects_active_outside_0_1() -> None:
    """active is range-checked here (the repo layer stores any int); 7 is rejected."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "active": 7}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "active" in result["summary"]
    member = members.get(1)
    assert member is not None
    assert member.active == 1


def test_member_update_rejects_bad_wake_without_touching_row() -> None:
    """Validation runs before mutation: a bad wake leaves the stored row untouched."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "wake": "8:00"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "wake" in result["summary"]
    assert result["cron_relay"] is None
    member = members.get(1)
    assert member is not None
    assert member.wake == "08:00"
    assert member.updated_at == SEED_CREATED_AT


def test_member_update_rejects_bad_timezone() -> None:
    """A tz that resolves to no zone fails, naming the field and the bad value."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update(
        {"member_id": 1, "timezone": "Not/AZone"}, members, checkins, settings, clock
    )

    assert result["ok"] is False
    assert "timezone" in result["summary"]
    assert "Not/AZone" in result["summary"]
    assert result["cron_relay"] is None


def test_member_update_rejects_unknown_field() -> None:
    """status_days is a column but not a member_update payload field, so it is rejected."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update({"member_id": 1, "status_days": "5"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "status_days" in result["summary"]


def test_member_update_requires_member_id() -> None:
    """member_id is the one required field for updates."""
    members, checkins, settings, clock = _wire()

    result = member_update({"wake": "09:00"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "member_id" in result["summary"]


def test_member_update_rejects_string_member_id() -> None:
    """member_id must be an int, not a numeric string."""
    members, checkins, settings, clock = _wire()

    result = member_update({"member_id": "1"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "member_id" in result["summary"]


# --- member_list ---------------------------------------------------------------------


def test_member_list_defaults_to_active_only() -> None:
    """Default filter is the active roster; rows are name-ordered and never leak telegram_id."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, name="Alice")
    _seed_member(members, name="Bob", telegram_id=222)
    _seed_member(members, name="Zed")
    members.update(3, updated_at=SEED_CREATED_AT, active=0)

    result = member_list({}, members, checkins, settings, clock)

    assert result["ok"] is True
    rows = result["data"]["members"]
    assert [row["name"] for row in rows] == ["Alice", "Bob"]
    row = rows[0]
    assert set(row) == {"id", "name", "timezone", "wake", "role", "active"}
    assert "telegram_id" not in row
    assert result["cron_relay"] is None


def test_member_list_inactive_filter() -> None:
    """{"active": 0} lists only the inactive members."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, name="Alice")
    _seed_member(members, name="Zed")
    members.update(2, updated_at=SEED_CREATED_AT, active=0)

    result = member_list({"active": 0}, members, checkins, settings, clock)

    assert result["ok"] is True
    rows = result["data"]["members"]
    assert [row["name"] for row in rows] == ["Zed"]


@pytest.mark.parametrize("active", [2, "yes", True])
def test_member_list_rejects_bad_active(active: object) -> None:
    """The filter accepts only 0 or 1; other values fail, naming the field."""
    members, checkins, settings, clock = _wire()

    result = member_list({"active": active}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "active" in result["summary"]


# --- checkin_submit ------------------------------------------------------------------


def test_checkin_submit_happy_path() -> None:
    """A valid check-in is stored (default source "auto") and echoed in data."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = checkin_submit(
        {
            "member_id": 1,
            "date": "2026-01-15",
            "done": "shipped T0.7",
            "next": "review T0.8",
            "blockers": "none",
        },
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is True
    assert result["data"] == {"member_id": 1, "date": "2026-01-15"}
    assert result["cron_relay"] is None
    assert "Alice" in result["summary"]
    assert "2026-01-15" in result["summary"]
    rows = checkins.by_date("2026-01-15")
    assert len(rows) == 1
    assert rows[0].member_id == 1
    assert rows[0].done == "shipped T0.7"
    assert rows[0].next == "review T0.8"
    assert rows[0].blockers == "none"
    assert rows[0].source == "auto"
    assert rows[0].created_at == AT_NOON.isoformat()


def test_checkin_submit_upsert_latest_wins() -> None:
    """A second submit for the same member+date replaces the first (one row remains)."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)
    checkin_submit(
        {"member_id": 1, "date": "2026-01-15", "done": "first"},
        members,
        checkins,
        settings,
        clock,
    )

    result = checkin_submit(
        {"member_id": 1, "date": "2026-01-15", "done": "second"},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is True
    rows = checkins.by_date("2026-01-15")
    assert len(rows) == 1
    assert rows[0].done == "second"


def test_checkin_submit_unknown_member() -> None:
    """A check-in for an unknown member fails, naming the id."""
    members, checkins, settings, clock = _wire()

    result = checkin_submit(
        {"member_id": 42, "date": "2026-01-15"}, members, checkins, settings, clock
    )

    assert result["ok"] is False
    assert "unknown member 42" in result["summary"]


def test_checkin_submit_rejects_unknown_field() -> None:
    """Extra payload keys are rejected even on an otherwise valid check-in."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = checkin_submit(
        {"member_id": 1, "date": "2026-01-15", "mood": "great"},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "mood" in result["summary"]


@pytest.mark.parametrize("bad_date", ["15-01-2026", "2026-1-5", "20260115", "2026-13-45"])
def test_checkin_submit_rejects_malformed_date(bad_date: str) -> None:
    """Non-ISO and non-calendar dates fail, naming the expected format; nothing stored."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = checkin_submit({"member_id": 1, "date": bad_date}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "YYYY-MM-DD" in result["summary"]
    assert bad_date in result["summary"]
    assert checkins.by_date("2026-01-15") == []


# --- checkins_by_date ----------------------------------------------------------------


def test_checkins_by_date_returns_only_that_date() -> None:
    """Rows come back for the requested date only, ordered by member id."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, name="Alice")
    _seed_member(members, name="Bob", telegram_id=222)
    checkin_submit(
        {"member_id": 1, "date": "2026-01-15", "done": "a"}, members, checkins, settings, clock
    )
    checkin_submit(
        {"member_id": 2, "date": "2026-01-15", "done": "b"}, members, checkins, settings, clock
    )
    checkin_submit(
        {"member_id": 1, "date": "2026-01-14", "done": "yesterday"},
        members,
        checkins,
        settings,
        clock,
    )

    result = checkins_by_date({"date": "2026-01-15"}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["data"]["date"] == "2026-01-15"
    rows = result["data"]["checkins"]
    assert [row["member_id"] for row in rows] == [1, 2]
    assert rows[0]["done"] == "a"
    assert result["cron_relay"] is None


def test_checkins_by_date_rejects_malformed_date() -> None:
    """The same strict date validation guards reads."""
    members, checkins, settings, clock = _wire()

    result = checkins_by_date({"date": "01/15/2026"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "YYYY-MM-DD" in result["summary"]


# --- setting_get ---------------------------------------------------------------------


def test_setting_get_returns_default_when_not_stored() -> None:
    """An unset key falls back to the repo DEFAULTS."""
    members, checkins, settings, clock = _wire()

    result = setting_get({"key": "digest_time"}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["data"] == {"key": "digest_time", "value": DEFAULTS["digest_time"]}
    assert result["cron_relay"] is None


def test_setting_get_returns_stored_value() -> None:
    """A stored value wins over the default."""
    members, checkins, settings, clock = _wire()
    settings.set("digest_time", "17:30")

    result = setting_get({"key": "digest_time"}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["data"] == {"key": "digest_time", "value": "17:30"}


def test_setting_get_unknown_key_names_valid_keys() -> None:
    """An unknown key fails and the summary names every valid key."""
    members, checkins, settings, clock = _wire()

    result = setting_get({"key": "theme"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "theme" in result["summary"]
    for valid_key in DEFAULTS:
        assert valid_key in result["summary"]


# --- setting_set ---------------------------------------------------------------------


def test_setting_set_happy_path() -> None:
    """A valid setting is stored; no cron relay in T0.8 (digest-job relay is out of scope)."""
    members, checkins, settings, clock = _wire()

    result = setting_set(
        {"key": "digest_time", "value": "17:30"}, members, checkins, settings, clock
    )

    assert result["ok"] is True
    assert result["cron_relay"] is None
    assert settings.get("digest_time") == "17:30"


def test_setting_set_rejects_unknown_key() -> None:
    """Only the three DEFAULTS keys are settable; the summary names the valid keys."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": "theme", "value": "dark"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "theme" in result["summary"]
    for valid_key in DEFAULTS:
        assert valid_key in result["summary"]


def test_setting_set_rejects_bad_digest_time() -> None:
    """digest_time is an HH:MM, validated through scheduling.validate_wake."""
    members, checkins, settings, clock = _wire()

    result = setting_set(
        {"key": "digest_time", "value": "25:00"}, members, checkins, settings, clock
    )

    assert result["ok"] is False
    assert "25:00" in result["summary"]
    assert result["cron_relay"] is None


@pytest.mark.parametrize("value", ["abc", "-1", "1.5", ""])
def test_setting_set_rejects_bad_nudge_limit(value: str) -> None:
    """nudge_limit must be digits only."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": "nudge_limit", "value": value}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "nudge_limit" in result["summary"]


def test_setting_set_rejects_bad_digest_chat() -> None:
    """digest_chat accepts only "dm" or a chat-id string."""
    members, checkins, settings, clock = _wire()

    result = setting_set(
        {"key": "digest_chat", "value": "group"}, members, checkins, settings, clock
    )

    assert result["ok"] is False
    assert "digest_chat" in result["summary"]


def test_setting_set_rejects_non_string_value() -> None:
    """The settings store is str-keyed/str-valued, so value must arrive as a string."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": "nudge_limit", "value": 2}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "value" in result["summary"]


def test_setting_set_requires_value() -> None:
    """Both key and value are required."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": "digest_time"}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "value" in result["summary"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("digest_chat", "dm", id="chat-dm"),
        pytest.param("digest_chat", "12345", id="chat-id"),
        pytest.param("digest_chat", "-100200300", id="chat-group-negative"),
        pytest.param("nudge_limit", "2", id="nudge-digits"),
        pytest.param("digest_time", "07:15", id="digest-hhmm"),
    ],
)
def test_setting_set_accepts_valid_values(key: str, value: str) -> None:
    """The documented valid shapes for every setting key."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": key, "value": value}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert settings.get(key) == value


# --- phase-gate red-team regressions (A, B, G) ----------------------------------------


@pytest.mark.parametrize("huge", [2**63, -(2**63) - 1])
def test_member_add_rejects_telegram_id_beyond_int64(huge: int) -> None:
    """A telegram_id outside SQLite's signed 64-bit range fails cleanly, storing nothing."""
    members, checkins, settings, clock = _wire()

    result = member_add(
        {"name": "Huge", "timezone": "America/Guayaquil", "wake": "08:00", "telegram_id": huge},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "telegram_id" in result["summary"]
    assert result["cron_relay"] is None
    assert result["data"] == {}
    assert len(members.list()) == 0


@pytest.mark.parametrize("huge", [2**63, -(2**63) - 1])
def test_member_update_rejects_telegram_id_beyond_int64(huge: int) -> None:
    """An int64-overflow telegram_id fails cleanly and leaves the stored row untouched."""
    members, checkins, settings, clock = _wire()
    _seed_member(members)

    result = member_update(
        {"member_id": 1, "telegram_id": huge}, members, checkins, settings, clock
    )

    assert result["ok"] is False
    assert "telegram_id" in result["summary"]
    assert result["cron_relay"] is None
    member = members.get(1)
    assert member is not None
    assert member.telegram_id == 111


def test_member_add_rejects_already_registered_telegram_id() -> None:
    """A telegram_id held by another member fails cleanly instead of a raw IntegrityError."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, telegram_id=111)

    result = member_add(
        {"name": "Bob", "timezone": "America/Guayaquil", "wake": "08:00", "telegram_id": 111},
        members,
        checkins,
        settings,
        clock,
    )

    assert result["ok"] is False
    assert "telegram_id 111 already registered" in result["summary"]
    assert result["cron_relay"] is None
    assert result["data"] == {}
    assert len(members.list()) == 1  # only the seeded row exists


def test_member_update_rejects_telegram_id_held_by_another_member() -> None:
    """Stealing another member's telegram_id fails cleanly; both rows keep their own ids."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, telegram_id=111)  # id 1
    _seed_member(members, name="Bob", telegram_id=222)  # id 2

    result = member_update({"member_id": 2, "telegram_id": 111}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert "telegram_id 111 already registered" in result["summary"]
    assert result["cron_relay"] is None
    alice = members.get(1)
    bob = members.get(2)
    assert alice is not None and alice.telegram_id == 111
    assert bob is not None and bob.telegram_id == 222


def test_member_update_accepts_members_own_telegram_id() -> None:
    """Re-sending the member's stored telegram_id is not a conflict (plain no-op update)."""
    members, checkins, settings, clock = _wire()
    _seed_member(members, telegram_id=111)

    result = member_update({"member_id": 1, "telegram_id": 111}, members, checkins, settings, clock)

    assert result["ok"] is True
    assert result["cron_relay"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("nudge_limit", "\uff11\uff12", id="nudge-fullwidth"),
        pytest.param("nudge_limit", "\u0661\u0662", id="nudge-arabic-indic"),
        pytest.param("digest_chat", "\uff11\uff12", id="chat-fullwidth"),
        pytest.param("digest_chat", "\u0661\u0662", id="chat-arabic-indic"),
    ],
)
def test_setting_set_rejects_non_ascii_digits(key: str, value: str) -> None:
    """Numeric settings accept ASCII digits only; Unicode Nd digits fail with nothing stored."""
    members, checkins, settings, clock = _wire()

    result = setting_set({"key": key, "value": value}, members, checkins, settings, clock)

    assert result["ok"] is False
    assert key in result["summary"]
    assert result["cron_relay"] is None
    assert settings.get(key) == DEFAULTS[key]
