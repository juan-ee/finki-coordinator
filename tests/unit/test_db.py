"""DB adapter contract: WAL pragmas after connect, versioned idempotent migrations, specced DDL."""

import pathlib
import sqlite3

import pytest

from coordinator.db import DatabaseError, connect, migrate

FIXED_APPLIED_AT = "2026-01-01T00:00:00+00:00"

EXPECTED_COLUMNS: dict[str, list[str]] = {
    "members": [
        "id",
        "name",
        "telegram_id",
        "timezone",
        "wake",
        "role",
        "status_days",
        "active",
        "created_at",
        "updated_at",
    ],
    "checkins": [
        "id",
        "member_id",
        "date",
        "done",
        "next",
        "blockers",
        "source",
        "created_at",
    ],
    "settings": ["key", "value"],
}


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of a table in declaration order."""
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]


def test_connect_configures_wal_busy_timeout_and_foreign_keys(tmp_path: pathlib.Path) -> None:
    """A file connection opens with WAL journaling, a 5s busy timeout, FK enforcement, Row rows."""
    db_path = tmp_path / "hermes-coord.db"

    conn = connect(db_path)

    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_connect_raises_when_wal_cannot_be_honored() -> None:
    """A store that cannot do WAL (in-memory) fails loudly instead of silently degrading."""
    with pytest.raises(DatabaseError, match="journal_mode"):
        connect(":memory:")


def test_migrate_creates_specced_tables_and_columns(tmp_path: pathlib.Path) -> None:
    """Migration 1 creates members, checkins and settings with the proposal §1 columns."""
    conn = connect(tmp_path / "hermes-coord.db")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"members", "checkins", "settings", "schema_migrations"} <= tables
        for table, expected in EXPECTED_COLUMNS.items():
            assert _column_names(conn, table) == expected
    finally:
        conn.close()


def test_migrate_records_version_1_with_the_passed_timestamp(tmp_path: pathlib.Path) -> None:
    """The applied version is recorded in schema_migrations with the caller's timestamp."""
    conn = connect(tmp_path / "hermes-coord.db")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        rows = list(conn.execute("SELECT version, applied_at FROM schema_migrations"))
        assert [(row["version"], row["applied_at"]) for row in rows] == [(1, FIXED_APPLIED_AT)]
    finally:
        conn.close()


def test_migrate_is_idempotent_on_double_call(tmp_path: pathlib.Path) -> None:
    """Calling migrate twice neither raises nor adds versions or rewrites the timestamp."""
    conn = connect(tmp_path / "hermes-coord.db")
    migrate(conn, applied_at=FIXED_APPLIED_AT)

    migrate(conn, applied_at="2026-01-02T00:00:00+00:00")

    try:
        rows = list(conn.execute("SELECT version, applied_at FROM schema_migrations"))
        assert [(row["version"], row["applied_at"]) for row in rows] == [(1, FIXED_APPLIED_AT)]
    finally:
        conn.close()


def test_checkins_unique_constraint_on_member_and_date(tmp_path: pathlib.Path) -> None:
    """UNIQUE(member_id, date) rejects a repeated pair but allows other member/date combos."""
    conn = connect(tmp_path / "hermes-coord.db")
    migrate(conn, applied_at=FIXED_APPLIED_AT)
    conn.execute("INSERT INTO members (id, name) VALUES (1, 'Alice')")
    conn.execute("INSERT INTO members (id, name) VALUES (2, 'Bob')")
    conn.execute("INSERT INTO checkins (member_id, date) VALUES (1, '2026-02-01')")

    conn.execute("INSERT INTO checkins (member_id, date) VALUES (2, '2026-02-01')")
    conn.execute("INSERT INTO checkins (member_id, date) VALUES (1, '2026-02-02')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO checkins (member_id, date) VALUES (1, '2026-02-01')")

    conn.close()
