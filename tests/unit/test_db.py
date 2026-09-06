"""DB adapter contract: WAL pragmas after connect, versioned idempotent migrations, specced DDL."""

import pathlib
import sqlite3
import threading

import pytest

from coordinator.db import DatabaseError, connect, migrate
from coordinator.repositories import SettingsRepo

FIXED_APPLIED_AT = "2026-01-01T00:00:00+00:00"

EXPECTED_COLUMNS: dict[str, list[str]] = {
    "members": [
        "id",
        "name",
        "telegram_id",
        "timezone",
        "wake",
        "role",
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


def test_migrate_records_versions_with_the_passed_timestamp(tmp_path: pathlib.Path) -> None:
    """Every applied version is recorded in schema_migrations with the caller's timestamp."""
    conn = connect(tmp_path / "hermes-coord.db")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        rows = list(conn.execute("SELECT version, applied_at FROM schema_migrations"))
        assert [(row["version"], row["applied_at"]) for row in rows] == [
            (1, FIXED_APPLIED_AT),
            (2, FIXED_APPLIED_AT),
            (4, FIXED_APPLIED_AT),
        ]
    finally:
        conn.close()


def test_migrate_is_idempotent_on_double_call(tmp_path: pathlib.Path) -> None:
    """Calling migrate twice neither raises nor adds versions or rewrites the timestamp."""
    conn = connect(tmp_path / "hermes-coord.db")
    migrate(conn, applied_at=FIXED_APPLIED_AT)

    migrate(conn, applied_at="2026-01-02T00:00:00+00:00")

    try:
        rows = list(conn.execute("SELECT version, applied_at FROM schema_migrations"))
        assert [(row["version"], row["applied_at"]) for row in rows] == [
            (1, FIXED_APPLIED_AT),
            (2, FIXED_APPLIED_AT),
            (4, FIXED_APPLIED_AT),
        ]
    finally:
        conn.close()


# --- migration 002: the v5 -> v6 status_days drop (D4) ----------------------------------


def _create_v5_schema(conn: sqlite3.Connection, applied_at: str) -> None:
    """Create the v5-shaped schema (members WITH status_days) and mark version 1 applied."""
    conn.executescript(
        f"""
        CREATE TABLE members (
          id          INTEGER PRIMARY KEY,
          name        TEXT NOT NULL,
          telegram_id INTEGER UNIQUE,
          timezone    TEXT NOT NULL DEFAULT 'UTC',
          wake        TEXT,
          role        TEXT,
          status_days TEXT,
          active      INTEGER DEFAULT 1,
          created_at  TEXT, updated_at TEXT
        );
        CREATE TABLE checkins (
          id         INTEGER PRIMARY KEY,
          member_id  INTEGER REFERENCES members(id),
          date       TEXT NOT NULL,
          done       TEXT, next TEXT, blockers TEXT,
          source     TEXT DEFAULT 'auto',
          created_at TEXT,
          UNIQUE(member_id, date)
        );
        CREATE TABLE settings (
          key   TEXT PRIMARY KEY,
          value TEXT
        );
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
        INSERT INTO schema_migrations (version, applied_at) VALUES (1, '{applied_at}');
        """
    )
    conn.commit()


def test_migration_002_drops_status_days_on_a_v5_shaped_db(tmp_path: pathlib.Path) -> None:
    """Upgrade path: a v5 store loses status_days; its data rows survive the drop."""
    conn = connect(tmp_path / "upgrade.db")
    _create_v5_schema(conn, FIXED_APPLIED_AT)
    conn.execute("INSERT INTO members (id, name, status_days) VALUES (1, 'Alice', 'mon')")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        columns = _column_names(conn, "members")
        assert "status_days" not in columns
        row = conn.execute("SELECT id, name FROM members WHERE id = 1").fetchone()
        assert row is not None and row["name"] == "Alice"
    finally:
        conn.close()


def test_migration_002_purges_stored_digest_time_setting(tmp_path: pathlib.Path) -> None:
    """Upgrade path: a stored digest_time row is deleted, so the dropped dial stays dead."""
    conn = connect(tmp_path / "upgrade.db")
    _create_v5_schema(conn, FIXED_APPLIED_AT)
    conn.execute("INSERT INTO settings (key, value) VALUES ('digest_time', '17:30')")
    conn.commit()

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        # The dial must stay dead on upgraded stores too: with the row gone and the
        # key out of DEFAULTS, the repo get raises KeyError (setting_get rejects it).
        with pytest.raises(KeyError):
            SettingsRepo(conn).get("digest_time")
    finally:
        conn.close()


def test_migration_002_is_a_noop_on_a_fresh_v6_schema(tmp_path: pathlib.Path) -> None:
    """Fresh path: migration 001 never created status_days, so 002 must not raise."""
    conn = connect(tmp_path / "fresh.db")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        assert "status_days" not in _column_names(conn, "members")
    finally:
        conn.close()


def test_fresh_and_upgrade_paths_converge(tmp_path: pathlib.Path) -> None:
    """A v5 store upgraded in place ends with the same schema as a fresh v6 store."""
    upgraded = connect(tmp_path / "upgrade.db")
    fresh = connect(tmp_path / "fresh.db")
    _create_v5_schema(upgraded, FIXED_APPLIED_AT)

    migrate(upgraded, applied_at=FIXED_APPLIED_AT)
    migrate(fresh, applied_at=FIXED_APPLIED_AT)

    try:
        # Effective-schema convergence: the columns, as the engine sees them, are the
        # contract. A full sqlite_master.sql text comparison would be brittle here —
        # the v5 fixture's stored DDL differs from schema.sql's in comments and
        # whitespace. (ALTER TABLE DROP COLUMN itself does rewrite the stored text,
        # so the upgrade path's members DDL is pinned below to carry no status_days.)
        for table in ("members", "checkins", "settings"):
            assert _column_names(upgraded, table) == _column_names(fresh, table)
        # v7 (T2.28): the knowledge cache is gone from BOTH paths — fresh stores never
        # create it (001 no longer carries the DDL) and v6.1 stores shed it in 004.
        for store in (upgraded, fresh):
            names = {
                str(row["name"])
                for row in store.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            assert "knowledge" not in names
            assert "knowledge_fts" not in names
        upgraded_tables = {
            str(row["name"])
            for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        fresh_tables = {
            str(row["name"])
            for row in fresh.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert upgraded_tables == fresh_tables
        # ALTER TABLE DROP COLUMN rewrites the stored CREATE TABLE text: the upgraded
        # store's members DDL must not even mention the dropped column.
        upgraded_members_ddl = str(
            upgraded.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'members'"
            ).fetchone()["sql"]
        )
        assert "status_days" not in upgraded_members_ddl
    finally:
        upgraded.close()
        fresh.close()


# --- migration 004: the v6.1 -> v7 knowledge-cache drop (T2.28, proposal §12) ------------


def _create_v61_shaped_schema(conn: sqlite3.Connection, applied_at: str) -> None:
    """Create the v6.1-shaped store (knowledge cache + FTS5, versions 1-3 applied)."""
    conn.executescript(
        f"""
        CREATE TABLE members (
          id          INTEGER PRIMARY KEY,
          name        TEXT NOT NULL,
          telegram_id INTEGER UNIQUE,
          timezone    TEXT NOT NULL DEFAULT 'UTC',
          wake        TEXT,
          role        TEXT,
          active      INTEGER DEFAULT 1,
          created_at  TEXT, updated_at TEXT
        );
        CREATE TABLE checkins (
          id         INTEGER PRIMARY KEY,
          member_id  INTEGER REFERENCES members(id),
          date       TEXT NOT NULL,
          done       TEXT, next TEXT, blockers TEXT,
          source     TEXT DEFAULT 'auto',
          created_at TEXT,
          UNIQUE(member_id, date)
        );
        CREATE TABLE settings (
          key   TEXT PRIMARY KEY,
          value TEXT
        );
        CREATE TABLE knowledge (
          chunk_id       INTEGER PRIMARY KEY,
          file_id        TEXT NOT NULL,
          path           TEXT NOT NULL,
          title          TEXT NOT NULL,
          heading        TEXT,
          body           TEXT NOT NULL,
          modified_time  TEXT NOT NULL,
          fetched_at     TEXT NOT NULL,
          UNIQUE(file_id, heading)
        );
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
          title, body,
          content='knowledge',
          content_rowid='chunk_id',
          tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
        INSERT INTO schema_migrations (version, applied_at) VALUES
          (1, '{applied_at}'), (2, '{applied_at}'), (3, '{applied_at}');
        """
    )
    conn.commit()


def test_migration_004_drops_the_v61_knowledge_cache(tmp_path: pathlib.Path) -> None:
    """Upgrade path: a v6.1 store loses the knowledge cache + FTS index and the
    freshness stamp row; roster rows survive (v7 teardown — the cache is not the
    record, the Pi-local docs/ folder is)."""
    conn = connect(tmp_path / "upgrade61.db")
    _create_v61_shaped_schema(conn, FIXED_APPLIED_AT)
    conn.execute("INSERT INTO members (id, name) VALUES (1, 'Alice')")
    conn.execute(
        "INSERT INTO knowledge (file_id, path, title, body, modified_time, fetched_at)"
        " VALUES ('f1', 'docs/x.md', 'X', 'body', '2026-09-05T00:00:00Z',"
        " '2026-09-05T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('knowledge_last_freshness_check',"
        " '2026-09-05T00:00:00+00:00')"
    )
    conn.commit()

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        names = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "knowledge" not in names
        assert "knowledge_fts" not in names
        row = conn.execute("SELECT name FROM members WHERE id = 1").fetchone()
        assert row is not None and row["name"] == "Alice"
        stamp = conn.execute(
            "SELECT value FROM settings WHERE key = 'knowledge_last_freshness_check'"
        ).fetchone()
        assert stamp is None
        versions = [
            int(r["version"]) for r in conn.execute("SELECT version FROM schema_migrations")
        ]
        assert versions == [1, 2, 3, 4]
    finally:
        conn.close()


def test_migration_004_is_a_noop_on_a_fresh_v7_schema(tmp_path: pathlib.Path) -> None:
    """Fresh path: 001 never created the knowledge tables, so 004 must not raise."""
    conn = connect(tmp_path / "fresh.db")

    migrate(conn, applied_at=FIXED_APPLIED_AT)

    try:
        names = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"members", "checkins", "settings"} <= names
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


# --- phase-gate red-team regressions (I, J, K) -----------------------------------------


def test_connect_to_unopenable_path_raises_database_error(tmp_path: pathlib.Path) -> None:
    """A directory cannot be a SQLite store: DatabaseError, never a raw OperationalError."""
    with pytest.raises(DatabaseError, match="SQLite"):
        connect(tmp_path)


def test_migrate_readonly_store_raises_database_error(tmp_path: pathlib.Path) -> None:
    """A read-only store file surfaces as DatabaseError, never a raw OperationalError."""
    target = tmp_path / "readonly.db"
    target.write_bytes(b"")
    target.chmod(0o444)
    try:
        with pytest.raises(DatabaseError):
            conn = connect(target)
            migrate(conn, applied_at=FIXED_APPLIED_AT)
    finally:
        target.chmod(0o644)


def test_migrate_conflicting_existing_schema_raises_database_error(
    tmp_path: pathlib.Path,
) -> None:
    """A pre-existing clashing members table makes migrate raise DatabaseError, not raw."""
    pre = sqlite3.connect(tmp_path / "conflict.db")
    pre.execute("CREATE TABLE members (id INTEGER)")
    pre.commit()
    pre.close()

    conn = connect(tmp_path / "conflict.db")

    with pytest.raises(DatabaseError, match="migration"):
        migrate(conn, applied_at=FIXED_APPLIED_AT)


def test_connection_is_usable_from_a_foreign_thread(tmp_path: pathlib.Path) -> None:
    """The runtime gateway calls handlers from worker threads (2026-09-01 gate).

    register(ctx) opens the connection during plugin discovery (one thread); upstream
    later invokes tool handlers from other threads. sqlite3's default check_same_thread
    guard raises ProgrammingError on that topology, so connect() must lift the guard —
    concurrent access stays safe via SQLite serialized mode + the busy_timeout pragma.
    """
    conn = connect(tmp_path / "cross-thread.db")
    migrate(conn, applied_at=FIXED_APPLIED_AT)
    errors: list[Exception] = []

    def foreign_thread_query() -> None:
        """Run one query from a non-creating thread, capturing any exception."""
        try:
            rows = conn.execute("SELECT count(*) FROM members").fetchall()
            assert len(rows) == 1 and rows[0][0] == 0
        except Exception as error:  # noqa: BLE001 — forwarded to the main thread's assert
            errors.append(error)

    worker = threading.Thread(target=foreign_thread_query)
    worker.start()
    worker.join(timeout=10)

    assert not errors, f"foreign-thread use failed: {errors!r}"
