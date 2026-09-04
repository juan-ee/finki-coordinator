"""Repository contracts: member CRUD, check-in latest-wins upsert, settings defaults."""

import pathlib
import sqlite3
from collections.abc import Iterator

import pytest

from coordinator.db import connect, migrate
from coordinator.repositories import (
    DEFAULTS,
    Checkin,
    CheckinsRepo,
    Member,
    MembersRepo,
    SettingsRepo,
)

FIXED_APPLIED_AT = "2026-01-01T00:00:00+00:00"
FIXED_CREATED_AT = "2026-02-01T09:00:00+00:00"
FIXED_UPDATED_AT = "2026-02-01T12:00:00+00:00"


@pytest.fixture()
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """Yield a migrated tmp_path database connection, closed after the test."""
    c = connect(tmp_path / "hermes-coord.db")
    migrate(c, applied_at=FIXED_APPLIED_AT)
    yield c
    c.close()


def _repos(conn: sqlite3.Connection) -> tuple[MembersRepo, CheckinsRepo, SettingsRepo]:
    """Build the three concrete repositories over one open connection."""
    return MembersRepo(conn), CheckinsRepo(conn), SettingsRepo(conn)


def test_member_delete_removes_member_and_checkins(conn: sqlite3.Connection) -> None:
    """delete cascades the member's check-ins then removes the row; True when removed."""
    members, checkins, _ = _repos(conn)
    added = members.add(
        name="Alice",
        telegram_id=101,
        timezone="America/Guayaquil",
        wake="08:00",
        role=None,
        status_days=None,
        active=1,
        created_at=FIXED_CREATED_AT,
    )
    checkins.submit(
        member_id=added.id,
        date="2026-02-01",
        done="d",
        next="n",
        blockers=None,
        source="auto",
        created_at=FIXED_CREATED_AT,
    )

    removed = members.delete(added.id)

    assert removed is True
    assert members.get(added.id) is None
    assert checkins.by_date("2026-02-01") == []


def test_member_delete_unknown_id_returns_false(conn: sqlite3.Connection) -> None:
    """delete on an absent id is a False no-op, not an error."""
    members, _, _ = _repos(conn)

    assert members.delete(4242) is False


def test_member_delete_failure_rolls_back_the_whole_cascade(
    conn: sqlite3.Connection,
) -> None:
    """A members-DELETE failure (trigger RAISE(ABORT)) rolls the cascade back entirely.

    The checkins DELETE that already ran must not survive as a pending change that a
    later unrelated commit on the shared connection could persist: after the failed
    delete, both the member row and its check-ins are still there.
    """
    members, checkins, _ = _repos(conn)
    added = members.add(name="Alice", created_at=FIXED_CREATED_AT)
    checkins.submit(member_id=added.id, date="2026-02-01", done="d", created_at=FIXED_CREATED_AT)
    conn.execute(
        "CREATE TRIGGER refuse_member_delete BEFORE DELETE ON members"
        " BEGIN SELECT RAISE(ABORT, 'member delete refused'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.Error):
        members.delete(added.id)
    conn.commit()  # a later, unrelated commit must not persist the aborted cascade

    assert members.get(added.id) is not None
    assert len(checkins.by_date("2026-02-01")) == 1


def test_member_add_then_get_round_trips_every_column(conn: sqlite3.Connection) -> None:
    """add stores every column and get returns an equal Member carrying a fresh id."""
    members, _, _ = _repos(conn)

    added = members.add(
        name="Alice",
        telegram_id=101,
        timezone="America/Guayaquil",
        wake="08:00",
        role="maintainer",
        status_days='["mon","wed","fri"]',
        active=1,
        created_at=FIXED_CREATED_AT,
    )

    fetched = members.get(added.id)
    assert fetched is not None
    assert fetched == added
    assert added.name == "Alice"
    assert added.telegram_id == 101
    assert added.timezone == "America/Guayaquil"
    assert added.wake == "08:00"
    assert added.role == "maintainer"
    assert added.status_days == '["mon","wed","fri"]'
    assert added.active == 1
    assert added.created_at == FIXED_CREATED_AT
    assert added.updated_at == FIXED_CREATED_AT


def test_member_add_applies_schema_column_defaults(conn: sqlite3.Connection) -> None:
    """add with only name and created_at lands the schema defaults (UTC, active=1, NULLs)."""
    members, _, _ = _repos(conn)

    added = members.add(name="Zed", created_at=FIXED_CREATED_AT)

    assert members.get(added.id) == Member(
        id=added.id,
        name="Zed",
        telegram_id=None,
        timezone="UTC",
        wake=None,
        role=None,
        status_days=None,
        active=1,
        created_at=FIXED_CREATED_AT,
        updated_at=FIXED_CREATED_AT,
    )


def test_member_update_rewrites_any_column_and_stamps_updated_at(
    conn: sqlite3.Connection,
) -> None:
    """update rewrites every supplied column (incl. active) and stamps the caller's updated_at."""
    members, _, _ = _repos(conn)
    added = members.add(name="Alice", created_at=FIXED_CREATED_AT)

    updated = members.update(
        added.id,
        name="Alicia",
        telegram_id=999,
        timezone="Europe/Berlin",
        wake="07:00",
        role="ops",
        status_days='["tue"]',
        active=0,
        updated_at=FIXED_UPDATED_AT,
    )

    assert updated is not None
    assert updated == Member(
        id=added.id,
        name="Alicia",
        telegram_id=999,
        timezone="Europe/Berlin",
        wake="07:00",
        role="ops",
        status_days='["tue"]',
        active=0,
        created_at=FIXED_CREATED_AT,
        updated_at=FIXED_UPDATED_AT,
    )
    assert members.get(added.id) == updated


def test_member_update_returns_none_for_unknown_id(conn: sqlite3.Connection) -> None:
    """update on a missing member id returns None instead of failing or inventing a row."""
    members, _, _ = _repos(conn)

    assert members.update(999, updated_at=FIXED_UPDATED_AT) is None


def test_member_list_orders_by_name_and_applies_the_active_filter(
    conn: sqlite3.Connection,
) -> None:
    """list orders by name; the active filter hides inactive rows, but get still returns them."""
    members, _, _ = _repos(conn)
    alice = members.add(name="Alice", created_at=FIXED_CREATED_AT)
    members.add(name="Carol", created_at=FIXED_CREATED_AT)
    members.add(name="Bob", created_at=FIXED_CREATED_AT)
    members.update(alice.id, active=0, updated_at=FIXED_UPDATED_AT)

    assert [member.name for member in members.list(active=1)] == ["Bob", "Carol"]
    assert [member.name for member in members.list(active=0)] == ["Alice"]
    assert [member.name for member in members.list()] == ["Alice", "Bob", "Carol"]

    inactive = members.get(alice.id)
    assert inactive is not None
    assert inactive.active == 0


def test_checkin_submit_round_trips_via_by_date(conn: sqlite3.Connection) -> None:
    """submit stores the row and by_date reads them back ordered by member id."""
    members, checkins, _ = _repos(conn)
    alice = members.add(name="Alice", created_at=FIXED_CREATED_AT)
    bob = members.add(name="Bob", created_at=FIXED_CREATED_AT)

    first = checkins.submit(
        member_id=alice.id,
        date="2026-02-02",
        done="shipped T0.5",
        next="start T0.6",
        blockers="waiting on review",
        source="manual",
        created_at=FIXED_CREATED_AT,
    )
    second = checkins.submit(
        member_id=bob.id, date="2026-02-02", done="hired", created_at=FIXED_CREATED_AT
    )

    assert checkins.by_date("2026-02-02") == [
        Checkin(
            id=first.id,
            member_id=alice.id,
            date="2026-02-02",
            done="shipped T0.5",
            next="start T0.6",
            blockers="waiting on review",
            source="manual",
            created_at=FIXED_CREATED_AT,
        ),
        Checkin(
            id=second.id,
            member_id=bob.id,
            date="2026-02-02",
            done="hired",
            next=None,
            blockers=None,
            source="auto",
            created_at=FIXED_CREATED_AT,
        ),
    ]


def test_second_submit_same_member_and_date_replaces_the_row(
    conn: sqlite3.Connection,
) -> None:
    """submit is latest-wins: the second call replaces every value and leaves exactly one row."""
    members, checkins, _ = _repos(conn)
    alice = members.add(name="Alice", created_at=FIXED_CREATED_AT)
    checkins.submit(
        member_id=alice.id,
        date="2026-02-02",
        done="first",
        next="old next",
        blockers="old blockers",
        source="auto",
        created_at="2026-02-02T09:00:00+00:00",
    )

    latest = checkins.submit(
        member_id=alice.id,
        date="2026-02-02",
        done="second",
        next="new next",
        blockers="new blockers",
        source="manual",
        created_at="2026-02-02T18:00:00+00:00",
    )

    rows = checkins.by_date("2026-02-02")
    assert rows == [latest]
    assert latest.done == "second"
    assert latest.next == "new next"
    assert latest.blockers == "new blockers"
    assert latest.source == "manual"
    assert latest.created_at == "2026-02-02T18:00:00+00:00"


def test_checkin_submit_for_unknown_member_fails_on_foreign_keys(
    conn: sqlite3.Connection,
) -> None:
    """FK enforcement from T0.5 makes a check-in for a nonexistent member fail loudly."""
    _, checkins, _ = _repos(conn)

    with pytest.raises(sqlite3.IntegrityError):
        checkins.submit(member_id=999, date="2026-02-02", done="ghost", created_at=FIXED_CREATED_AT)


def test_settings_defaults_match_the_task_spec(conn: sqlite3.Connection) -> None:
    """DEFAULTS is exactly the module-level dict the T0.6 task block specifies."""
    assert DEFAULTS == {"digest_time": "18:00", "digest_chat": "dm", "nudge_limit": "2"}


def test_settings_get_returns_default_then_set_overrides(conn: sqlite3.Connection) -> None:
    """get falls back to DEFAULTS until set stores a value; the stored value then wins."""
    _, _, settings = _repos(conn)

    assert settings.get("digest_time") == "18:00"
    assert settings.get("digest_chat") == "dm"
    assert settings.get("nudge_limit") == "2"

    settings.set("digest_time", "17:30")

    assert settings.get("digest_time") == "17:30"
    assert settings.get("digest_chat") == "dm"


def test_settings_set_upserts_repeated_keys(conn: sqlite3.Connection) -> None:
    """set on an already-stored key overwrites it and get returns the latest value."""
    _, _, settings = _repos(conn)

    settings.set("nudge_limit", "3")
    settings.set("nudge_limit", "5")

    assert settings.get("nudge_limit") == "5"


def test_settings_get_unknown_key_raises_key_error(conn: sqlite3.Connection) -> None:
    """A key that is neither stored nor in DEFAULTS raises KeyError (no silent invention)."""
    _, _, settings = _repos(conn)

    with pytest.raises(KeyError):
        settings.get("nonexistent")
